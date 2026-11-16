"""
main.py -- mingling 核心 Star（v4 多 Star 拆分入口）
===============================================================
关键契约：AstrBot 只识别**入口模块 main.py** 的 @filter handler（按模块路径
精确匹配），兄弟子模块里的 handler 不会被绑定。因此所有 @filter 处理器
（指令 / 被动收集 / LLM 工具 / 消息增强）都已内联到本文件的 MingbuPlugin 类。

架构：
  - core.py        : MingbuCore 单例（持有所有引擎）+ MingbuStar 基类（极薄）
  - main.py        : MingbuPlugin（核心 Star：生命周期 + 全部 @filter handler）
  - star_mixins/   : MingbuStar 的共享助手方法（按职责分 mixin）
  - stars/admin.py : StarAdmin（后台管理面板 Web API，唯一保留的子 Star，
                     经继承链接入，且其 initialize 由入口 Star 手动触发）

依赖后端：Mingbu Next.js 服务（默认 http://localhost:2963）
部署方式：将本目录放入 AstrBot 的 data/plugins/ 下，启用插件。
"""

import asyncio
import inspect
import json
import os
import random
import re
import sys
import time
import traceback
from typing import Any, Dict, List, Optional

# 兼容 verify_hooks.py 等独立脚本直接运行时的绝对导入
_PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))
if _PLUGIN_DIR not in sys.path:
    sys.path.insert(0, _PLUGIN_DIR)

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.provider import ProviderRequest

try:
    from astrbot.api.message_components import Record
except Exception:
    Record = None

try:
    from astrbot.api.message_components import Plain
except Exception:
    Plain = None

try:
    from astrbot.api.event import MessageChain
except Exception:
    MessageChain = None

# 尝试导入 LLMResponse，不同 AstrBot 版本可能路径不同
try:
    from astrbot.api.provider import LLMResponse
except Exception:
    try:
        from astrbot.core.provider import LLMResponse  # type: ignore[no-redef]
    except Exception:
        LLMResponse = None

from .astrology import current_da_yun_liu_nian, extract_key_palaces
from .bj_time import CRON_TZ, now_bj
from .character_pool import HIDDEN_PRESET_ID, VISIBLE_PRESETS
from .core import BIRTH_YEAR_RE, EMOTION_KEYWORD_PATTERN, ZODIAC_RE
from .counseling import (
    CounselingPrompts,
    CounselingSession,
    counseling_turn_prompt,
)
from .emotion_detect import detect_emotion, emotion_to_weight
from .formatters import (
    _clock_to_shichen,
    _gender_label,
    _safe_int,
    _shichen_name,
    format_bazi,
    format_health,
    format_liuyao,
    format_qimen,
    format_signals,
    format_tarot,
    format_ziwei,
    format_zizhan,
)
from .health import generate_health_plan
from .stars.admin import StarAdmin  # noqa: F401
from .tarot import draw_spread, spread_for_question
from .technique_kb import (
    build_bazi_knowledge_grounding,
    build_liuyao_knowledge_grounding,
    build_qimen_knowledge_grounding,
)
from .tts_config import (
    DEFAULT_TTS_LONG_TEXT_THRESHOLD as TTS_LONG_TEXT_THRESHOLD,
)
from .tts_config import (
    TTS_MAX_CHUNK_CHARS,
    load_tts_settings,
)
from .util import cleanup_stale_cron_jobs, msg_type_is, spawn
from .ziwei_kb import build_ziwei_knowledge_grounding

# ---------------------------------------------------------------------------
# 体验增强的模块级辅助（原 stars/enhancements.py，迁入入口模块使 handler 可被识别）
# ---------------------------------------------------------------------------

# 自动 TTS 触发策略：长文本必触发；强情绪语义触发；其余按固定概率随机触发
TTS_RANDOM_RATE = 0.0             # 短文本不再随机触发语音（需求：短→文字，长→语音）
TTS_MIN_RANDOM_LEN = 20            # 随机触发的最短文本长度，避免朗读单字/短句
TTS_SEMANTIC_MIN_LEN = 10          # 情绪语义触发的最短文本长度（安慰/开心等短回复也读）
TTS_SEMANTIC_EMOTION_WEIGHT = 0.5  # 情绪权重阈值：|weight|>=0.5(joy/sad/excited/angry)才语义触发
# TTS_LONG_TEXT_THRESHOLD / TTS_MAX_CHUNK_CHARS 已上移 tts_config.py（v4.9.0），
# 经顶部 import 以同名提供（tests 依赖 from main import 这两个名字）。

# 「没听清」转文字：用户表达没听清/听不清/转文字时，重发上一条回复的文字版（不再调用 LLM）
TTS_TRANSCRIBE_HINT_RE = re.compile(
    r"(没听清|听不清|没听到|没听见|听不见|转文字|文字版|发文字|看文字|打出来|没听懂|用文字|文字形式|再说一遍.*文字)"
)

# 用户点名语音（v4.8.128）：用户明确要求发语音时，无视长度/情绪/随机判定，本条
# 回复强制转语音。此前 TTS 只有「长文本/强情绪」两条自动路径（随机恒关），用户
# 嘴上要语音时模型常回短中性句、三条路径全不满足 → 只能收到文字（日志实证：
# 点名后 mingbu.tts 零触发，仅能力声明注入生效）。否定语境排除：「别发语音」
# 「不要语音」「转文字」不触发（转文字走 TTS_TRANSCRIBE_HINT_RE 旧路重发文字版）。
TTS_VOICE_REQUEST_RE = re.compile(
    r"(?:发|来|换|要|用|想听)[一-龥]{0,3}语音|语音(?:呢|回复|形式|说|聊)|说给我听|读给我听|念给我听"
)
TTS_VOICE_REQUEST_NEG_RE = re.compile(
    r"(?:不要|别|别再|不想|不准|禁止)[一-龥]{0,3}语音|转(?:成|为)?文字"
)


def _user_requests_voice(user_text: str) -> bool:
    """判断用户本条消息是否明确点名要语音（否定语境排除）。

    命中例：发语音 / 发条语音 / 发一段语音 / 来段语音 / 用语音说 / 想听语音 /
    换语音 / 要语音 / 语音呢 / 语音回复我 / 语音形式 / 说给我听 / 读给我听。
    排除例：别发语音 / 不要发语音 / 不想听语音 / 转文字 / 转成文字。
    """
    t = (user_text or "").strip()
    if not t:
        return False
    if TTS_VOICE_REQUEST_NEG_RE.search(t):
        return False
    return bool(TTS_VOICE_REQUEST_RE.search(t))

# v4.8.141：移除 DashScope 直连专属的「情绪→语气指令」注入链路。instructions
# 参数（仅 qwen3-tts-instruct-flash 系列支持）是插件直连 TTS 时的私有能力；
# 现在语音合成完全交由 AstrBot TTS provider（provider.get_audio），provider
# 不接收语气指令，故该注入链路整体下线。音色试听工具与运行时合成路径解耦。

# 入站消息去重 TTL（秒）：微信客服 webhook 在回调未及时 ack 时会重投同一条消息，
# 同 msgid 在 TTL 内直接丢弃，避免「被处理多次 / 重复回复」。
MSGID_DEDUP_TTL = 120
TTS_SEGMENT_GAP = 1.0              # 多段语音之间的发送间隔（秒），降低微信客服 95001 限流概率
TTS_SEND_MAX_RETRIES = 3           # 单条语音发送遇 95001 限流时的最大重试次数
TTS_SEND_BACKOFF_BASE = 1.5        # 95001 重试退避基数（秒），第 n 次等待 backoff_base * 2^(n-1)

# 机器腔残留模式（v4.8.107 输出侧兜底）：模型偶尔会把系统说明/内部标记漏进回复
# （如 "(TTS active)"「注：当前仍无法实际发送语音文件」「语音合成功能正常」），
# 提示词约束（v4.8.104 说话的底线）已尽力，这里再做输出侧正则清理——TTS 朗读前
# 与文字展示前均剥离，避免"机器腔被读出来"。
MACHINE_TALK_PATTERNS = [
    re.compile(r"\(TTS\s+active\)", re.IGNORECASE),
    re.compile(r"（TTS\s+active）", re.IGNORECASE),
    re.compile(r"[（(]?\s*注[:：].{0,40}?[)）]?", re.IGNORECASE),
    re.compile(r"【系统提示】.{0,80}?(?:。|！|\n|$)"),
    re.compile(r"系统(?:未|已|正在).{0,20}?(?:语音|TTS|合成|插件).{0,20}?(?:。|！|\n|$)", re.IGNORECASE),
    re.compile(r"(?:语音|TTS)(?:合成|通道|功能|模块).{0,20}?(?:正常|已激活|恢复|在线|工作).{0,10}?(?:。|！|\n|$)", re.IGNORECASE),
]


def _strip_machine_talk(text: str) -> str:
    """清理回复中的机器腔/系统说明残留（输出侧兜底，不依赖提示词）。

    返回清洗后文本；仅剥离上述模式，保留正常内容。任何模式匹配失败都
    静默跳过（清洗绝不能损坏正文）。
    """
    if not text:
        return ""
    out = text
    try:
        for pat in MACHINE_TALK_PATTERNS:
            out = pat.sub("", out)
    except Exception:  # noqa: BLE001
        pass
    # 清理可能留下的多余空白/括号
    out = re.sub(r"[（(]\s*[)）]", "", out)
    out = re.sub(r"\s{2,}", " ", out)
    return out.strip()


def _extract_birth_year_from_msg(message_str: str):
    """从用户消息中提取出生年份；无法确认出生语义时返回 None。

    第一道闸（filter 正则）已确认消息含"我…年"，但"分析我在2022-2035年的
    感情运势"这种**时间范围**也会命中——本函数做第二道语义判定：
    - 必须出现出生语义词：出生/生于/生日/生的/年生/属…年；
    - 排除范围语法：``-2035年``（负向后瞻）、``2035年之/以/前/后``；
    - 年份须在 1900~当前年之间（未来年份绝不可能是出生年）。
    """
    if not message_str:
        return None
    birth_hint = re.search(
        r"(?:出生|生于|生日|生的?|年生|属[鼠牛虎兔龙蛇马羊猴鸡狗猪])", message_str
    )
    if not birth_hint:
        return None
    m = BIRTH_YEAR_RE.search(message_str)
    if not m:
        return None
    year = int(m.group(1))
    if year < 1900 or year > now_bj().year:
        return None
    return year


def _split_tts_chunks(text: str, max_chars: int = TTS_MAX_CHUNK_CHARS) -> list:
    """把长文本按句子边界切分为多段，每段不超过 max_chars，避免单条语音超微信 60s 限制。

    优先在断句符（。！？\\n.!?）处切分；若某句超长（无断句符）则硬切。返回非空片段列表。
    """
    text = (text or "").strip()
    if not text:
        return []
    if len(text) <= max_chars:
        return [text]
    chunks: list = []
    buf = ""
    breaks = set("。！？\n.!?")
    for ch in text:
        buf += ch
        # 遇到断句符且已达 max_chars 的 60% 以上，在此切分（避免过碎）
        if ch in breaks and len(buf) >= max_chars * 0.6:
            chunks.append(buf)
            buf = ""
        elif len(buf) >= max_chars:
            chunks.append(buf)
            buf = ""
    if buf:
        chunks.append(buf)
    # 兜底：某段仍超长（无断句符的长串）硬切
    out: list = []
    for c in chunks:
        if len(c) <= max_chars:
            out.append(c)
        else:
            for i in range(0, len(c), max_chars):
                out.append(c[i:i + max_chars])
    return [c for c in out if c.strip()]


def _split_utf8_bytes(text: str, max_bytes: int) -> list:
    """按 UTF-8 字节数切分文本，每段不超过 max_bytes 字节（企业微信 2048 字节上限）。

    优先在断句符处切分（避免切碎语义）；无断句符则按字节边界硬切，绝不切断多字节字符。
    返回非空片段列表，拼接后可无损还原原文。
    """
    text = (text or "").strip()
    if not text:
        return []
    if len(text.encode("utf-8")) <= max_bytes:
        return [text]
    chunks: list = []
    buf = ""
    breaks = set("。！？\n.!?；;")
    for ch in text:
        buf += ch
        if len(buf.encode("utf-8")) > max_bytes:
            # 当前段超限：先尝试去掉最后一个字符（若为断句符）或直接回退
            if buf[-1] in breaks and len(buf[:-1].encode("utf-8")) > 0:
                # 断句符归属到下一段更合理时保留在本段末尾
                if len(buf[:-1].encode("utf-8")) <= max_bytes:
                    chunks.append(buf[:-1])
                    buf = ch
                    continue
            # 硬切：把超出的部分作为新段起点
            chunks.append(buf[:-1])
            buf = ch
            continue
        # 断句符 + 达到 max_bytes 的 60%：在此切分
        if ch in breaks and len(buf.encode("utf-8")) >= max_bytes * 0.6:
            chunks.append(buf)
            buf = ""
    if buf:
        chunks.append(buf)
    # 兜底：任何仍超长的段按字节硬切（防止无断句符长串漏网）
    out: list = []
    for c in chunks:
        if len(c.encode("utf-8")) <= max_bytes:
            out.append(c)
        else:
            cur = ""
            for ch in c:
                if len((cur + ch).encode("utf-8")) > max_bytes:
                    if cur:
                        out.append(cur)
                    cur = ch
                else:
                    cur += ch
            if cur:
                out.append(cur)
    return [c for c in out if c.strip()]


