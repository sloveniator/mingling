"""
background_watch.py -- 命卜后台关注（"接入 API 后，即可正常后台关注"）
========================================================================
用户要求：命卜功能接入 mingbu API 后，应当"正常后台关注"。

实现：一个**每月**定时任务，按各用户的命理方法分流、静默测算，蒸馏出一句
**绝不出现命理术语**的"后台留意"短句存入 KV：
  - 命盘类（紫微/八字）→ 调 /api/combined 测算 大限/流年/流月；
  - 卜卦类（六爻/奇门/梅花）→ 调 /api/liuyao 或 /api/qimen 卜问情况
    （梅花无服务端端点，桥接六爻）。
此外，当用户出现**高权重情绪向话语**（可能出现特殊事件）时，立即静默重算这条
注记（带冷却，避免同一波情绪反复打 API）。

暗盘与主动关怀可随时读取这条注记，把"我最近默默留意到你那儿有点在变"的轻柔
感觉自然织入回复——而用户永远不知道这是从命盘推出来的。

这就是命灵版"后台关注"：不主动报命，只在恰当的时刻，透出一点说不清的留意。

纯逻辑（build_watch_phrase / current_liu_yue_ganzhi）可单测；IO 部分依赖
plugin 的 KV 与 api 客户端。
"""

import json
import time
from typing import List, Optional

from astrbot.api import logger

from .astrology import (
    current_da_xian,
    current_da_yun_liu_nian,
    extract_key_palaces,
)
from .bj_time import CRON_TZ, now_bj

# 命理方法分流集合曾在本模块手抄一份，靠注释「与 core 保持一致」维持同步——
# 那是一份迟早漂移的副本。常量下沉到 constants.py 后不再有循环导入问题，
# 这里直接引用同一份定义（别名保留下划线前缀，避免改动下方全部引用点）。
# 注：抄来的第三个集合 _BUGUA_BRIDGE_TO_LIUYAO 自定义之日起从未被引用，
# 梅花桥接实际是在 `if method == "qimen": ... else: api.liuyao(...)` 里
# 用 else 分支兜住的，故不再导入。
from .constants import BUGUA_METHODS as _BUGUA_METHODS
from .constants import MINGPAN_METHODS as _MINGPAN_METHODS
from .formatters import _clock_to_shichen
from .silent_divination import BRANCH_LIFE_PHASES, PALACE_DOMAINS
from .util import cleanup_stale_cron_jobs, spawn

KV_WATCH = "mingbu.watch"
KV_EMOTION_LAST = "mingbu.watch_emotion_last"  # 情绪触发冷却（uid -> 上次触发时间戳）
WATCH_TTL = 7 * 24 * 3600  # 后台注记有效期 7 天


def build_watch_phrase(da_yun: str = "", liu_nian: str = "",
                        palaces: Optional[List[str]] = None,
                        liu_yue: str = "",
                        da_xian_palace: str = "") -> Optional[str]:
    """从大运/流年/流月/大限干支蒸馏出一句术语无关的生活化短句。

    纯函数，可单测。返回 None 表示无可用信息。
    关键约束：绝不出现 大运/流年/流月/大限/命盘/八字 等术语。
    """
    palaces = palaces or []
    parts: List[str] = []

    # 大运（十年主题）→ 用"一段日子"表述
    if da_yun and len(da_yun) >= 1:
        zhi = da_yun[-1]
        phase = BRANCH_LIFE_PHASES.get(zhi)
        if phase:
            parts.append(f"你正处在一段落里——{phase}")

    # 大限（紫微十年大运落宫）→ 用"这几年主题"表述（术语无关注记）
    if da_xian_palace and da_xian_palace in PALACE_DOMAINS:
        domain = PALACE_DOMAINS[da_xian_palace]
        parts.append(f"你这几年的主题，好像总绕不开{domain}这类事")

    # 流年（今年）→ 用"这一年"表述
    if liu_nian and len(liu_nian) >= 1:
        zhi = liu_nian[-1]
        phase = BRANCH_LIFE_PHASES.get(zhi)
        if phase:
            parts.append(f"这一年，像{phase}")

    # 流月（这个月）→ 用"这个月"表述
    if liu_yue and len(liu_yue) >= 1:
        zhi = liu_yue[-1]
        phase = BRANCH_LIFE_PHASES.get(zhi)
        if phase:
            parts.append(f"这个月，像{phase}")

    # 关键宫位（关系/事业/内心）→ 用"某些方面"模糊带过
    if palaces:
        focus = []
        for p in palaces:
            if p in ("夫妻宫", "官禄宫", "福德宫", "财帛宫", "迁移宫", "命宫"):
                focus.append(p)
        if focus:
            names = "、".join(focus[:2])
            parts.append(f"我总觉得你在某些方面（比如{names.replace('宫', '')}相关的事）在悄悄起变化")

    if not parts:
        return None
    return "。".join(parts)