async def _send_voice_with_backoff(event, file_value: str,
                                    max_retries: int = TTS_SEND_MAX_RETRIES,
                                    backoff_base: float = TTS_SEND_BACKOFF_BASE):
    """发送一条语音 Record，遇到微信客服限流时指数退避重试。

    返回：
    - True：发送成功；
    - False：发送失败（非 45007）；
    - "too_long"：微信客服 45007（playtime out of limit，语音超 60s）——
      不是偶发错误，是文本过长，调用方应把该段文本再切小重试。
    """
    for attempt in range(1, max_retries + 1):
        try:
            await event.send(MessageChain([Record(file=file_value)]))
            return True
        except Exception as e:  # noqa: BLE001
            msg = str(e)
            if "45007" in msg or "playtime out of limit" in msg:
                logger.warning("[mingbu.tts] 语音超时长(45007)被微信客服拒绝，需重切更短")
                return "too_long"
            if attempt < max_retries and ("95001" in msg or "send msg count limit" in msg):
                wait = backoff_base * (2 ** (attempt - 1))
                logger.warning(
                    f"[mingbu.tts] 发送遇 95001 限流，第 {attempt} 次重试（等待 {wait:.1f}s）"
                )
                await asyncio.sleep(wait)
                continue
            logger.error(f"[mingbu.tts] 语音发送失败（已重试 {attempt} 次）：{e}")
            return False
    return False


def tts_should_trigger(plain: str, threshold: int = TTS_LONG_TEXT_THRESHOLD,
                       rate: float = TTS_RANDOM_RATE, rng=random.random) -> tuple:
    """判断本次回复是否触发 TTS。

    返回 (是否触发, 原因)。触发路径（按优先级）：
    1. 长文本(>=threshold)必然触发——原因"长文本"；
    2. 强情绪语义：回复带明显情绪（joy/excited/sad/angry，|权重|>=emotion_threshold）
       且长度达到 TTS_SEMANTIC_MIN_LEN ——原因"情绪语义"。安慰/开心/心疼这类
       「语气比内容重要」的短回复也应读出来，而不是一律只给文字；
    3. 其余回复在长度达到 TTS_MIN_RANDOM_LEN 后按 rate 概率随机触发（默认 0 关闭）。

    rng 可注入以便测试；emotion 检测使用插件自带 detect_emotion，不引入新依赖。
    """
    if len(plain) >= threshold:
        return True, "长文本"
    if len(plain) >= TTS_SEMANTIC_MIN_LEN:
        try:
            emo = detect_emotion(plain)
            w = abs(emotion_to_weight(emo))
            if w >= TTS_SEMANTIC_EMOTION_WEIGHT:
                return True, f"情绪语义({emo})"
        except Exception:  # noqa: BLE001 - 情绪判定失败不影响后续路径
            pass
    if len(plain) >= TTS_MIN_RANDOM_LEN and rng() < rate:
        return True, "随机"
    return False, ""


# 兼容不同 AstrBot 版本的 EventMessageType
# 旧版: FRIEND_MESSAGE / ALL
# v4.26+: PRIVATE_MESSAGE / GROUP_MESSAGE (无 ALL，不装饰即接收全部)
_EMT = getattr(filter, 'EventMessageType', None)
_EMT_PRIVATE = None
_EMT_ALL = None
if _EMT:
    for _name in ('PRIVATE_MESSAGE', 'FRIEND_MESSAGE'):
        _val = getattr(_EMT, _name, None)
        if _val is not None:
            _EMT_PRIVATE = _val
            break
    # ALL 在旧版存在，新版不存在（不装饰 = 接收全部）
    _EMT_ALL = getattr(_EMT, 'ALL', None)


_ON_MESSAGE = getattr(filter, 'on_message', None)


def _message_handler(event_type=None):
    """装饰一个消息处理器，跨版本兼容地注册为 AdapterMessageEvent 入口。

    event_type: 目标消息类型（如 _EMT_PRIVATE / _EMT_ALL）；None 表示不限类型。
    旧版(含 on_message): on_message() 入口，可选叠加 event_message_type 条件过滤。
    新版(4.27+, 无 on_message): event_message_type(event_type 或 ALL) 作入口；
        EventMessageType 为 Flag，ALL = GROUP|PRIVATE|OTHER，可捕获全部消息类型。
    """
    def decorator(func):
        if _ON_MESSAGE is not None:
            func = _ON_MESSAGE()(func)
            if event_type is not None and hasattr(filter, 'event_message_type'):
                func = filter.event_message_type(event_type)(func)
            return func
        et = event_type if event_type is not None else _EMT_ALL
        if et is not None and hasattr(filter, 'event_message_type'):
            return filter.event_message_type(et)(func)
        return func
    return decorator


class MingbuPlugin(StarAdmin):
    """核心 Star：生命周期 + 全部 @filter handler（内联自 stars/* 子模块）。

    四层能力总览：
    - 被动收集：regex 监听出生信息 + 情绪信号（_on_birth_year 等）
    - LLM 工具：排盘/占卜/近况分析/自我探查（query_bazi 等 10 个）
    - 体验增强：TTS / 图片转述 / 合并转发 / 撤回 / 防抖 / 语音转写
    - 朋友式关心：钩子关心 + 神秘鼓励（_on_llm_request）
    - 暗盘外显：静默排盘的洞察伪装成直觉；重大预感以梦境偷偷告知
    - 指令入口：/排盘 /卜卦 /心情 /开解 /我的命盘 /设置命盘（cmd_*）
    - 后台管理面板 Web API（StarAdmin，经继承链接入）
    """

    def __init__(self, context, config=None):
        super().__init__(context, config)
        # 消息防抖：uid -> {"texts": [...], "ids": [...], "event": ..., "timer": ...}
        self._debounce_buffer: Dict[str, dict] = {}
        # 待发送的回复：message_id -> event，用于撤回时取消
        self._pending_replies: Dict[str, dict] = {}
        # 入站消息去重：msgid -> 上次处理时间戳（微信客服 webhook 重投防护）
        self._seen_msgids: Dict[str, float] = {}
        # 占卜工具防重：同用户同问题在 TTL 内重复调用直接返回上次结果，
        # 防止 LLM 工具循环里反复起卦（每次随机结果不同，会造成卦象混乱）
        self._divination_recent: Dict[tuple, tuple] = {}

    async def initialize(self):
        """插件加载完成后初始化内核并注册后台任务。"""
        # 内核初始化（幂等；首个初始化的 Star 完成真实创建）
        await self.core.ensure(self)

        # 注册后台周期任务（cron_manager，失败转 asyncio）
        await self._register_cron()

        # 主动对话引擎（自带调度）
        if self.proactive:
            await self.proactive.register_jobs()

        # 后台管理面板 Web API（StarAdmin 在部分 AstrBot 版本下不被自动初始化，
        # 故统一在主 Star 注册；_register_web_apis 带 context 级幂等守卫，避免重复）
        self._register_web_apis()

    async def _register_cron(self):
        cron = getattr(self.context, "cron_manager", None)
        if cron is None:
            logger.warning("[mingbu] 未找到 cron_manager，主动/梦境/成长任务不可用（需手动触发）")
            return

        # 引擎级任务按开关注册（2026-09-22 P0）：引擎未启用（如
        # dream_engine_enabled=false → core.dream=None）时不注册对应任务，
        # 并清理历史遗留的同名 persistent job，避免每晚空转与告警刷屏。
        jobs = [
            ("mingbu_tick", "*/5 * * * *", self._persona_tick, "人格自然波动"),
            ("mingbu_decay", "0 4 * * *", self._decay_memories, "记忆自然衰减"),
            ("mingbu_emotion_reset", "0 0 * * *", self._reset_emotion_daily, "情绪追踪每日重置"),
            ("mingbu_news", "0 9,15 * * *", self._read_news, "新闻阅读"),
            ("mingbu_search", "0 14 * * 1,3,5", self._proactive_search, "主动搜索"),
        ]
        if self.dream is not None:
            jobs.append(("mingbu_dream", "0 3 * * *", self._nightly_dreams, "夜间梦境巩固"))
        else:
            await cleanup_stale_cron_jobs(cron, "mingbu_dream", "")
        if self.growth is not None:
            jobs.append(("mingbu_growth", "0 2 * * *", self._reflect_all, "成长反思"))
        else:
            await cleanup_stale_cron_jobs(cron, "mingbu_growth", "")
        for name, cron_expr, handler, desc in jobs:
            try:
                job = await cron.add_basic_job(
                    name=name,
                    cron_expression=cron_expr,
                    # nm 绑定：name 是循环变量，lambda 触发时引用会漂移到最后一个值
                    handler=lambda h=handler, nm=name: spawn(h(), name=nm),
                    description=f"神秘朋友·{desc}",
                    persistent=True,
                    # 显式绑北京时区：不传则 AstrBot 回退「调度器默认 = 进程本地时区」，
                    # 生产容器原先为 UTC → dream/growth/decay 等夜间任务整体 +8h 跑到白天。
                    timezone=CRON_TZ,
                )
                await cleanup_stale_cron_jobs(cron, name, job.job_id)
            except Exception as e:
                logger.warning(f"[mingbu] cron 注册 {name} 失败: {e}")
        logger.info("[mingbu] 已注册 cron 周期任务（tick/dream/growth/decay/emotion_reset/news/search）")

    async def _persona_tick(self):
        if self.persona:
            self.persona.tick(5)
            await self._save_persona()

    async def _nightly_dreams(self):
        # 用户间串行延迟（错峰）：避免夜间批量任务瞬时打满 LLM 配额（如 Gemini 429）
        # 引擎未启用（dream_engine_enabled=false）时 core.dream 为 None，
        # 但本任务仍被 cron 注册 → 裸调用会刷出
        # "'NoneType' object has no attribute 'nightly_consolidate'"（2026-09-19~22 生产 96 条）。
        if not self.dream:
            logger.info("[mingbu] 梦境引擎未启用，跳过本次夜梦任务")
            return
        await self._run_batch_report(
            "dream", self._active_uids(),
            lambda uid: self.dream.nightly_consolidate(uid),
            label="夜梦",
        )

    async def _reflect_all(self):
        # 用户间串行延迟（错峰）：同上，避免成长反思批量调用触发配额限流
        # 同 _nightly_dreams：growth_engine_enabled=false 时 core.growth 为 None。
        if not self.growth:
            logger.info("[mingbu] 成长引擎未启用，跳过本次反思任务")
            return
        await self._run_batch_report(
            "growth", self._active_uids(),
            lambda uid: self.growth.reflect(uid),
            label="成长反思",
        )

    async def _run_batch_report(self, task: str, uids_await, call, label: str):
        """批量任务执行 + 单行摘要日志 + KV 报表（2026-09-22 P2 可观测）。

        summary: {date, task, total, ok, skip, fail}；ok=有产出，skip=无产出
        （无新素材/无记忆），fail=异常。落 KV mingbu.batch_report:<task>。
        """
        uids = await uids_await
        total = len(uids)
        summary: Dict[str, Any] = {
            "date": time.strftime("%Y-%m-%d"),
            "task": task,
            "total": total,
            "ok": 0,
            "skip": 0,
            "fail": 0,
        }
        for i, uid in enumerate(uids):
            try:
                r = await call(uid)
                # dict 结果带 skipped=True（触发闸跳过）计为 skip
                if r and not (isinstance(r, dict) and r.get("skipped")):
                    summary["ok"] += 1
                else:
                    summary["skip"] += 1
            except Exception as e:
                summary["fail"] += 1
                logger.warning(f"[mingbu] {label}失败 {uid}: {e}")
            if i < total - 1:
                await asyncio.sleep(random.uniform(4.0, 5.0))
        logger.info(f"[mingbu] {label}批处理完成：{summary}")
        try:
            await self.put_kv_data(
                f"mingbu.batch_report:{task}",
                json.dumps(summary, ensure_ascii=False),
            )
        except Exception as e:
            logger.debug(f"[mingbu] {label}报表写入失败(忽略): {e}")

    async def _decay_memories(self):
        """每日记忆衰减：让不重要/少访问的记忆自然消散。"""
        if not self.memory:
            return
        for uid in await self._active_uids():
            try:
                await self.memory.decay_all(uid, days=1.0)
            except Exception as e:
                logger.warning(f"[mingbu] 记忆衰减失败 {uid}: {e}")

    async def _reset_emotion_daily(self):
        """每日重置情绪追踪的祝福计数等日维状态。"""
        if self.tracker:
            try:
                self.tracker.reset_daily()
                logger.info("[mingbu] 情绪追踪日计数已重置")
            except Exception as e:
                logger.warning(f"[mingbu] 情绪日重置失败: {e}")

    async def _read_news(self):
        """定时阅读新闻。"""
        if self.news_search:
            try:
                result = await self.news_search.read_news()
                if result.get("ok"):
                    logger.info(f"[mingbu] 已阅读新闻: {result['note']['topic']}")
            except Exception as e:
                logger.warning(f"[mingbu] 新闻阅读失败: {e}")

    async def _proactive_search(self):
        """定时主动搜索。"""
        if self.news_search and self.persona:
            try:
                # 根据人格兴趣选一个主题
                interests = self.persona.interests or ["有趣的知识"]
                interest = random.choice(interests)
                result = await self.news_search.proactive_search(interest)
                if result.get("ok"):
                    logger.info(f"[mingbu] 主动搜索完成: {interest}")
            except Exception as e:
                logger.warning(f"[mingbu] 主动搜索失败: {e}")

    async def terminate(self):
        await self.core.shutdown()

    # ============================================================
    # LLM 请求钩子（唯一，转发到内核钩子主体）
    # ============================================================

    @filter.on_llm_request()
    async def _on_llm_request(self, event: AstrMessageEvent, req: ProviderRequest):
        """LLM 调用前统一注入钩子（朋友式关心 + 神秘鼓励 + 入站去重）。

        实际逻辑见 MingbuStar._on_llm_request_body（core.py）。
        仅本核心 Star 注册钩子，避免多 Star 重复触发。
        """
        # 微信客服 webhook 重投防护：同 msgid 在 TTL 内只放行一次，其余在 LLM 调用前短路
        if self._is_replay(event):
            event.stop_event()
            return

        # 「没听清」转文字：用户表达没听清/听不清时，重发上一条回复文字，不再调用 LLM
        try:
            mo = getattr(event, "message_obj", None)
            msg_type = getattr(mo, "type", None)
            is_private = msg_type_is(mo, "FriendMessage") or (msg_type is None)
            if is_private and Plain is not None:
                user_text = getattr(event, "message_str", "") or ""
                if TTS_TRANSCRIBE_HINT_RE.search(user_text):
                    uid = event.get_sender_id()
                    last = await self.get_kv_data(f"mingbu.last_bot_text:{uid}", None)
                    if last:
                        logger.info("[mingbu.tts] 用户表示没听清，转为文字重发上一条回复")
                        event.stop_event()
                        await event.send(MessageChain([Plain(last)]))
                        return
        except Exception as e:
            logger.debug(f"[mingbu] 没听清转文字拦截失败(忽略): {e}")
        await self._on_llm_request_body(event, req)

    @filter.on_llm_response()
    async def _on_llm_response(self, event: AstrMessageEvent, resp):
        """LLM 回复后：记录 Bot 对话 → 长文本转语音(并压住文本)。

        入站去重只在 `_on_llm_request` 请求钩子做（LLM 调用前短路）。
        注意：不能在此按 msgid 压空回复——微信语音重投被 AstrBot follow-up
        机制合并进同一 run 时，唯一且正常的响应挂在原始消息上，其 msgid 已
        被 request 阶段标记，若再兜底压空会把正常回复误判为重复而吞掉
        （生产日志 07:34:38 实证「兜底压空回复 + skipping respond stage」，
        用户收不到任何回复）。
        """
        # 1) 记录 Bot 的对话到检视系统
        uid = event.get_sender_id()
        text = getattr(resp, "completion_text", None) \
            or getattr(resp, "response_text", None) \
            or str(resp)
        if text and text.strip():
            if self.conversation_inspector:
                mo = getattr(event, "message_obj", None)
                gid = getattr(mo, "group_id", "") or ""
                s = getattr(event, "unified_msg_origin", None) \
                    or getattr(event, "session_id", None) or ""
                is_group = msg_type_is(mo, "GroupMessage")
                try:
                    await self.conversation_inspector.log_bot_reply(
                        uid=uid, content=text.strip(),
                        session_id=s, gid=gid, is_group=is_group
                    )
                    await self._append_turn(uid, "bot", text.strip())
                except Exception as e:
                    logger.debug(f"[mingbu] 记录 Bot 对话失败(忽略): {e}")
            # 存最近一条回复文本，供用户「没听清」时转文字重发
            try:
                await self.put_kv_data(f"mingbu.last_bot_text:{uid}", text.strip())
            except Exception as e:
                logger.debug(f"[mingbu] 缓存最近回复失败(忽略): {e}")

        # 2) TTS 语音增强：长文本(>=阈值)转语音；短文本不触发（短→文字）
        sent_voice = await self._tts_on_response(event, resp)
        if sent_voice:
            # 长文本已转语音，压住核心文本回复，避免「既给文本又给语音」
            try:
                resp.completion_text = ""
            except Exception:
                pass
            return

        # 3) 企微长文本分段兜底（v4.8.116）
        #    企业微信客服消息 content 上限 2048 **字节**（UTF-8），AstrBot 核心
        #    wecom split_plain 却按 2048 **字符** 切——中文每字 3 字节，
        #    2048 字符 ≈ 6144 字节，切出的每段都超限被微信拒收，用户收不到。
        #    这里在插件侧按 UTF-8 字节切段（每段 ≤1800 字节 < 2048 字节），
        #    每段字符数必然 < 2048，AstrBot 不再二次切分，整段发送不超限。
        #    仅对企微渠道生效（其他平台上限/行为不同，交给 AstrBot 默认处理）。
        if await self._maybe_split_long_text_for_wecom(event, resp):
            return

    # ============================================================
    # 命理咨询总闸（配置 counseling_enabled，v4.8.114 接线）
    # 关闭后：命理指令 / LLM 命理工具不再响应；心理/健康类不受影响。
    # ============================================================
    def _counseling_gate(self) -> str:
        """命理咨询未开启时的统一提示。开启则返回空串。"""
        if self._config.get("counseling_enabled", True):
            return ""
        return "\u26A0\uFE0F 命理咨询功能当前未开启，无法响应命理相关请求。"

    # ============================================================
    # 斜杠指令（原 stars/commands.py，内联迁入使 AstrBot 能识别绑定）
    # ============================================================

    @filter.command("排盘")
    async def cmd_paipan(self, event: AstrMessageEvent):
        gate_msg = self._counseling_gate()
        if gate_msg:
            yield event.plain_result(gate_msg)
            return
        uid = event.get_sender_id()
        profile = await self._get_profile(uid)
        birth = self._birth_from(profile)
        if birth:
            name = profile.get("name", "用户")
            si = _shichen_name(_safe_int(birth["hour"]))
            yield event.plain_result(
                f"\U0001F4CB {name} 的存档信息：\n"
                f"{birth['year']}年{birth['month']}月{birth['day']}日 "
                f"{si}时（{_gender_label(birth['gender'])}）\n\n"
                f"想了解什么？直接问我即可：\n"
                f"\u2022 「帮我排一下八字」\n"
                f"\u2022 「看看我的紫微命盘」\n"
                f"\u2022 「今年运势如何」\n"
                f"\u2022 「我的事业运怎么样」"
            )
        else:
            yield event.plain_result(
                "\U0001F4CB 还没有你的命盘档案。\n\n"
                "请告诉我你的出生信息，例如：\n"
                "\u300C我1990年5月20日早上8点生，男\u300D\n\n"
                "或使用 /设置命盘 了解详情。"
            )

    @filter.command("卜卦")
    async def cmd_bugua(self, event: AstrMessageEvent):
        gate_msg = self._counseling_gate()
        if gate_msg:
            yield event.plain_result(gate_msg)
            return
        yield event.plain_result(
            "\U0001F52F 请直接告诉我你想占卜的具体问题，例如：\n"
            "\u2022 「我该不该换工作？」\n"
            "\u2022 「这次考试能过吗？」\n"
            "\u2022 「我和TA的感情发展如何？」\n"
            "\u2022 「这笔投资能赚钱吗？」\n\n"
            "问题越具体明确，卦象越有参考价值。"
        )

    @filter.command("塔罗")
    async def cmd_tarot(self, event: AstrMessageEvent, question: str = ""):
        """塔罗抽牌（直接展示牌面，随后可让 Bot 陪你解读）。"""
        gate_msg = self._counseling_gate()
        if gate_msg:
            yield event.plain_result(gate_msg)
            return
        q = question.strip() or "我近期的整体状态"
        result = draw_spread(spread="three_card", question=q)
        text = format_tarot(result)
        text += "\n\n\u2728 想让我顺着你的处境聊聊这副牌？把你的困惑直接告诉我就行～"
        yield event.plain_result(text)

    @filter.command("我的命盘")
    async def cmd_my_chart(self, event: AstrMessageEvent):
        gate_msg = self._counseling_gate()
        if gate_msg:
            yield event.plain_result(gate_msg)
            return
        uid = event.get_sender_id()
        profile = await self._get_profile(uid)
        birth = self._birth_from(profile)
        if birth:
            name = profile.get("name", "用户")
            si = _shichen_name(_safe_int(birth["hour"]))
            yield event.plain_result(
                f"\U0001F4CB {name} 的命盘档案：\n"
                f"出生：{birth['year']}年{birth['month']}月{birth['day']}日 "
                f"{si}时（{_gender_label(birth['gender'])}）\n\n"
                f"我已经记住了你的出生信息，随时可以为你排盘看运。"
            )
        else:
            yield event.plain_result(
                "\U0001F4CB 还没有你的命盘档案。\n"
                "请使用 /设置命盘 设置，或直接告诉我你的出生信息。"
            )

    @filter.command("设置命盘")
    async def cmd_set_profile(self, event: AstrMessageEvent):
        uid = event.get_sender_id()
        profile = await self._get_profile(uid)
        birth = self._birth_from(profile)
        current = ""
        if birth:
            si = _shichen_name(_safe_int(birth["hour"]))
            current = (
                f"\n\U0001F4CC 当前存档：{birth['year']}年{birth['month']}月"
                f"{birth['day']}日 {si}时（{_gender_label(birth['gender'])}）\n"
            )
        yield event.plain_result(
            f"\U0001F4CB 请告诉我你的出生信息：{current}\n"
            "直接这样说即可：\n"
            "\u300C设置命盘：1990年5月20日早上8点，男\u300D\n\n"
            "或者分几次告诉我也行，我能记住：\n"
            "\u2022 出生年份（公历）\n"
            "\u2022 出生月日\n"
            "\u2022 出生钟点（如早上8点、下午3点）\n"
            "\u2022 性别\n\n"
            "设置后我会记住，以后看盘不用重复说。"
        )

    @filter.command("命理方法")
    async def cmd_method(self, event: AstrMessageEvent, method: str = ""):
        """设置后台测算所用的命理方法（紫微/八字/六爻/奇门/梅花/自动）。"""
        gate_msg = self._counseling_gate()
        if gate_msg:
            yield event.plain_result(gate_msg)
            return
        uid = event.get_sender_id()
        mapping = {
            "紫微": "ziwei", "紫微斗数": "ziwei", "八字": "bazi", "八子": "bazi",
            "六爻": "liuyao", "六爻卦": "liuyao",
            "奇门": "qimen", "奇门遁甲": "qimen",
            "梅花": "meihua", "梅花易数": "meihua",
            "自动": "auto", "auto": "auto",
        }
        m = (method or "").strip()
        if not m:
            yield event.plain_result(
                "\U0001F52E 设置后台测算的命理方法：\n"
                "\u2022 /命理方法 紫微   （命盘类：测算大限/流年/流月）\n"
                "\u2022 /命理方法 八字   （命盘类：测算大运/流年/流月）\n"
                "\u2022 /命理方法 六爻   （卜卦类：每月卜问情况）\n"
                "\u2022 /命理方法 奇门   （卜卦类：每月卜问情况）\n"
                "\u2022 /命理方法 梅花   （卜卦类：每月卜问情况，桥接六爻）\n"
                "\u2022 /命理方法 自动   （有出生信息→命盘，否则→六爻）\n\n"
                "不设置则按「自动」推断。"
            )
            return
        key = mapping.get(m)
        if not key:
            yield event.plain_result(
                "\u26A0\uFE0F 没认出这个方法。可选：紫微 / 八字 / 六爻 / 奇门 / 梅花 / 自动。"
            )
            return
        profile = await self._get_profile(uid)
        profile["method"] = key
        await self._save_profile(uid, profile)
        try:
            if self.background_watch:
                birth = self._birth_from(profile)
                await self.background_watch.watch_user(uid, birth, key)
        except Exception:
            pass
        label = {
            "ziwei": "紫微（命盘类·大限/流年/流月）",
            "bazi": "八字（命盘类·大运/流年/流月）",
            "liuyao": "六爻（卜卦类·卜问情况）",
            "qimen": "奇门（卜卦类·卜问情况）",
            "meihua": "梅花（卜卦类·卜问情况，桥接六爻）",
            "auto": "自动推断",
        }.get(key, key)
        yield event.plain_result(
            f"\u2705 已设置后台测算方法：{label}\n"
            f"从下个月起（或你出现高权重情绪时）会按这个方法默默留意你～"
        )

    @filter.command("我的绑定")
    async def cmd_my_binding(self, event: AstrMessageEvent):
        """查询当前账号的跨平台绑定状态（只读，不提供改绑定入口）。

        安全设计：绑定写入仅经管理面板（站长信任区）完成。聊天侧只读——若允许
        普通用户在聊天里把自己绑到任意 unified_user_id，等于可劫持他人长期记忆
        （本插件无 admin 鉴权、无跨账号归属验证），故此处只暴露查询。
        """
        from integration.identity_resolver import KV_BINDING_PREFIX, resolve_user
        uid = event.get_sender_id()
        try:
            ident = await resolve_user(event, kv_get=self.get_kv_data)
        except Exception:
            ident = None
        platform = getattr(ident, "platform", "") or ""
        unified = getattr(ident, "unified_user_id", "") or uid
        is_bound = bool(getattr(ident, "is_bound", False))
        # 读原始绑定映射，向用户展示「哪个账号并到了哪个主身份」
        bound_to = ""
        try:
            raw = await self.get_kv_data(f"{KV_BINDING_PREFIX}:{uid}", None)
            bound_to = (raw or "").strip() if isinstance(raw, str) else ""
        except Exception:
            bound_to = ""
        lines = [f"\U0001F510 当前账号：{uid}"]
        if platform:
            lines.append(f"平台：{platform}")
        lines.append(f"长期记忆身份：{unified}")
        if is_bound and bound_to:
            lines.append("\u2705 已与主身份并到一起，跨平台共享长期记忆。")
        else:
            lines.append(
                "本账号独立保存记忆（不同平台互不相通）。"
                "如需把几个平台的账号并到一起，请联系管理员设置。")
        yield event.plain_result("\n".join(lines))

    @filter.command("心情")
    async def cmd_mood(self, event: AstrMessageEvent):
        """查看当前情绪状态和近况分析。"""
        uid = event.get_sender_id()
        emotion = self.tracker.get_emotion_summary(uid)
        state = self.tracker.get_state(uid)

        if emotion:
            phase_desc = {
                "idle": "我就在，随时找我",
                "noticed": "我感觉到你最近有点心事",
                "hooked": "刚才我们聊了聊你",
                "exploring": "我们正在一起琢磨呢",
                "prophesied": "刚给你打了句气",
            }
            phase_text = phase_desc.get(state.phase, "")
            yield event.plain_result(
                f"\U0001F9E0 近期情绪状态：{emotion}\n"
                f"\U0001F4CB 我的感觉：{phase_text}\n\n"
                f"想聊两句不？我可以帮你：\n"
                f"\u2022 看看最近的运势（直接问我「看看我最近的运势」）\n"
                f"\u2022 琢磨某个困扰（用 /开解 [话题]）\n"
                f"\u2022 排盘看八字/紫微（用 /排盘）\n"
                f"\u2022 占卜具体问题（直接说出你的问题就行）\n\n"
                f"或者，就随便扯扯也是好的 \u263A\uFE0F"
            )
        else:
            yield event.plain_result(
                "\U0001F9E0 我还不太了解你最近咋样呢。\n\n"
                "多跟我唠唠吧——开心的、烦的、迷糊的，我都听着。\n"
                "想聊的时候，我随时能帮你看盘、占卜，或者就陪你瞎聊。"
            )

    @filter.command("开解")
    async def cmd_counsel(self, event: AstrMessageEvent, topic: str = ""):
        """心理咨询式自我探查入口（开启多轮疏导会话）。"""
        uid = event.get_sender_id()

        if not topic:
            yield event.plain_result(
                "\U0001F9E0 请告诉我你想探讨的话题，例如：\n"
                "\u2022 /开解 工作方向\n"
                "\u2022 /开解 感情困惑\n"
                "\u2022 /开解 人生意义\n\n"
                "或者直接和我聊聊你的困扰，我会以朋友的方式陪你探索。"
            )
            return

        session = await self.counsel_sessions.get(uid)
        if session is None:
            session = CounselingSession(topic=topic)
        else:
            session.topic = topic
        await self.counsel_sessions.save(uid, session)
        await self._set_topic(uid, topic)

        self.tracker.exploration_started(uid)

        yield event.plain_result(
            f"\U0001F9E0 来，咱聊聊「{topic}」。\n\n"
            f"先不急着下结论，你先说说——关于{topic}，你心里最真实的那句是啥？"
        )

    @filter.command("结束开解")
    async def cmd_end_counsel(self, event: AstrMessageEvent):
        """结束当前心理疏导会话。"""
        uid = event.get_sender_id()
        await self.counsel_sessions.end(uid)
        yield event.plain_result(
            "\U0001F9E0 好，这次先到这儿。\n"
            "不管聊出个啥，你愿意跟我说这些，我就挺开心的。\n"
            "啥时候想接着聊，随时找我～"
        )

    @filter.command("健康")
    async def cmd_health(self, event: AstrMessageEvent):
        """健康生活指导入口。"""
        uid = event.get_sender_id()
        profile = await self._get_health_profile(uid)
        if not profile.has_basic:
            yield event.plain_result(
                "\U0001F4A1 想给你定制健康建议，先告诉我一点基本情况吧～\n"
                "比如直接说：\n"
                "\u300C帮我看看健康，我每天12点睡7点起，一周运动2次，想改善睡眠\u300D\n\n"
                "我也能记着，下次不用重复说。或者你先随便唠唠你的日常也行。"
            )
            return
        plan = generate_health_plan(profile)
        text = format_health(plan)
        text += "\n\n\u2728 想听我顺着你的情况唠两句？跟我说说你的日常就好～"
        yield event.plain_result(text)

    # ---- 角色切换共享助手（私聊专属） ----

    async def _list_unlocked_cards(self, uid: str):
        """返回 (cards, current_id, hidden_unlocked)。cards 为用户已解锁角色的卡片 dict 列表。"""
        if not self.character_collection or not self.character_pool:
            return [], None, False
        coll = await self.character_collection.get_user_collection(uid)
        ids = list(coll["unlocked"])
        hidden_ok = bool(coll.get("hidden_unlocked", False))
        if hidden_ok and HIDDEN_PRESET_ID not in ids:
            ids.append(HIDDEN_PRESET_ID)
        cards = []
        for cid in ids:
            c = await self.character_pool.get_character(cid)
            if c:
                cards.append(c)
        return cards, coll.get("current"), hidden_ok

    async def _find_unlocked_character(self, uid: str, query: str):
        """在用户已解锁角色中按 编号 / 名字 / 关键词 解析目标角色，返回 card dict 或 None。"""
        cards, _, _ = await self._list_unlocked_cards(uid)
        if not cards:
            return None
        q = (query or "").strip()
        if not q:
            return None
        # 1) 编号（1-based）
        if q.isdigit():
            idx = int(q) - 1
            if 0 <= idx < len(cards):
                return cards[idx]
        # 2) 精确 id / 头像
        for c in cards:
            if c["id"] == q or c.get("avatar") == q:
                return c
        # 3) 名字 / 描述 / 特点 / 口头禅 / 兴趣 关键词包含
        ql = q.lower()
        for c in cards:
            name = (c.get("name") or "").lower()
            if ql in name:
                return c
            hay = " ".join([
                c.get("description", ""),
                " ".join(c.get("traits", []) or []),
                " ".join(c.get("catchphrases", []) or []),
                " ".join(c.get("interests", []) or []),
                " ".join(c.get("habits", []) or []),
            ]).lower()
            if ql in hay:
                return c
        return None

    async def _do_switch_character(self, uid: str, char_id: str) -> dict:
        """执行切换（含隐藏角色），返回 {'ok': bool, 'card': dict|None}。"""
        ok = await self.character_collection.switch_to_character(uid, char_id)
        if not ok:
            return {"ok": False, "card": None}
        card = await self.character_pool.get_character(char_id)
        return {"ok": True, "card": card}

    def _format_character_list(self, cards, current_id) -> str:
        total = len(VISIBLE_PRESETS)
        lines = ["🎭 你已解锁的角色（回复「/切换角色 <编号或名字>」切换）："]
        for i, c in enumerate(cards, 1):
            mark = "（当前）" if c["id"] == current_id else ""
            lines.append(f"{i}️⃣ {c['name']} {c.get('avatar', '')}{mark}")
        lines.append("")
        lines.append("示例：/切换角色 2  或  /切换角色 毒舌损友  或  /切换角色 下一个")
        remaining = total - len(cards)
        if remaining > 0:
            lines.append(f"（还有 {remaining} 个角色等你解锁，多跟我互动就能解锁～）")
        return "\n".join(lines)

    @filter.command("切换角色")
    async def cmd_switch_character(self, event: AstrMessageEvent, query: str = ""):
        """切换当前陪你的角色（私聊专属）。无参数时列出已解锁角色；带参数按 编号/名字/特征 切换。

        群聊下角色体系不适用，优雅提示去私聊切换。
        """
        msg_obj = getattr(event, "message_obj", None)
        is_group = msg_type_is(msg_obj, "GroupMessage")
        if is_group:
            yield event.plain_result("🎭 角色切换是私聊专属功能，请私聊我再来切换～")
            return
        uid = event.get_sender_id()
        if not self.character_collection or not self.character_pool:
            yield event.plain_result("⚠️ 角色系统暂不可用。")
            return

        # 确保该用户至少有一个初始角色
        await self.character_collection.assign_initial_character(uid)
        cards, current_id, _ = await self._list_unlocked_cards(uid)
        if not cards:
            yield event.plain_result("⚠️ 你还没有解锁任何角色。")
            return

        q = (query or "").strip()
        if not q:
            yield event.plain_result(self._format_character_list(cards, current_id))
            return

        # 支持「下一个 / 换一个」循环切换
        if q in ("下一个", "下一个角色", "换一个", "换一个角色", "next", "cycle", "下一个朋友"):
            try:
                idx = cards.index(next(c for c in cards if c["id"] == current_id))
            except Exception:
                idx = -1
            target = cards[(idx + 1) % len(cards)]
        else:
            target = await self._find_unlocked_character(uid, q)

        if not target:
            yield event.plain_result(
                "😶 没找到这个角色，已解锁的如下：\n" + self._format_character_list(cards, current_id)
            )
            return

        res = await self._do_switch_character(uid, target["id"])
        if not res["ok"]:
            yield event.plain_result("⚠️ 切换失败，该角色似乎尚未解锁。")
            return
        card = res["card"]
        yield event.plain_result(
            f"✅ 已切换为「{card['name']}」{card.get('avatar', '')}\n"
            f"{card.get('description', '')}\n\n"
            f"接下来我会用 ta 的口吻陪你～（从下一条消息开始生效）"
        )

    @filter.command("角色图鉴")
    async def cmd_character_codex(self, event: AstrMessageEvent):
        """查看全部角色图鉴：已解锁/未解锁状态、当前角色、解锁提示（私聊专属）。

        让角色切换方案真正可用——用户先知道有哪些角色、如何解锁，再去 /切换角色。
        """
        msg_obj = getattr(event, "message_obj", None)
        is_group = msg_type_is(msg_obj, "GroupMessage")
        if is_group:
            yield event.plain_result("🎭 角色图鉴是私聊专属，请私聊我查看～")
            return
        uid = event.get_sender_id()
        if not self.character_collection or not self.character_pool:
            yield event.plain_result("⚠️ 角色系统暂不可用。")
            return

        lines = await self._character_codex_lines(uid)
        yield event.plain_result("\n".join(lines))

    async def _character_codex_lines(self, uid: str) -> list:
        """角色图鉴内容（/角色图鉴 命令与 character_codex_progress 工具共用）。

        调用方需保证 uid 来自私聊会话。
        """
        coll = await self.character_collection.get_user_collection(uid)
        unlocked = set(coll["unlocked"])
        if coll.get("hidden_unlocked") and HIDDEN_PRESET_ID not in unlocked:
            unlocked.add(HIDDEN_PRESET_ID)
        current = coll.get("current")

        presets = await self.character_pool.list_presets()
        hidden = self.character_pool.get_hidden_preset()

        lines = ["📖 角色图鉴（想换谁陪你就直接说名字，或说「切换」）："]
        for i, p in enumerate(presets, 1):
            pid = p["id"]
            is_u = pid in unlocked
            mark = "（当前）" if pid == current else ""
            state = "✅ 已解锁" if is_u else "🔒 未解锁"
            lines.append(f"{i}️⃣ {p['name']} {p.get('avatar', '')} — {state}{mark}")
        if hidden:
            pid = hidden["id"]
            is_u = pid in unlocked
            mark = "（当前）" if pid == current else ""
            if is_u:
                state = "✅ 已解锁"
                hint = ""
            else:
                state = "🔒 隐藏款"
                hint = "（集齐其它 6 位后，问问 TA 是不是瞒着你什么）"
            lines.append(f"👑 {hidden['name']} — {state}{mark}{hint}")

        total = len(presets) + (1 if hidden else 0)
        unlocked_count = sum(1 for p in presets if p["id"] in unlocked) + (
            1 if hidden and hidden["id"] in unlocked else 0
        )
        lines.append("")
        lines.append(f"已解锁 {unlocked_count}/{total} 个。")
        # 如实给出下一个可见角色的解锁进度（基于当前角色羁绊阈值）
        visible_u = sum(1 for p in presets if p["id"] in unlocked)
        if visible_u < len(presets):
            next_thr = self.character_collection.next_unlock_threshold(coll)
            if next_thr is not None:
                lines.append(f"再和当前角色多培养羁绊（好感度达到 {next_thr}）就能解锁下一个角色～")
            else:
                lines.append("多跟我互动就能解锁更多角色～")
        elif hidden and hidden["id"] not in unlocked:
            lines.append("六位朋友都到齐了……可 TA 身边，好像还藏着一个名字。")
        else:
            lines.append("所有朋友都到你身边了，收集全图鉴达成！")
        return lines

    @filter.llm_tool(name="character_codex_progress")
    async def character_codex_progress(self, event: AstrMessageEvent) -> str:
        """查看用户的角色收集进度与图鉴。当用户问「都有谁」「还差谁」「还能认识谁」「图鉴」「收集进度」「解锁几个角色」「想认识新朋友/新角色」「身边有哪些人」时使用；返回已解锁/未解锁角色名单与下一步解锁提示，请用你自己的口吻自然转述给 TA，不要照抄列表格式。仅私聊有效。
        """
        msg_obj = getattr(event, "message_obj", None)
        is_group = msg_type_is(msg_obj, "GroupMessage")
        if is_group:
            return "这个得在私聊里，我悄悄给你看～"
        uid = event.get_sender_id()
        if not self.character_collection or not self.character_pool:
            return "角色系统暂时不可用。"
        lines = await self._character_codex_lines(uid)
        return "\n".join(lines)

    # ============================================================
    # 被动收集（原 stars/collectors.py，内联迁入使 AstrBot 能识别绑定）
    # ============================================================

    # 出生年提取：第一道闸是 filter 正则（只要含"我…年"就进来），
    # 第二道闸在 handler 内——必须命中「出生语义」才采信，否则跳过。
    # 历史缺陷：用户说"分析我在2022-2035年的感情运势"，"我…2035年"误命中
    # filter，且 BIRTH_YEAR_RE 把"-2035年"后段当出生年 → 档案被写坏。
    # v4.8.105：filter 保持宽松（避免漏掉"我1990年生"等正常句式），
    # handler 内用 _extract_birth_year_from_msg 做出生语义判定 + 范围排除。
    @filter.regex(r"(?:我是?|我的).*?(\d{4})\s*年")
    async def _on_birth_year(self, event: AstrMessageEvent):
        try:
            uid = event.get_sender_id()
            year = _extract_birth_year_from_msg(event.message_str)
            if year is None:
                return
            profile = await self._get_profile(uid)
            if "birth" not in profile:
                profile["birth"] = {}
            profile["birth"]["year"] = year
            await self._save_profile(uid, profile)
            logger.info(f"[mingbu] 收集到 {uid} 出生年份: {year}")
        except Exception as e:
            logger.debug(f"[mingbu] 出生年份收集异常(已忽略): {e}")

    @filter.regex(r"我属([鼠牛虎兔龙蛇马羊猴鸡狗猪])")
    async def _on_zodiac(self, event: AstrMessageEvent):
        try:
            uid = event.get_sender_id()
            m = ZODIAC_RE.search(event.message_str)
            if m:
                zodiac = m.group(1)
                logger.info(f"[mingbu] 检测到 {uid} 属相: {zodiac}")
                profile = await self._get_profile(uid)
                profile["zodiac_hint"] = zodiac
                await self._save_profile(uid, profile)
        except Exception as e:
            logger.debug(f"[mingbu] 属相收集异常(已忽略): {e}")

    @filter.regex(
        r"(?:排盘|看八字|看盘|算命[中吗]?|算一卦|"
        r"占卜|卜卦|六爻|测一卦|测测|"
        r"紫微斗数|紫微命盘|看紫微|运势如何|今年运势|"
        r"帮我算|测一下|看看运势)"
    )
    async def _on_mingbu_keyword(self, event: AstrMessageEvent):
        try:
            if not self._config.get("counseling_enabled", True):
                return
            uid = event.get_sender_id()
            await self._append_context(uid, event.message_str)
        except Exception as e:
            logger.debug(f"[mingbu] 命卜关键词收集异常(已忽略): {e}")

    @filter.regex(EMOTION_KEYWORD_PATTERN)
    async def _on_emotion_signal(self, event: AstrMessageEvent):
        """检测情绪关键词，记录到情绪追踪器 + 情绪记忆。"""
        try:
            uid = event.get_sender_id()
            self.tracker.record(uid, event.message_str)
            await self._append_context(uid, event.message_str)
            dom = self.tracker.get_dominant_emotion(uid)
            weight = {"stress": -0.6, "sadness": -0.8, "worry": -0.5,
                      "uncertainty": -0.4, "self_doubt": -0.7,
                      "hope": 0.7, "anger": -0.6}.get(dom or "", -0.3)
            try:
                await self.memory.add_emotion(
                    uid, f"[情绪] {event.message_str[:60]}",
                    weight, importance=0.55, tags=["情绪", dom or ""],
                )
            except Exception as e:
                logger.debug(f"[mingbu] 写入情绪记忆失败(已忽略): {e}")
        except Exception as e:
            logger.debug(f"[mingbu] 情绪信号收集异常(已忽略): {e}")

    # ============================================================
    # LLM 工具（原 stars/tools.py，内联迁入使 AstrBot 能识别登记）
    # ============================================================

    @filter.llm_tool(name="query_bazi")
    async def query_bazi(
        self, event: AstrMessageEvent,
        year: int, month: int, day: int,
        birth_hour: int, gender: str,
    ) -> str:
        """查询八字命盘。
        当用户想了解自己的八字、命理格局、五行、大运、用神时使用此工具。
        如果用户没有提供完整的出生信息（年/月/日/钟点/性别），
        请在对话中先向用户询问缺失的信息，收集完整后再调用。

        注意（重要）：若出生钟点为 23（晚子时，23:00–23:59），请直接传入
        year/month/day/23 的原值，不要自行把日期 +1。插件内部会自动按次日
        排盘（日柱、时柱顺延到次日）。切勿因"晚子时"反复重排或重复调用本工具。

        Args:
            year(number): 出生年份（公历），如 1990
            month(number): 出生月份（1-12）
            day(number): 出生日（1-31）
            birth_hour(number): 出生钟点（0-23）。注意 23 即晚子时，直接传 23 原值即可，插件会自动换日，勿自行 +1
            gender(string): 性别，male=男 female=女
        """
        gate_msg = self._counseling_gate()
        if gate_msg:
            return gate_msg
        ok, err = self._validate_birth(year, month, day, birth_hour, gender)
        if not ok:
            return f"\u26A0\uFE0F {err}"
        try:
            birth = self._birth_params(year, month, day, birth_hour, gender,
                                       event.get_sender_name() or "")
            await self._save_birth(event, year, month, day, birth_hour, gender)

            result = await self.api.bazi(birth)
            summary = format_bazi(result)

            # 附八字技法知识库（自 mingbu 移植），作为专业解读 grounding
            chart = result.get("chart", result) if isinstance(result, dict) else {}
            grounding = build_bazi_knowledge_grounding(chart)
            if grounding:
                summary = f"{summary}\n\n{grounding}"

            uid = event.get_sender_id()
            # 排盘后标记为探索完成（与合参/六爻/奇门一致）
            self.tracker.exploration_completed(uid, result)

            if birth_hour == 23:
                summary = f"\U0001F319 晚子时（23 点后出生，按次日排日柱）\n\n{summary}"
            return summary
        except Exception as e:
            logger.error(f"[mingbu] 八字排盘失败: {e}")
            return f"\u26A0\uFE0F 八字排盘暂时不可用：{str(e)[:120]}"

    @filter.llm_tool(name="query_ziwei")
    async def query_ziwei(
        self, event: AstrMessageEvent,
        year: int, month: int, day: int,
        birth_hour: int, gender: str,
    ) -> str:
        """查询紫微斗数命盘。
        当用户想看紫微命盘、星曜分布、十二宫、大限走势时使用。
        需要完整的出生信息（年/月/日/钟点/性别）。

        注意（重要）：若出生钟点为 23（晚子时，23:00–23:59），请直接传入
        year/month/day/23 的原值，不要自行把日期 +1。插件内部会自动按次日
        排盘（日柱、时柱顺延到次日）。切勿因"晚子时"反复重排或重复调用本工具。

        Args:
            year(number): 出生年份（公历），如 1990
            month(number): 出生月份（1-12）
            day(number): 出生日（1-31）
            birth_hour(number): 出生钟点（0-23）。注意 23 即晚子时，直接传 23 原值即可，插件会自动换日，勿自行 +1
            gender(string): 性别，male=男 female=女
        """
        gate_msg = self._counseling_gate()
        if gate_msg:
            return gate_msg
        ok, err = self._validate_birth(year, month, day, birth_hour, gender)
        if not ok:
            return f"\u26A0\uFE0F {err}"
        try:
            birth = self._birth_params(year, month, day, birth_hour, gender,
                                       event.get_sender_name() or "")
            await self._save_birth(event, year, month, day, birth_hour, gender)

            result = await self.api.combined(birth)
            summary = format_ziwei(result)

            # 附紫微知识库（自 mingbu 移植），作为专业解读 grounding
            analysis = result.get("analysis", result) if isinstance(result, dict) else {}
            zw_chart = (analysis.get("ziwei", {}) or {}) if isinstance(analysis, dict) else {}
            grounding = build_ziwei_knowledge_grounding(zw_chart)
            if grounding:
                summary = f"{summary}\n\n{grounding}"

            uid = event.get_sender_id()
            # 排盘后标记为探索完成（与合参/六爻/奇门一致）
            self.tracker.exploration_completed(uid, result)

            if birth_hour == 23:
                summary = f"\U0001F319 晚子时（23 点后出生，按次日排盘）\n\n{summary}"
            return summary
        except Exception as e:
            logger.error(f"[mingbu] 紫微排盘失败: {e}")
            return f"\u26A0\uFE0F 紫微排盘暂时不可用：{str(e)[:120]}"

    @filter.llm_tool(name="divination_liuyao")
    async def divination_liuyao(
        self, event: AstrMessageEvent,
        question: str,
        question_type: str = "general",
    ) -> str:
        """六爻占卜起卦。
        当用户想占卜某事吉凶、问卦、求测时使用。
        需要用户提供具体明确的问题，问题越具体卦象越有参考价值。

        注意（重要）：本工具每次起卦结果随机，同一问题只需起卦一次，
        拿到卦象后请直接解读，切勿因"卦象不好/想再确认"重复调用本工具——
        一卦一占，卦不二起；重复起卦只会得到不同的卦象，干扰判断。

        Args:
            question(string): 用户想问的具体问题，如"我该不该换工作？"
            question_type(string): 问题类别，可选：general(综合)/career(事业)/wealth(财运)/love(感情)/health(健康)/study(学业)/family(家庭)/yearly(流年)
        """
        gate_msg = self._counseling_gate()
        if gate_msg:
            return gate_msg
        uid = event.get_sender_id()
        key = self._divination_cache_key("liuyao", uid, question)
        cached = self._divination_cached(key)
        if cached is not None:
            logger.info(f"[mingbu] 六爻防重命中（同一问题 TTL 内重复调用，沿用上次卦象）uid={uid}")
            return cached
        try:
            result = await self.api.liuyao(question, question_type)
            summary = format_liuyao(result)

            # 占卜后标记为探索完成
            self.tracker.exploration_completed(uid, result)

            # 附六爻技法库（本地，全面准确），作为专业断卦依据
            chart = result.get("chart", result) if isinstance(result, dict) else {}
            grounding = build_liuyao_knowledge_grounding(chart)
            if grounding:
                summary = f"{summary}\n\n{grounding}"

            self._divination_cache_set(key, summary)
            return summary
        except Exception as e:
            logger.error(f"[mingbu] 六爻占卜失败: {e}")
            return f"\u26A0\uFE0F 占卜服务暂时不可用：{str(e)[:120]}"

    @filter.llm_tool(name="query_qimen")
    async def query_qimen(
        self, event: AstrMessageEvent,
        question: str = "",
    ) -> str:
        """奇门遁甲起局。
        当用户想用奇门遁甲问事、看吉凶方位、择时择地、判断当前形势时使用。
        起局以当前时间排盘，无需用户提供出生信息。

        注意（重要）：同一问题只需起局一次，拿到结果后请直接解读，
        切勿因"局象不好/想再确认"重复调用本工具。

        Args:
            question(string): 用户想问的问题（可选），如"这次合作能成吗？"
        """
        gate_msg = self._counseling_gate()
        if gate_msg:
            return gate_msg
        uid = event.get_sender_id()
        key = self._divination_cache_key("qimen", uid, question)
        cached = self._divination_cached(key)
        if cached is not None:
            logger.info(f"[mingbu] 奇门防重命中（同一问题 TTL 内重复调用，沿用上次局象）uid={uid}")
            return cached
        try:
            now = now_bj()
            # 后端 /api/qimen 的 hour 是「时辰地支索引 0–11」，now.hour 为 0–23 钟点需折算
            result = await self.api.qimen(
                now.year, now.month, now.day,
                _clock_to_shichen(now.hour), now.minute,
            )
            summary = format_qimen(result)

            # 占卜后标记为探索完成
            self.tracker.exploration_completed(uid, result)

            # 附奇门技法库（本地，全面准确），作为专业断局依据
            chart = result.get("chart", result) if isinstance(result, dict) else {}
            grounding = build_qimen_knowledge_grounding(chart)
            if grounding:
                summary = f"{summary}\n\n{grounding}"

            if question:
                summary = f"\u2753 所问：{question}\n\n{summary}"

            self._divination_cache_set(key, summary)
            return summary
        except Exception as e:
            logger.error(f"[mingbu] 奇门起局失败: {e}")
            return f"\u26A0\uFE0F 奇门遁甲服务暂时不可用：{str(e)[:120]}"

    @filter.llm_tool(name="query_combined")
    async def query_combined(
        self, event: AstrMessageEvent,
        year: int, month: int, day: int,
        birth_hour: int, gender: str,
    ) -> str:
        """八字+紫微合参分析。两个体系交叉印证，给出更全面的命理判断。
        当用户希望获得深入的、双体系交叉的命理分析时使用。
        需要完整的出生信息。

        注意（重要）：若出生钟点为 23（晚子时，23:00–23:59），请直接传入
        year/month/day/23 的原值，不要自行把日期 +1。插件内部会自动按次日
        排盘（日柱、时柱顺延到次日）。切勿因"晚子时"反复重排或重复调用本工具。

        Args:
            year(number): 出生年份（公历）
            month(number): 出生月份（1-12）
            day(number): 出生日（1-31）
            birth_hour(number): 出生钟点（0-23）。注意 23 即晚子时，直接传 23 原值即可，插件会自动换日，勿自行 +1
            gender(string): 性别，male=男 female=女
        """
        gate_msg = self._counseling_gate()
        if gate_msg:
            return gate_msg
        ok, err = self._validate_birth(year, month, day, birth_hour, gender)
        if not ok:
            return f"\u26A0\uFE0F {err}"
        try:
            birth = self._birth_params(year, month, day, birth_hour, gender,
                                       event.get_sender_name() or "")
            await self._save_birth(event, year, month, day, birth_hour, gender)

            result = await self.api.combined(birth)
            analysis = result.get("analysis", result)
            bazi_chart = analysis.get("bazi", {}) or {}
            bazi_txt = format_bazi({"chart": bazi_chart})
            # 附八字技法知识库（自 mingbu 移植），作为专业解读 grounding
            bazi_grounding = build_bazi_knowledge_grounding(bazi_chart)
            if bazi_grounding:
                bazi_txt = f"{bazi_txt}\n\n{bazi_grounding}"
            ziwei_txt = format_ziwei(result)
            # 附紫微知识库（自 mingbu 移植），作为专业解读 grounding
            zw_chart = analysis.get("ziwei", {}) or {}
            ziwei_grounding = build_ziwei_knowledge_grounding(zw_chart)
            if ziwei_grounding:
                ziwei_txt = f"{ziwei_txt}\n\n{ziwei_grounding}"
            signals_txt = format_signals(analysis.get("signals", []))

            uid = event.get_sender_id()

            # 标记探索完成
            self.tracker.exploration_completed(uid, result)

            combined = f"{bazi_txt}\n\n---\n\n{ziwei_txt}{signals_txt}"
            if birth_hour == 23:
                combined = f"\U0001F319 晚子时（23 点后出生，按次日排盘）\n\n{combined}"
            return combined
        except Exception as e:
            logger.error(f"[mingbu] 合参分析失败: {e}")
            return f"\u26A0\uFE0F 合参分析暂时不可用：{str(e)[:120]}"

    async def _resolve_interpret_provider(self):
        """解析命理 AI 解读（/api/interpret）用的 provider。

        优先级：
          1. 插件配置 mingbu_interpret_*（可指定与 AstrBot 不同的专属服务商）；
          2. 若配置留空，回退到 AstrBot 当前正在使用的对话服务商，将其
             key/base_url/model 透传给 mingbu —— 实现「与 AstrBot 同一个 provider」；
          3. 都取不到则返回全 None，由 mingbu 用自己的环境变量兜底。

        Returns:
            (provider, api_key, base_url, model) 四元组，任一缺失即为 None。
        """
        cfg = self._config or {}
        ak = (cfg.get("mingbu_interpret_api_key") or "").strip()
        bu = (cfg.get("mingbu_interpret_base_url") or "").strip()
        mdl = (cfg.get("mingbu_interpret_model") or "").strip()
        prov = (cfg.get("mingbu_interpret_provider") or "").strip()
        if ak and bu and mdl:
            return prov or None, ak, bu, mdl
        # 回退：AstrBot 当前正在使用的对话服务商（与 AstrBot 同一个 provider）
        try:
            from .provider_router import get_default_chat_provider
            p = await get_default_chat_provider(self.context)
            if p is not None:
                pc = getattr(p, "provider_config", None) or {}
                pc = pc if isinstance(pc, dict) else {}

                def _g(*keys):
                    for k in keys:
                        v = pc.get(k) if isinstance(pc, dict) else None
                        if not v:
                            v = getattr(p, k, None)
                        if v:
                            return str(v)
                    return None

                ak2 = _g("api_key", "key", "apiKey")
                bu2 = _g("base_url", "baseUrl", "api_base", "baseurl")
                mdl2 = _g("model", "model_name")
                if ak2 and bu2 and mdl2:
                    return (
                        (pc.get("type") or pc.get("provider") or prov or None),
                        ak2, bu2, mdl2,
                    )
        except Exception as e:
            logger.debug(f"[mingbu] 取 AstrBot 当前 provider 失败，mingbu 将用自身环境变量: {e}")
        return None, None, None, None

    @filter.llm_tool(name="query_interpret")
    async def query_interpret(
        self, event: AstrMessageEvent,
        year: int, month: int, day: int,
        birth_hour: int, gender: str,
        topic: str = "free",
        type: str = "combined",
        question: str = "",
    ) -> str:
        """命理深度解读（推荐）。当用户想获得完整、专业的命理分析时使用此工具，
        例如详细的运势解读、综合判断、事业/财运/感情/健康专题分析、流年/大限走势。
        需要完整的出生信息（年/月/日/钟点/性别）。

        注意（重要）：若出生钟点为 23（晚子时，23:00–23:59），请直接传入
        year/month/day/23 的原值，不要自行把日期 +1。插件内部会自动按次日
        排盘（日柱、时柱顺延到次日）。切勿因"晚子时"反复重排或重复调用本工具。

        本工具会调用命理引擎，基于命盘事实与知识库生成一份「中性、专业」的分析文本。
        拿到结果后，请务必用你（当前角色）的口吻、风格重新组织并输出给用户：
        保留全部事实、判断与要点，不要照抄原文，也不要额外编造命理术语。

        Args:
            year(number): 出生年份（公历），如 1990
            month(number): 出生月份（1-12）
            day(number): 出生日（1-31）
            birth_hour(number): 出生钟点（0-23）。注意 23 即晚子时，直接传 23 原值即可，插件会自动换日，勿自行 +1
            gender(string): 性别，male=男 female=女
            topic(string): 解读专题，可选 free(自由)/overview(总览)/career(事业)/wealth(财运)/love(感情)/health(健康)/personality(性格)/daxian(大限)/liunian(流年)/current(当前)
            type(string): 命盘体系，combined(八字+紫微合参,默认)/ziwei(紫微)/bazi(八字)
            question(string): 用户本次想问的核心问题（可选），如"下半年事业运如何"
        """
        gate_msg = self._counseling_gate()
        if gate_msg:
            return gate_msg
        ok, err = self._validate_birth(year, month, day, birth_hour, gender)
        if not ok:
            return f"\u26A0\uFE0F {err}"
        try:
            birth = self._birth_params(year, month, day, birth_hour, gender,
                                       event.get_sender_name() or "")
            await self._save_birth(event, year, month, day, birth_hour, gender)

            # 组装对话历史（供 mingbu 做连贯的多轮解读），截断到最近 20 条
            messages: List[Dict[str, str]] = []
            try:
                get_msgs = getattr(event, "get_messages", None)
                if callable(get_msgs):
                    raw = get_msgs()
                    if inspect.isawaitable(raw):
                        raw = await raw
                    for m in (raw or [])[-20:]:
                        role = getattr(m, "role", "user") or "user"
                        text = (getattr(m, "message_str", None)
                                or getattr(m, "content", None) or "")
                        if text:
                            messages.append({"role": str(role), "content": str(text)})
            except Exception as e:
                logger.debug(f"[mingbu] 取对话历史失败，仅用当前问题: {e}")
                messages = []
            user_text = (question or getattr(event, "message_str", "") or "").strip()
            if user_text:
                messages.append({"role": "user", "content": user_text})
            if not messages:
                messages = [{"role": "user", "content": "请解读我的命盘"}]

            prov, ak, bu, mdl = await self._resolve_interpret_provider()

            try:
                text = await self.api.interpret(
                    type=type, birth=birth, topic=topic,
                    question=user_text or None,
                    messages=messages,
                    provider=prov, api_key=ak, base_url=bu, model=mdl,
                )
                if text and text.strip():
                    if birth_hour == 23:
                        return (f"\U0001F319 晚子时（23 点后出生，按次日排盘）\n\n"
                                f"{text.strip()}\n\n"
                                f"（以上为命理引擎出具的中性专业分析，请以我的口吻转述给你）")
                    return text.strip()
                logger.warning("[mingbu] interpret 返回空，回退本地合参")
            except Exception as e:
                logger.warning(f"[mingbu] interpret 调用失败，回退本地合参: {e}")

            # 双通道兜底：mingbu 不可用 → 走本地合参（与 query_combined 一致）
            return await self.query_combined(event, year, month, day, birth_hour, gender)
        except Exception as e:
            logger.error(f"[mingbu] 深度解读失败: {e}")
            return f"\u26A0\uFE0F 命理解读暂时不可用：{str(e)[:120]}"

    @filter.llm_tool(name="query_situation")
    async def query_situation(
        self, event: AstrMessageEvent,
        concern: str = "",
    ) -> str:
        """分析用户近期整体运势和心理状态。
        当用户表示迷茫、想知道近况、或心理咨询钩子被接受后，使用此工具。
        此工具不需要用户提供出生信息（会使用已存档的或通过情绪信号分析）。

        Args:
            concern(string): 用户当前最关心的问题（可选），如"工作压力"、"感情迷茫"
        """
        gate_msg = self._counseling_gate()
        if gate_msg:
            return gate_msg
        uid = event.get_sender_id()
        profile = await self._get_profile(uid)
        birth = self._birth_from(profile)
        emotion_summary = self.tracker.get_emotion_summary(uid)

        # 标记进入探索模式
        self.tracker.exploration_started(uid)

        lines = ["\U0001F52E **近况分析与运势探查**", ""]

        # 情绪概览
        if emotion_summary:
            lines.append(f"\U0001F9E0 近期情绪状态：{emotion_summary}")
            lines.append("")

        # 如果有出生信息，做快速排盘
        if birth:
            try:
                result = await self.api.combined(birth)
                analysis = result.get("analysis", result)

                # 关键宫位星曜（共用提取）
                key_palaces = extract_key_palaces(analysis)
                if key_palaces:
                    lines.append("\U0001F3DB **关键宫位星曜**：")
                    for gong, stars in key_palaces.items():
                        lines.append(f"  {gong}：{stars}")
                    lines.append("")

                # 当前大运 / 流年
                dyn = current_da_yun_liu_nian(analysis)
                if "da_yun" in dyn:
                    lines.append(f"\U0001F4CD 当前大运：{dyn['da_yun']}")
                if "liu_nian" in dyn:
                    lines.append(f"\U0001F4CD 当前流年：{dyn['liu_nian']}年")
                lines.append("")

                self.tracker.exploration_completed(uid, result)
            except Exception as e:
                logger.warning(f"[mingbu] 近况分析排盘失败（降级至纯情绪分析）: {e}")
                lines.append("\u26A0\uFE0F 命盘数据暂时无法获取，以下为基于聊天内容的情绪分析。\n")
        else:
            lines.append(
                "\u26A0\uFE0F 尚未存储出生信息，以下分析基于聊天情绪信号。\n"
            )

        return "\n".join(lines)

    @filter.llm_tool(name="guided_reflection")
    async def guided_reflection(
        self, event: AstrMessageEvent,
        topic: str,
    ) -> str:
        """引导用户进行自我探查和内心对话。
        当用户希望深入探讨某个困扰、需要开解、或心理咨询钩子被接受后，
        使用此工具来展开心理咨询式对话。

        Args:
            topic(string): 用户想探讨的话题，如"工作方向"、"感情困惑"、"人生意义"
        """
        uid = event.get_sender_id()
        emotion_summary = self.tracker.get_emotion_summary(uid)
        self.tracker.exploration_started(uid)

        # 生成引导 prompt 交给 LLM 整合（不直接发给用户，避免内部指令泄露）
        return CounselingPrompts.guided_reflection(topic, emotion_summary)

    @filter.llm_tool(name="divination_tarot")
    async def divination_tarot(
        self, event: AstrMessageEvent,
        question: str,
        question_type: str = "general",
        spread: str = "",
    ) -> str:
        """塔罗牌占卜。当用户想用塔罗牌寻求映照、指引或自我觉察时使用。
        塔罗是映照当下状态的镜子，不是宿命宣判。问题越具体，解读越有共鸣。

        注意（重要）：本工具每次抽牌随机，同一问题只需抽牌一次，
        拿到牌面后请直接解读，切勿重复调用本工具（重复抽牌会得到不同牌面）。

        Args:
            question(string): 用户想问的问题或想映照的处境，如"我该不该换工作？"
            question_type(string): 问题类别，可选：general(综合)/love(感情)/career(事业)/decision(抉择)
            spread(string): 牌阵名（可选）：single(单张)/three_card(三张)/celtic_cross(凯尔特十字)/love(感情)/decision(抉择)/career(事业)。留空则按问题类别自动选。
        """
        gate_msg = self._counseling_gate()
        if gate_msg:
            return gate_msg
        try:
            if not self._config.get("tarot_enabled", True):
                return "\u26A0\uFE0F 塔罗功能未开启。"
            uid = event.get_sender_id()
            key = self._divination_cache_key("tarot", uid, f"{question}|{spread}")
            cached = self._divination_cached(key)
            if cached is not None:
                logger.info(f"[mingbu] 塔罗防重命中（同一问题 TTL 内重复调用，沿用上次牌面）uid={uid}")
                return cached
            sp = (spread or "").strip() or spread_for_question(question_type)
            result = draw_spread(spread=sp, question=question)
            summary = format_tarot(result)
            self._divination_cache_set(key, summary)
            return summary
        except Exception as e:
            logger.error(f"[mingbu] 塔罗占卜失败: {e}")
            return f"\u26A0\uFE0F 塔罗抽牌暂时不可用：{str(e)[:120]}"

    @filter.llm_tool(name="divination_zizhan")
    async def divination_zizhan(
        self, event: AstrMessageEvent,
        question: str,
        mode: str = "now",
    ) -> str:
        """紫占（紫微斗数占卜）：以当下时空起紫微盘问事，出三层卦断吉凶。
        当用户想用紫微占卜问事吉凶、寻物、定方向、看应期时使用。
        无需用户提供出生信息——紫占以「问事当下」起盘，心诚则灵。

        注意（重要）：本工具每次起盘基于问卦时刻，同一问题只需起卦一次，
        拿到卦象后请直接解读，切勿重复调用本工具（重复起盘会得到不同卦象，
        干扰判断）。若用户要问新问题，再为新问题调用一次。

        Args:
            question(string): 用户想问的具体问题，如"我该不该换工作？"
            mode(string): 起盘方式，now=当下时空起盘(默认)，random=真随机起盘(可选)
        """
        gate_msg = self._counseling_gate()
        if gate_msg:
            return gate_msg
        uid = event.get_sender_id()
        key = self._divination_cache_key("zizhan", uid, f"{question}|{mode}")
        cached = self._divination_cached(key)
        if cached is not None:
            logger.info(f"[mingbu] 紫占防重命中（同一问题 TTL 内重复调用，沿用上次卦象）uid={uid}")
            return cached
        try:
            result = await self.api.zizhan(question, mode)
            summary = format_zizhan(result)

            # 占卜后标记为探索完成
            self.tracker.exploration_completed(uid, result)

            self._divination_cache_set(key, summary)
            return summary
        except Exception as e:
            logger.error(f"[mingbu] 紫占失败: {e}")
            return f"\u26A0\uFE0F 紫占服务暂时不可用：{str(e)[:120]}"

    @filter.llm_tool(name="health_advice")
    async def health_advice(
        self, event: AstrMessageEvent,
        goals: str = "",
        diet_habits: str = "",
        sleep_time: str = "",
        wake_time: str = "",
        exercise_freq: int = -1,
        age: int = 0,
        conditions: str = "",
    ) -> str:
        """健康生活指导。当用户想了解日常作息、饮食、运动等个性化建议时使用。
        本工具只给生活方式层面的通用建议，不是医疗诊断。

        可补充/更新用户健康档案（留空则不改动已存档信息）：
        Args:
            goals(string): 健康目标，逗号分隔，如"减脂,改善睡眠"
            diet_habits(string): 饮食偏好或问题，如"常吃外卖,偏甜"
            sleep_time(string): 通常入睡时间，如"23:30"
            wake_time(string): 通常起床时间，如"07:00"
            exercise_freq(number): 每周运动次数 0-7（不知道填 -1）
            age(number): 年龄（不知道填 0）
            conditions(string): 需留意的身体状况，逗号分隔，如"久坐腰酸,失眠"
        """
        try:
            if not self._config.get("health_enabled", True):
                return "\u26A0\uFE0F 健康指导功能未开启。"
            uid = event.get_sender_id()
            profile = await self._get_health_profile(uid)

            # 合并用户本次提供的信息（只覆盖非空字段）
            if age and age > 0:
                profile.age = age
            if goals:
                profile.goals = [g.strip() for g in goals.split(",") if g.strip()]
            if diet_habits:
                profile.diet_habits = diet_habits
            if sleep_time:
                profile.sleep_time = sleep_time
            if wake_time:
                profile.wake_time = wake_time
            if exercise_freq is not None and exercise_freq >= 0:
                profile.exercise_freq = int(exercise_freq)
            if conditions:
                profile.conditions = [c.strip() for c in conditions.split(",") if c.strip()]

            await self._save_health_profile(uid, profile)
            plan = generate_health_plan(profile)
            summary = format_health(plan)
            return summary
        except Exception as e:
            logger.error(f"[mingbu] 健康建议生成失败: {e}")
            return f"\u26A0\uFE0F 健康建议暂时不可用：{str(e)[:120]}"

    @filter.llm_tool(name="psychological_counsel")
    async def psychological_counsel(
        self, event: AstrMessageEvent,
        topic: str = "",
        user_text: str = "",
    ) -> str:
        """多轮心理疏导对话。当用户需要被倾听、想深入聊聊困扰、或情绪需要疏导时使用。
        会延续之前的疏导会话（跨多轮连贯），以朋友方式陪 TA 探索。

        Args:
            topic(string): 本次想探讨的话题（首次可给，如"工作方向""感情困惑"）
            user_text(string): 用户这一轮想说的内容（尽量原话）
        """
        try:
            uid = event.get_sender_id()
            session = await self.counsel_sessions.get(uid)
            if session is None:
                session = CounselingSession(topic=topic)
            elif topic and topic != session.topic:
                session.topic = topic

            text = (user_text or "").strip()
            if text:
                session.add_turn("user", text)

            emotion = self.tracker.get_emotion_summary(uid)
            dominant = self.tracker.get_dominant_emotion(uid)
            prompt = counseling_turn_prompt(session, text, emotion, dominant)

            # 阶段推进：每两轮用户发言推进一级，避免过快
            user_turns = [t for t in session.turns if t.get("role") == "user"]
            if len(user_turns) % 2 == 0:
                session.advance()
            await self.counsel_sessions.save(uid, session)
            await self._set_topic(uid, session.topic or "心理疏导")

            # 生成疏导 prompt 交给 LLM 整合（不直接发给用户，避免内部指令泄露）
            return prompt
        except Exception as e:
            logger.error(f"[mingbu] 心理疏导对话失败: {e}")
            return f"\u26A0\uFE0F 疏导对话暂时不可用：{str(e)[:120]}"

    @filter.llm_tool(name="switch_character")
    async def switch_character(
        self, event: AstrMessageEvent,
        target: str = "",
    ) -> str:
        """切换当前陪用户的角色（人设）。当用户说「我想换个角色 / 换个陪我的人 / 换成 XX」时使用。
        只能切换到用户已解锁的角色；未解锁的无法切换。切换后从下一条消息开始以新角色口吻回应。

        Args:
            target(string): 用户想切换到的角色名字或特征描述，如"毒舌损友"或"想要一个能陪我吐槽的损友"
        """
        msg_obj = getattr(event, "message_obj", None)
        is_group = msg_type_is(msg_obj, "GroupMessage")
        if is_group:
            return "角色切换仅支持私聊，请私聊我再来切换～"
        uid = event.get_sender_id()
        if not self.character_collection or not self.character_pool:
            return "角色系统暂不可用。"
        await self.character_collection.assign_initial_character(uid)
        target_card = await self._find_unlocked_character(uid, target or "")
        if not target_card:
            cards, current_id, _ = await self._list_unlocked_cards(uid)
            return (
                "没找到匹配的角色。你现在已解锁的角色：\n"
                + self._format_character_list(cards, current_id)
                + "\n请从中选一个名字告诉我，例如「切换成毒舌损友」。"
            )
        res = await self._do_switch_character(uid, target_card["id"])
        if not res["ok"]:
            return "这个角色似乎还没解锁，先多跟我互动解锁更多角色吧～"
        card = res["card"]
        return (
            f"已为你切换为「{card['name']}」{card.get('avatar', '')}。"
            f"接下来我会用 ta 的口吻陪你（从下一条消息开始生效）。"
        )

    # ============================================================
    # 体验增强（原 stars/enhancements.py，内联迁入使 AstrBot 能识别绑定）
    # ============================================================

    # ---- 入站消息去重（微信客服 webhook 重投防护） ----

    def _is_replay(self, event: AstrMessageEvent) -> bool:
        """微信重投判定：同 msgid 在 TTL 内已处理过则返回 True（由调用方短路）。

        微信客服 webhook 在回调未及时 ack 时会重投同一条消息（同 msgid）。
        重投必须在 LLM 调用前短路——既省配额又避免重复回复；首次放行时在此标记。
        日志用 DEBUG（属预期内的正常拦截，不刷告警、不误导排障）。
        """
        mo = getattr(event, "message_obj", None)
        msgid = getattr(mo, "message_id", "") if mo else ""
        if not msgid:
            return False
        if self._is_seen(msgid):
            logger.debug(f"[mingbu] 微信重投已拦截 msgid={msgid[:16]}…（同消息 TTL 内已处理，跳过）")
            return True
        self._mark_seen(msgid)
        return False

    def _is_seen(self, msgid: str) -> bool:
        """msgid 是否在 TTL 内已标记（顺带清理过期条目）。"""
        now = time.time()
        for k in [k for k, v in self._seen_msgids.items() if v < now - MSGID_DEDUP_TTL]:
            self._seen_msgids.pop(k, None)
        return msgid in self._seen_msgids

    def _mark_seen(self, msgid: str) -> None:
        self._seen_msgids[msgid] = time.time()

    # ---- 占卜工具防重复起卦（LLM 工具循环护栏） ----
    # 背景：AstrBot tool_loop 下 LLM 可能对同一问题反复调用 divination_liuyao /
    # query_qimen / divination_tarot。这些工具每次结果随机（起卦/抽牌），重复调用
    # 会得到不同卦象，导致解读混乱 + 平台配额浪费。因此同用户同问题在 TTL 内
    # 重复调用时直接返回上次结果（一卦一占，卦不二起）。

    DIVINATION_DEDUP_TTL = 120.0  # 秒

    def _divination_cache_key(self, tool: str, uid: str, question: str) -> tuple:
        return (tool, uid, (question or "").strip()[:40])

    def _divination_cached(self, key: tuple) -> Optional[str]:
        """TTL 内命中返回上次结果文本；过期或未命中返回 None（顺带清理）。"""
        now = time.time()
        for k in [k for k, (ts, _) in self._divination_recent.items() if ts < now - self.DIVINATION_DEDUP_TTL]:
            self._divination_recent.pop(k, None)
        hit = self._divination_recent.get(key)
        if hit and hit[0] >= now - self.DIVINATION_DEDUP_TTL:
            return hit[1]
        return None

    def _divination_cache_set(self, key: tuple, text: str) -> None:
        self._divination_recent[key] = (time.time(), text)

    async def _maybe_split_long_text_for_wecom(self, event, resp) -> bool:
        """企微超长文本按字节切段自发送，返回 True 表示已处理（调用方应压空原回复）。

        根因：企业微信客服消息 content 上限 2048 **字节**（UTF-8），AstrBot 核心
        wecom `split_plain` 却按 2048 **字符** 切——中文每字 3 字节，2048 字符
        ≈ 6144 字节，切出的每段都超限被微信拒收（用户收不到回复）。

        方案：插件侧把文本切成每段 ≤1800 字节（< 2048 字节），同时每段字符数
        必然 < 2048，AstrBot 不再二次切分，整段发送 → 微信正常收到全部内容。

        仅对企微渠道生效（wecom / wecom_ai_bot）；其他平台交给 AstrBot 默认处理。
        """
        try:
            platform = ""
            try:
                platform = str(event.get_platform_name() or "").lower()
            except Exception:
                platform = ""
            if platform and not (
                "wecom" in platform or "wechat" in platform or "weixin" in platform
            ):
                return False

            text = ""
            if LLMResponse and isinstance(resp, LLMResponse):
                text = getattr(resp, "completion_text", "") or ""
            else:
                text = str(resp)
            text = text.strip()
            if not text:
                return False

            max_bytes = int(self._config.get("wecom_text_max_bytes", 1800))
            if max_bytes <= 0:
                max_bytes = 1800
            if len(text.encode("utf-8")) <= max_bytes:
                return False

            chunks = _split_utf8_bytes(text, max_bytes)
            if len(chunks) <= 1:
                return False
            logger.info(f"[mingbu] 企微长文本分段发送: {len(text.encode('utf-8'))}B → {len(chunks)} 段")
            for i, chunk in enumerate(chunks):
                await event.send(MessageChain([Plain(chunk)]))
                if i < len(chunks) - 1:
                    await asyncio.sleep(0.5)  # 避免发送过快触发微信限流
            # 已自行分段发送，压空核心回复防止 respond 阶段再发一份超长文本
            try:
                resp.completion_text = ""
            except Exception:
                pass
            return True
        except Exception as e:
            logger.error(f"[mingbu] 企微长文本分段失败(忽略, 走默认发送): {e}")
            logger.debug(traceback.format_exc())
            return False

    async def _tts_on_response(self, event: AstrMessageEvent, resp):
        """TTS 自动触发：用户点名 > 长文本 > 强情绪语义；中性短文本不触发（短→文字）。

        触发判定见 tts_should_trigger：长文本(>=阈值)必然触发；带明显情绪
        （joy/sad/excited/angry）的回复即使较短也读出来；中性短文本只给文字。
        另有最高优先级的「用户点名」路径（v4.8.128）：用户本条消息明确要求
        发语音（_user_requests_voice 命中）时，无视长度/情绪/随机判定与
        ``tts_auto_for_long_text`` 开关，本条回复强制转语音。

        开关语义（v4.8.100 修正）：
        - ``tts_enabled`` 是唯一总闸；
        - ``tts_auto_for_long_text`` 只控制「长文本触发」这条路径，关闭后长文本
          不再自动转语音，但情绪语义路径仍可触发（用户可能只想关长文本、保留
          情绪路由）——此前把它当总闸导致关掉长文本后情绪路由也失效。

        返回是否成功发出语音（True 时调用方应压住核心文本回复，避免既文本又语音）。
        """
        tts_cfg = load_tts_settings(self._config)
        if not tts_cfg.enabled:
            return False
        try:
            text = ""
            if LLMResponse and isinstance(resp, LLMResponse):
                text = getattr(resp, "completion_text", "") or ""
            else:
                text = str(resp)
            plain = _strip_machine_talk(re.sub(r"<[^>]+>", "", text))
            if not plain:
                return False
            threshold = tts_cfg.long_text_threshold
            # 用户点名（v4.8.128）：本条用户消息明确要语音 → 最高优先级强制触发，
            # 不受 tts_auto_for_long_text / 随机概率 / 情绪判定约束
            user_text = (getattr(event, "message_str", "") or "").strip()
            if _user_requests_voice(user_text):
                should, reason = True, "用户点名"
                logger.info(f"[mingbu.tts] 用户点名语音，强制触发: {user_text[:40]}")
            else:
                should, reason = tts_should_trigger(plain, threshold)
            if not should:
                return False
            # 长文本路径受 tts_auto_for_long_text 控制；情绪语义路径不受其限制
            if reason == "长文本" and not tts_cfg.auto_for_long_text:
                logger.debug("[mingbu.tts] 长文本触发被 tts_auto_for_long_text 关闭，跳过")
                return False
            logger.info(f"[mingbu.tts] 触发({reason}) 长度={len(plain)}（阈值={threshold}）")
            ok = await self._send_tts(event, plain)
            # 记录发送结果（供请求钩子按需回注给 LLM，防模型幻觉"语音失败"）
            try:
                await self.put_kv_data(
                    f"mingbu.last_tts_result:{event.get_sender_id()}",
                    {"ok": bool(ok), "reason": reason, "ts": time.time()},
                )
            except Exception as e:
                logger.debug(f"[mingbu] 记录 TTS 结果失败(忽略): {e}")
            return ok
        except Exception as e:
            logger.error(f"[mingbu] TTS 处理失败: {e}")
            logger.error(traceback.format_exc())
            return False

    async def _send_tts(self, event: AstrMessageEvent, text: str):
        """调用 TTS provider 生成语音并发送（可选情绪音色路由）。"""
        try:
            from .provider_router import (
                get_tts_provider,
            )

            # v4.9.0：TTS 配置统一经 TTSSettings 读取——默认值 / max_chunk_chars
            # 钳制 / voice_map 解析全部收敛于 tts_config.load_tts_settings，
            # 消除散读与默认值漂移。v4.8.110 钳制语义不变：加载时已按
            # TTS_MAX_CHUNK_CHARS 上限钳制（45007 企微吞错，源头预防）。
            tts_cfg = load_tts_settings(self._config)
            max_chars = tts_cfg.max_chunk_chars
            logger.info(
                f"[mingbu.tts] 开始合成: model={tts_cfg.model or '默认'} "
                f"voice={tts_cfg.voice or '默认'} provider_tts={tts_cfg.provider_id or '默认'} "
                f"max_chunk={max_chars}"
            )

            clean_text = re.sub(r"<[^>]+>", "", text).strip()
            if not clean_text:
                logger.warning("[mingbu.tts] 清洗后文本为空，跳过")
                return False
            chunks = _split_tts_chunks(clean_text, max_chars)
            logger.info(f"[mingbu.tts] 文本切分为 {len(chunks)} 段（单段上限 {max_chars} 字）")

            # v4.8.141 起：TTS 合成完全走 AstrBot TTS provider，插件不再持有直连
            # 凭据（移除原「直连优先」分支）。音色试听/连通性测试属面板独立工具，
            # 与运行时合成路径解耦，仍可用请求体临时凭据调用。
            tts_provider = await get_tts_provider(self.context, tts_cfg.provider_id)
            if not tts_provider:
                logger.error(
                    "[mingbu.tts] 未找到可用的 AstrBot TTS provider；"
                    "请在 AstrBot 主设置里配置 TTS 服务，或在命灵面板为 TTS 指定服务商（provider_tts）"
                )
                return False

            # 选定音色：情绪路由优先，其次 tts_voice，否则接口默认
            voice = None
            if tts_cfg.emotion_routing_enabled:
                try:
                    emo = detect_emotion(clean_text)
                    logger.info(f"[mingbu.tts] 检测情绪: {emo}")
                    voice = tts_cfg.voice_for_emotion(emo)
                except Exception as e:
                    logger.warning(f"[mingbu.tts] 情绪路由失败(忽略): {e}")
            if voice is None and tts_cfg.voice:
                voice = tts_cfg.voice

            # 安全探测 get_audio 是否接受 voice 参数（Genie 等 provider 不接受该关键字）
            try:
                accepts_voice = "voice" in inspect.signature(tts_provider.get_audio).parameters
            except (ValueError, TypeError):
                accepts_voice = False

            sent = 0
            for i, chunk in enumerate(chunks):
                try:
                    if accepts_voice and voice:
                        audio = await tts_provider.get_audio(chunk, voice=voice)
                    else:
                        audio = await tts_provider.get_audio(chunk)
                except Exception as e:
                    logger.warning(f"[mingbu.tts] 分段 {i+1} 合成失败: {e}")
                    continue
                if not audio:
                    continue
                logger.info(f"[mingbu.tts] Provider 合成成功[{i+1}/{len(chunks)}]，发送语音")
                send_result = await _send_voice_with_backoff(event, audio)
                if send_result is True:
                    sent += 1
                elif send_result == "too_long":
                    # Provider 路径的 audio 是已合成文件，无法二分重切，记为失败
                    logger.warning(f"[mingbu.tts] 分段 {i+1} 超微信 60s 上限(45007)，该段未能发送")
                # 段间留间隔，降低微信客服 95001 限流概率
                if i < len(chunks) - 1:
                    await asyncio.sleep(TTS_SEGMENT_GAP)
            if sent == 0:
                logger.warning("[mingbu.tts] Provider 未成功发送任何语音")
            elif sent < len(chunks):
                logger.warning(f"[mingbu.tts] Provider 部分成功：{sent}/{len(chunks)} 段")
            return sent > 0
        except Exception as e:
            logger.error(f"[mingbu.tts] 发送 TTS 失败: {e}")
            logger.error(traceback.format_exc())
            return False

    # _tts_voice_for 已删除（v4.9.0）：voice_map JSON 解析收敛至
    # tts_config.load_tts_settings（加载时解析一次）+ TTSSettings.voice_for_emotion，
    # 消除「同一配置散读 + 每次调用重复 json.loads」。

    # ---- 消息防抖（私聊） ----

    @_message_handler(_EMT_PRIVATE)
    async def _on_friend_message_debounce(self, event: AstrMessageEvent):
        """私聊消息进入防抖缓冲区，合并连续补话。"""
        debounce_ms = 600  # 默认 600ms
        uid = self._extract_uid(event)
        msg_obj = getattr(event, "message_obj", None)
        text = getattr(event, "message_str", "") or ""
        msg_id = getattr(msg_obj, "message_id", "") if msg_obj else ""

        buf = self._debounce_buffer.setdefault(uid, {"texts": [], "ids": [], "event": event, "timer": None})
        buf["texts"].append(text)
        if msg_id:
            buf["ids"].append(msg_id)
        buf["event"] = event  # 保留最新事件

        if buf["timer"]:
            try:
                buf["timer"].cancel()
            except Exception:
                pass

        async def _flush():
            await asyncio.sleep(debounce_ms / 1000.0)
            await self._process_debounced(uid)

        buf["timer"] = spawn(_flush(), name="vision_debounce_flush")

    async def _process_debounced(self, uid: str):
        """合并窗口到期后处理缓冲：把连续补话合并为一条观察写入记忆。"""
        buf = self._debounce_buffer.pop(uid, None)
        if not buf or not buf["texts"]:
            return
        merged_text = " ".join(buf["texts"]).strip()
        if not merged_text:
            return
        logger.debug(f"[mingbu] 防抖合并: {merged_text[:80]}")
        if self.memory:
            try:
                await self.memory.add(uid, "OBSERVATION", f"[连续补话合并] {merged_text}")
            except Exception as e:
                logger.debug(f"[mingbu] 防抖记忆写入失败(忽略): {e}")

    def _extract_uid(self, event: AstrMessageEvent) -> str:
        msg_obj = getattr(event, "message_obj", None)
        sender = getattr(msg_obj, "sender", None)
        if sender:
            return str(getattr(sender, "user_id", "") or getattr(sender, "id", ""))
        return getattr(event, "session_id", "unknown")

    # ---- 图片转述增强 ----

    @_message_handler(_EMT_ALL)
    async def _on_image_vision(self, event: AstrMessageEvent):
        """检测图片并留下简短视觉摘要（记忆）。"""
        if not self._config.get("image_vision_enabled", False):
            return
        try:
            msg_obj = getattr(event, "message_obj", None)
            chain = getattr(msg_obj, "message", None)
            if not chain:
                return
            uid = self._extract_uid(event)
            image_urls = []
            for comp in chain:
                comp_type = getattr(comp, "type", "")
                if comp_type == "Image":
                    url = getattr(comp, "url", "") or getattr(comp, "file", "")
                    if url:
                        image_urls.append(url)
            if not image_urls:
                return
            # 记录到记忆：用户发了一张图
            if self.memory:
                desc = f"[图片] 用户发送了 {len(image_urls)} 张图片"
                await self.memory.add(uid, "OBSERVATION", desc)
                # 异步调用视觉模型生成简短摘要（不阻塞回复）
                spawn(self._describe_images(uid, image_urls), name="image_vision_describe")
        except Exception as e:
            logger.debug(f"[mingbu] 图片转述失败(忽略): {e}")

    async def _summarize_image_urls(self, uid: str, image_urls: List[str]) -> str:
        """调用视觉模型生成当前图片摘要（纯文本）。

        供两条路径复用：
        - `_describe_images`：异步写记忆（后台，不阻塞回复）；
        - 请求钩子 `_inject_current_image_hint`：**同步**生成摘要注入请求上下文，
          让主模型直接拿到图内容、不必自己调文件读取工具（根治 AstrBot 主 agent
          读图路径错误→"卡死"→回复"发过来吧"的链路问题）。

        返回摘要文本；失败/无凭据返回空串。
        """
        try:
            prompt = (
                "用第一人称、简短的一句话描述这张图片的内容和氛围。"
                "只输出描述本身，不要解释。"
            )
            req = ProviderRequest(
                prompt=prompt,
                image_urls=image_urls,
                session_id=f"mingbu_vision_{uid}",
            )
            # v4.8.141 起完全走 AstrBot Provider 路由（provider_image_vision →
            # 默认 Provider），插件不再持有直连视觉凭据。
            model = self._config.get("image_vision_model", "")
            if self.provider_router:
                resp = await self.provider_router.llm_request(
                    "image_vision", req, model=model
                )
                return getattr(resp, "completion_text", "") or getattr(resp, "response_text", "")
            return ""
        except Exception as e:
            logger.debug(f"[mingbu] 视觉摘要失败(忽略): {e}")
            return ""

    async def _describe_images(self, uid: str, image_urls: List[str]):
        try:
            text = await self._summarize_image_urls(uid, image_urls)
            if text and text.strip() and self.memory:
                # 摘要带时间戳标记，帮助区分新旧图片（历史缺陷：无标记时旧图摘要
                # 与新图混在上下文，模型把新图识别成旧图）
                ts = now_bj().strftime("%m-%d %H:%M")
                await self.memory.add(uid, "OBSERVATION", f"[看图 {ts}] {text.strip()}")
        except Exception as e:
            logger.debug(f"[mingbu] 视觉摘要失败(忽略): {e}")

    # ---- 语音转写（STT） ----

    @_message_handler(_EMT_ALL)
    async def _on_voice_message(self, event: AstrMessageEvent):
        """检测语音(Record)消息并转写为文本注入对话；未启用时优雅跳过。

        STT 完全走 AstrBot STT provider：仅当 stt_enabled 显式为 true 才启用，
        且需在 AstrBot 主设置里配好 STT provider。插件不再持有直连凭据。
        """
        cfg = self._config or {}
        from .stt import is_stt_enabled
        if not is_stt_enabled(cfg):
            return
        try:
            from astrbot.api.message_components import Plain

            from .stt import transcribe_event_voice

            transcript = await transcribe_event_voice(
                event, self.context, self._config.get("provider_stt", "")
            )
            if not transcript:
                return
            # 注入为可识别文本，让后续 LLM 看到
            note = f"[语音转写] {transcript}"
            msg_obj = getattr(event, "message_obj", None)
            chain: Optional[list] = getattr(msg_obj, "message", None)
            if chain is not None:
                try:
                    chain.append(Plain(note))
                except Exception:
                    pass
            # 关键：把消息链里的 Record(音频)组件移除——语音已转成文字，
            # 音频分片若继续留在链里，AstrBot 组装请求时会给多模态主模型
            # (如 Qwen3-VL-32B-Thinking)发 audio 分片，而 SiliconFlow 网关
            # 只放行 text+image_url → 主模型 400 → 被迫每轮落 fallback。
            try:
                from .stt import strip_record_components
                kept = strip_record_components(chain)
                if chain is not None and isinstance(kept, list) and len(kept) != len(chain):
                    chain[:] = kept
            except Exception:
                pass
            orig = getattr(event, "message_str", "") or ""
            try:
                event.message_str = (orig + "\n" + note) if orig else note
            except Exception:
                pass
            # 记录到记忆（观测）
            if self.memory:
                uid = self._extract_uid(event)
                await self.memory.add(uid, "OBSERVATION", f"[语音] {transcript}")
        except Exception as e:
            logger.debug(f"[mingbu] 语音转写处理失败(忽略): {e}")

    # ---- 合并消息适配 ----

    @_message_handler(_EMT_ALL)
    async def _on_merged_forward(self, event: AstrMessageEvent):
        """检测合并转发消息，转述为记忆。"""
        try:
            msg_obj = getattr(event, "message_obj", None)
            chain = getattr(msg_obj, "message", None)
            if not chain:
                return
            uid = self._extract_uid(event)
            for comp in chain:
                comp_type = getattr(comp, "type", "")
                if comp_type in ("Node", "Nodes", "Forward"):
                    nodes = getattr(comp, "nodes", []) or [comp]
                    summaries = []
                    for node in nodes:
                        sender = getattr(node, "sender", "某人")
                        content = getattr(node, "content", "")
                        if content:
                            summaries.append(f"{sender}: {content}")
                    if not summaries:
                        return
                    # 异步生成摘要，不阻塞消息管线
                    spawn(self._summarize_merged_forward(uid, summaries), name="merged_forward_summarize")
        except Exception as e:
            logger.debug(f"[mingbu] 合并消息适配失败(忽略): {e}")

    async def _summarize_merged_forward(self, uid: str, summaries: List[str]):
        """把合并转发节点转述成一句摘要记忆（有可用 Provider 时走 LLM）。"""
        try:
            text = "\n".join(summaries[:10])
            model = self._config.get("merged_forward_model", "")
            prompt = (
                "用一句自然的话概括这段合并转发对话的要点（谁在聊什么），"
                "不要逐条复述，控制在 40 字以内：\n" + text
            )
            # v4.8.141 起完全走 AstrBot Provider 路由，插件不再持有直连凭据
            if self.provider_router:
                req = ProviderRequest(prompt=prompt, session_id=f"mingbu_merge_{uid}")
                resp = await self.provider_router.llm_request(
                    "merged_forward", req, model=model
                )
                summary = getattr(resp, "completion_text", "") or getattr(resp, "response_text", "")
            else:
                summary = ""
            if summary and self.memory:
                await self.memory.add(uid, "OBSERVATION", f"[转发摘要] {summary.strip()}")
            elif self.memory:
                # 未配置 LLM 时回退：保留结构化原始记录
                await self.memory.add(uid, "OBSERVATION", f"[转发记录]\n{text}")
        except Exception as e:
            logger.debug(f"[mingbu] 合并转发转述失败(忽略): {e}")

    # ---- 撤回增强 ----

    @_message_handler(_EMT_ALL)
    async def _on_message_recall(self, event: AstrMessageEvent):
        """监听撤回事件，取消尚未发出的回复。"""
        try:
            msg_obj = getattr(event, "message_obj", None)
            raw = getattr(msg_obj, "raw_message", None)
            if not raw:
                return
            # 撤回事件通常带有 recall 标记或 post_type=notice/notice_type=group_recall/friend_recall
            raw_dict = raw if isinstance(raw, dict) else {}
            post_type = raw_dict.get("post_type", "")
            notice_type = raw_dict.get("notice_type", "")
            if post_type == "notice" and "recall" in notice_type:
                recalled_id = str(raw_dict.get("message_id", ""))
                # 取消待发送的回复
                for msg_id, info in list(self._pending_replies.items()):
                    if info.get("reply_to") == recalled_id:
                        try:
                            info.get("task", None) and info["task"].cancel()
                        except Exception:
                            pass
                        self._pending_replies.pop(msg_id, None)
                        logger.info(f"[mingbu] 撤回拦截 -> 取消回复 {msg_id}")
        except Exception as e:
            logger.debug(f"[mingbu] 撤回处理失败(忽略): {e}")