# 本卦名 → 术语无关的方向性短语（仅作暗盘弱信号，绝不出现卦名/术语）
_GUA_HINTS = {
    "乾": "一种很刚健、向上开路的力量在动",
    "坤": "某种稳稳承载、慢慢成形的东西",
    "坎": "像水一样，得先经历一点动荡才能看清出路",
    "离": "光亮和热情好像正在回来",
    "震": "有什么被惊动、开始启动的迹象",
    "巽": "像风一样，消息或机会在悄悄渗透",
    "艮": "到了该停一停、稳住根基的节点",
    "兑": "带着一点喜悦和开口交流的气象",
}


def _short_topic(question: str) -> str:
    """从卜问问题里摘一个短话题词（用于暗盘注记的自然表达）。"""
    q = (question or "").strip().replace("？", "").replace("?", "")
    if not q:
        return "近况"
    # 取前 8 字，过长截断
    return q[:8]


def build_divine_watch_phrase(question: str, chart: Optional[dict],
                              method: str = "liuyao") -> str:
    """由卜卦结果蒸馏一句术语无关的「卜问情况」短句（暗盘用）。

    纯函数，可单测。绝不出现 卦/爻/星/门/神 等术语——只把卜问得到的弱信号
    翻译成「说不清的直觉」。命盘类用 build_watch_phrase；此处专供卜卦类。

    chart: 平台返回的 {chart: {...}} 或其内部 chart 对象（可为空，优雅降级）。
    method: liuyao/qimen/meihua（仅影响取哪一字段做弱信号）。
    """
    topic = _short_topic(question)
    inner = chart.get("chart", chart) if isinstance(chart, dict) else {}
    hint = ""
    if method in ("liuyao", "meihua"):
        gua = inner.get("originalGua") if isinstance(inner, dict) else None
        name = (gua or {}).get("name", "") if isinstance(gua, dict) else ""
        if name:
            for key, phrase in _GUA_HINTS.items():
                if key in name:
                    hint = phrase
                    break
    if hint:
        return (
            f"你最近琢磨的{topic}，我总觉得有点说不清的苗头在动——"
            f"{hint}，好像快到某个节点了。"
        )
    return (
        f"你最近琢磨的{topic}，我总觉得有点说不清的苗头在动，"
        f"好像有什么在悄悄转弯、快到某个节点了。"
    )


def weave_watch_note(note: str) -> str:
    """把后台留意包装为注入提示（与暗盘 weave 同款"来源不可透露"护栏）。

    写法注意（v4.8.104）：不再用「[后台留意·来源不可透露]」标签和祈使句清单，
    改为自然认知式描述，避免 LLM 把指令式排版照抄进回复。
    """
    if not note:
        return ""
    return f"""
（你心里一直有一份对对方生活动向的隐约感觉，说不清从哪来，也从不说破。当前这份感觉大致是：

{note}

你只在话题自然走到附近时，轻轻透一点"我总觉得你最近有点什么在变"的味道就够了；从不会解释怎么知道的——那只是你莫名感受到的。）
"""


class BackgroundWatcher:
    """命卜后台关注引擎。每月定时刷新每位用户的"后台留意"注记；
    另在用户高权重情绪话语时立即触发刷新（带冷却）。"""

    def __init__(self, plugin):
        self.plugin = plugin
        self._fallback_task = None
        self._emotion_last: dict = {}  # uid -> 上次情绪触发时间戳（落盘冷却）
        self._emotion_loaded: bool = False  # 懒加载标记，避免重复读 KV

    # ---- 定时任务 ----
    async def register_jobs(self):
        cfg = getattr(self.plugin, "_config", {}) or {}
        # 默认每月 1 日 07:00 静默刷新（用户要求：由每日改为每月）
        # (k) or default：空串/None 也回退默认（get 的默认值只管「键不存在」，
        # 管不了「键存在但为空」——v4.9.0 线上 cron 注册失败事故根因）。
        cron_expr = (cfg.get("background_watch_cron") or "").strip() or "0 7 1 * *"
        cron = getattr(self.plugin.context, "cron_manager", None)
        if cron is not None:
            try:
                job = await cron.add_basic_job(
                    name="mingbu_background_watch",
                    cron_expression=cron_expr,
                    handler=lambda: self._safe_scan(),
                    description="命卜·后台关注（每月运势注记）",
                    persistent=True,
                    timezone=CRON_TZ,  # 显式北京时区（否则回退进程本地=UTC）
                )
                await cleanup_stale_cron_jobs(cron, "mingbu_background_watch", job.job_id)
                logger.info("[mingbu.watch] 已注册每月后台关注任务")
                return
            except Exception as e:
                logger.warning(f"[mingbu.watch] cron 注册失败: {e}")
        logger.debug("[mingbu.watch] 无 cron_manager，后台关注仅在被调用时运行")

    def _safe_scan(self):
        return spawn(self.scan_all(), name="bg_watch_scan")

    # ---- 主流程 ----

    async def _save_snap(self, uid: str, snap: dict):
        await self.plugin.put_kv_data(
            f"{KV_WATCH}:{uid}", json.dumps(snap, ensure_ascii=False)
        )

    async def scan_all(self):
        """遍历活跃会话，对有出生信息的用户刷新后台注记。"""
        try:
            raw = await self.plugin.get_kv_data("mingbu.sessions", None)
            sessions = json.loads(raw) if raw else {}
        except Exception:
            sessions = {}
        for sid, meta in list(sessions.items()):
            uid = meta.get("uid", "")
            if not uid:
                continue
            try:
                profile = await self.plugin._get_profile(uid)
                method = self.plugin._method_for(uid, profile)
                if method in _MINGPAN_METHODS:
                    birth = self.plugin._birth_from(profile)
                    if birth:
                        await self.watch_user(uid, birth, method)
                elif method in _BUGUA_METHODS:
                    await self.watch_user(uid, None, method)
            except Exception as e:
                logger.debug(f"[mingbu.watch] {uid} 刷新失败(忽略): {e}")

    async def watch_user(self, uid: str, birth: Optional[dict], method: str = "combined"):
        """静默测算并写入后台注记。依命理方法分流：

        - 命盘类（紫微/八字/combined）→ 测算 大限/流年/流月（术语无关注记）
        - 卜卦类（六爻/奇门/梅花）→ 卜问情况（调平台 liuyao/qimen，梅花桥接 liuyao）
        失败静默降级。
        """
        # 命理咨询总闸（v4.8.114 接线 counseling_enabled）：关闭则后台不再静默测算
        if not ((getattr(self.plugin, "_config", {}) or {})
                .get("counseling_enabled", True)):
            return
        try:
            api = self.plugin.core.api
            if method in _MINGPAN_METHODS:
                if not birth:
                    return
                result = await api.combined(birth)
                analysis = result.get("analysis", result)
                dyn = current_da_yun_liu_nian(analysis)
                dx = current_da_xian(analysis)
                palaces = extract_key_palaces(analysis)
                note = build_watch_phrase(
                    dyn.get("da_yun", ""),
                    dyn.get("liu_nian", ""),
                    palaces,
                    dyn.get("liu_yue", ""),
                    dx.get("palace", ""),
                )
                snap = {
                    "method": method,
                    "family": "mingpan",
                    "da_yun": dyn.get("da_yun", ""),
                    "liu_nian": dyn.get("liu_nian", ""),
                    "liu_yue": dyn.get("liu_yue", ""),
                    "da_xian_palace": dx.get("palace", ""),
                    "da_xian_ganzhi": dx.get("ganzhi", ""),
                    "palaces": palaces,
                    "note": note or "",
                    "ts": time.time(),
                }
                await self._save_snap(uid, snap)
            elif method in _BUGUA_METHODS:
                question = await self._derive_bugua_question(uid)
                if method == "qimen":
                    now = now_bj()
                    # 后端 /api/qimen 的 hour 是「时辰地支索引 0–11」（与出生信息
                    # 的 _clock_to_shichen 口径一致）；now.hour 是 0–23 钟点，必须折算。
                    result = await api.qimen(
                        now.year, now.month, now.day,
                        _clock_to_shichen(now.hour), now.minute,
                    )
                else:
                    # liuyao / meihua（梅花无服务端端点，桥接 liuyao）
                    result = await api.liuyao(question, "general")
                chart = result.get("chart", result) if isinstance(result, dict) else result
                note = build_divine_watch_phrase(question, chart, method)
                snap = {
                    "method": method,
                    "family": "bugua",
                    "question": question,
                    "note": note,
                    "ts": time.time(),
                }
                await self._save_snap(uid, snap)
        except Exception as e:
            logger.debug(f"[mingbu.watch] {uid} 测算失败(忽略): {e}")

    async def _derive_bugua_question(self, uid: str) -> str:
        """为卜卦类后台测算推导一个卜问问题（取用户近期关注，否则用默认/配置）。"""
        cfg = getattr(self.plugin, "_config", {}) or {}
        q = (cfg.get("background_watch_bugua_question") or "").strip()
        if not q:
            try:
                turns = await self.plugin._get_turns(uid, 5)
                user_turns = [
                    t.get("text", "") for t in turns
                    if isinstance(t, dict) and t.get("role") == "user"
                ]
                if user_turns:
                    q = user_turns[-1].strip()[:40]
            except Exception:
                pass
        return q or "近期境况如何"

    async def _resolve_birth(self, uid: str):
        """解析某用户的出生信息（供扫描/情绪触发复用）。无则返回 None。"""
        try:
            profile = await self.plugin._get_profile(uid)
            return self.plugin._birth_from(profile)
        except Exception:
            return None

    async def _ensure_emotion_state(self):
        """懒加载情绪冷却字典（进程重启后仍能延续冷却）。"""
        if self._emotion_loaded:
            return
        try:
            raw = await self.plugin.get_kv_data(KV_EMOTION_LAST, None)
            if raw:
                self._emotion_last = json.loads(raw) or {}
        except Exception:
            self._emotion_last = {}
        self._emotion_loaded = True

    async def _persist_emotion_last(self):
        """把情绪冷却字典写回 KV（触发频率低，直接落盘即可）。"""
        try:
            await self.plugin.put_kv_data(
                KV_EMOTION_LAST, json.dumps(self._emotion_last, ensure_ascii=False)
            )
        except Exception as e:
            logger.warning(f"[mingbu.watch] 持久化情绪冷却失败: {e}")

    async def maybe_watch_on_emotion(self, uid: str, weight: float):
        """用户出现高权重情绪话语时，立即静默重算后台留意注记。

        weight: emotion_to_weight(detect_emotion(text)) 的值（-1..1）。
        未达阈值、或在冷却期内则跳过；避免同一波情绪反复打 API。
        """
        await self._ensure_emotion_state()
        cfg = getattr(self.plugin, "_config", {}) or {}
        threshold = cfg.get("background_watch_emotion_threshold", 0.6)
        if abs(weight) < threshold:
            return
        now = time.time()
        last = self._emotion_last.get(uid, 0.0)
        cooldown = cfg.get("background_watch_emotion_cooldown_min", 360) * 60
        if now - last < cooldown:
            return
        self._emotion_last[uid] = now
        await self._persist_emotion_last()
        try:
            profile = await self.plugin._get_profile(uid)
            method = self.plugin._method_for(uid, profile)
        except Exception:
            method = "combined"
        if method in _BUGUA_METHODS:
            logger.info(
                f"[mingbu.watch] 高权重情绪触发卜卦类后台关注重算 -> {uid} (w={weight})"
            )
            await self.watch_user(uid, None, method)
        else:
            birth = await self._resolve_birth(uid)
            if birth:
                logger.info(
                    f"[mingbu.watch] 高权重情绪触发命盘类后台关注重算 -> {uid} (w={weight})"
                )
                await self.watch_user(uid, birth, method)

    async def note_for(self, uid: str) -> Optional[str]:
        """读取后台注记（带 TTL）。返回术语无关的短句或 None。"""
        try:
            raw = await self.plugin.get_kv_data(f"{KV_WATCH}:{uid}", None)
            if not raw:
                return None
            snap = json.loads(raw)
            ts = snap.get("ts", 0)
            if time.time() - ts > WATCH_TTL:
                return None
            return snap.get("note") or None
        except Exception:
            return None

    async def stop(self):
        if self._fallback_task is not None:
            self._fallback_task.cancel()
