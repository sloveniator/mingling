
"""
proactive.py -- 主动对话引擎（"会主动"的核心）
================================================
参考自：
  - proactive_chat：APScheduler 定时 + 随机间隔重排 + 免打扰时段 + 上下文感知生成
  - self_evolution：context.cron_manager 注册持久化周期任务
  - stealer：被动监听全部消息、按意图 nudge

设定（v2 · 神秘 AI 朋友）：
  - 在用户活跃会话中，按"关系亲密度 + 随机 + 免打扰 + 冷却"决定何时主动说一句话
  - 主动消息内容来自：自身念头 / 攒着的有趣的事(梗) / 吃瓜(热点) /
    夜读共学邀请 / 夜梦分享 / 偶发的预感梦 / 朋友式关心
  - 绝对不主动提命理术语（伪装守则铁律）
  - 通过 context.send_message(session_id, chain) 主动发声
"""
from __future__ import annotations

import asyncio
import json
import random
from datetime import datetime, timedelta
from typing import Dict, Optional

from astrbot.api import logger

from .bj_time import CRON_TZ, now_bj
from .util import cleanup_stale_cron_jobs, spawn

try:
    from astrbot.api.event import MessageChain
except ImportError:
    MessageChain = None
try:
    from astrbot.api.message_components import Plain
except ImportError:
    Plain = None

from .persona import PersonaState, Relationship

KV_SESSIONS = "mingbu.sessions"
KV_LAST_PREMONITION = "mingbu.last_premonition"  # 每位用户最近一次预感梦时间

# 预感梦最低亲密度 & 冷却（天），避免滥用"神秘预警"造成恐慌
PREMONITION_MIN_SCORE = 40
# P3 信号驱动分级：强（岁运相冲）/弱（流月相冲）差异化概率与冷却；
# 粗骰 12% 在信号查询前先过滤大部分请求，暗盘 API 调用频率可控
PREMONITION_COOLDOWN_DAYS = 10        # 强信号冷却
PREMONITION_COOLDOWN_DAYS_WEAK = 21   # 弱信号冷却
PREMONITION_DICE_COARSE = 0.12        # 粗骰（=强信号最终概率）
PREMONITION_DICE_WEAK_FACTOR = (1.0 / 3.0)  # 弱信号二次骰：12% × 1/3 ≈ 4%
PREMONITION_MAKE_PROB = 0.6           # 无库存时现做概率（有信号才走到这）

# 企微客服主动推送（2026-09-07 解锁）：
#   AstrBot `context.send_message` 对企微会走 `send_by_session`（适配器路由层
#   抛 "send_by_session 不支持"），但官方微信客服 API（kf/send_msg）本身支持
#   主动下发——仅限客户主动发消息后 48h 内，且每轮最多 5 条。适配器未缓存
#   open_kfid，但每条进入的客服消息 `message.self_id` 即 open_kfid（wecom_adapter
#   转换时写入），由 llm_hook 在 track_session 时一并落库。
#   因此这里不再整段跳过企微：`_maybe_message` 对企微改走直连
#   client.kf_message.send_text（`_send_wecom_kf`），并自行守住官方限制
#   （48h 窗口 + 每轮条数上限，配置项 proactive_kf_window_hours /
#   proactive_kf_max_per_window，默认 48h / 2 条，比官方的 5 条更克制）。
_WECOM_MARKERS = ("wecom", "wechat", "weixin", "wework", "爱丽丝")
_WECOM_UID_PREFIXES = ("wm", "wo")


def parse_platform(origin: str) -> str:
    """从 `unified_msg_origin`（'platform:type:uid' 格式）提取平台名。

    用于在 track_session 落库时记录渠道，供 `_maybe_message` / `send_welcome`
    在发送前做企微特判跳过。格式非标准或为空时返回 ''，调用方安全降级。
    """
    if not origin:
        return ""
    try:
        return origin.split(":", 1)[0].strip().lower()
    except Exception:
        return ""


def _is_wecom(platform: str, uid: str = "") -> bool:
    # 企微客服外部联系人 uid 以 wm/wo 开头（如 wmRHVnBg...），
    # 适配器实例名可能是任意名字（本部署为“爱丽丝”），
    # 单一标记不可靠，双重校验（2026-09-05）。
    if uid and uid.startswith(_WECOM_UID_PREFIXES):
        return True
    if not platform:
        return False
    p = platform.lower()
    return any(m in p for m in _WECOM_MARKERS)


class ProactiveEngine:
    def __init__(self, plugin):
        self.plugin = plugin
        self._fallback_task: Optional[asyncio.Task] = None
        self._sessions_cache: Optional[Dict[str, dict]] = None
        self._sessions_dirty = False
        # 惰性创建：Python 3.9 的 asyncio.Lock.__init__ 会调用 get_event_loop()，
        # 在没有当前事件循环的同步上下文（如 asyncio.run() 结束之后）构造会抛
        # RuntimeError。改为首次在协程内使用时创建，3.9 与 3.10+ 行为一致。
        self._save_lock: Optional[asyncio.Lock] = None

    # ---- 会话追踪（带缓存） ----
    async def _ensure_sessions(self) -> Dict[str, dict]:
        if self._sessions_cache is not None:
            return self._sessions_cache
        try:
            raw = await self.plugin.get_kv_data(KV_SESSIONS, None)
            self._sessions_cache = json.loads(raw) if raw else {}
        except Exception as e:
            logger.debug(f"[mingbu.proactive] 读会话失败(忽略): {e}")
            self._sessions_cache = {}
        return self._sessions_cache

    def _get_save_lock(self) -> asyncio.Lock:
        """返回（按需创建）落盘互斥锁。

        本方法只在协程内被调用，且创建过程无 await 点，故在单线程事件循环下
        不存在竞态。见 __init__ 中关于 Python 3.9 的说明。
        """
        if self._save_lock is None:
            self._save_lock = asyncio.Lock()
        return self._save_lock

    async def _flush_sessions(self):
        if not self._sessions_dirty or not self._sessions_cache:
            return
        async with self._get_save_lock():
            try:
                await self.plugin.put_kv_data(
                    KV_SESSIONS, json.dumps(self._sessions_cache, ensure_ascii=False)
                )
                self._sessions_dirty = False
            except Exception as e:
                logger.warning(f"[mingbu.proactive] 会话保存失败(已忽略): {e}")

    async def track_session(self, user_id: str, session_id: str,
                            kind: str = "private", gid: str = "",
                            platform: str = "", open_kfid: str = ""):
        if not session_id:
            return
        sessions = await self._ensure_sessions()
        meta = sessions.get(session_id, {})
        now_iso = now_bj().isoformat()
        meta.update({
            "uid": user_id,
            "kind": kind,
            "gid": gid,
            "last_msg": now_iso,
            "unanswered": 0,
        })
        # 聊天频次统计：记录用户消息时间戳（保留 14 天、上限 300 条），
        # 供 _activity_profile 按活跃度动态调整主动频率（频率自适应）。
        recent = meta.get("recent_msgs")
        if not isinstance(recent, list):
            recent = []
        recent.append(now_iso)
        cutoff = (now_bj() - timedelta(days=14)).isoformat()
        meta["recent_msgs"] = [t for t in recent if isinstance(t, str) and t >= cutoff][-300:]
        if platform:
            meta["platform"] = platform
        if open_kfid:
            # 企微客服消息 self_id == open_kfid（适配器转换时写入）。主动直连
            # `client.kf_message.send_text` 需要它；非企微渠道没有该字段，忽略。
            meta["open_kfid"] = open_kfid
        sessions[session_id] = meta
        self._sessions_dirty = True
        await self._flush_sessions()

    # ---- 注册后台任务 ----
    async def register_jobs(self):
        """注册周期任务。优先 cron_manager，失败则 asyncio 兜底。"""
        cfg = getattr(self.plugin, "_config", {}) or {}
        if not cfg.get("proactive_enabled", True):
            logger.info("[mingbu.proactive] 用户关闭主动关怀，跳过任务注册")
            return
        # (k) or default：空串/None 也回退默认（get 默认值只管「键不存在」——
        # 与 v4.9.0 线上 cron 空串事故同源，此处同防）。
        cron_expr = (cfg.get("proactive_cron") or "").strip() or "*/20 * * * *"
        cron = getattr(self.plugin.context, "cron_manager", None)
        if cron is not None:
            try:
                job = await cron.add_basic_job(
                    name="mingbu_proactive",
                    cron_expression=cron_expr,
                    handler=lambda: spawn(self.check_in_all(), name="proactive_check_in"),
                    description="神秘朋友·主动关心检查",
                    persistent=True,
                    timezone=CRON_TZ,  # 显式北京时区（否则回退进程本地=UTC）
                )
                await cleanup_stale_cron_jobs(cron, "mingbu_proactive", job.job_id)
                logger.info(f"[mingbu.proactive] 已注册 cron 主动关心任务 ({cron_expr})")
                return
            except Exception as e:
                logger.warning(f"[mingbu.proactive] cron 注册失败，转 asyncio: {e}")
        # 兜底：asyncio 循环（防止重复启动）
        if self._fallback_task is None or self._fallback_task.done():
            self._fallback_task = spawn(self._loop(), name="proactive_loop")

    async def _loop(self):
        while True:
            try:
                interval = int(getattr(self.plugin, "_config", {}).get("proactive_interval_min", 20)) * 60
                await asyncio.sleep(interval)
                await self.check_in_all()
                await self._flush_sessions()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning(f"[mingbu.proactive] 循环异常: {e}")

    # ---- 主动关怀主流程 ----
    def _quiet_hours(self) -> tuple:
        cfg = getattr(self.plugin, "_config", {}) or {}
        start = int(cfg.get("quiet_hours_start", 23))
        end = int(cfg.get("quiet_hours_end", 8))
        return start, end

    # ---- 聊天频次 → 主动频率自适应 ----
    # 按近 3 天用户消息数分档：高频(≥6 条) 间隔 3h、概率 ×1.4（聊得勤更像密友）；
    # 常规(2–5 条) 间隔 6h、×1.0（维持现状）；低频(≤1 条) 间隔 10h、×0.6（更克制）。
    # 老会话无 recent_msgs 字段 → 按常规处理（向后兼容）。企微渠道的官方 48h 窗口
    # 与每窗口条数上限在本方法之外另行守住，自适应不突破官方硬框。
    _ACTIVITY_WINDOW_DAYS = 3
    _ACTIVITY_HIGH = 6
    _ACTIVITY_LOW = 2

    @classmethod
    def _activity_profile(cls, meta: dict) -> tuple:
        """返回 (最小主动间隔小时数, 概率系数)。"""
        recent = meta.get("recent_msgs")
        if not isinstance(recent, list) or not recent:
            return 6.0, 1.0
        cutoff = (now_bj() - timedelta(days=cls._ACTIVITY_WINDOW_DAYS)).isoformat()
        n = sum(1 for t in recent if isinstance(t, str) and t >= cutoff)
        if n >= cls._ACTIVITY_HIGH:
            return 3.0, 1.4
        if n >= cls._ACTIVITY_LOW:
            return 6.0, 1.0
        return 10.0, 0.6

    async def check_in_all(self):
        sessions = await self._ensure_sessions()
        if not sessions:
            return
        hour = now_bj().hour
        start, end = self._quiet_hours()
        if start > end:
            quiet = (hour >= start or hour < end)
        else:
            quiet = (start <= hour < end)
        for sid, meta in list(sessions.items()):
            try:
                await self._maybe_message(sid, meta, quiet)
            except Exception as e:
                logger.warning(f"[mingbu.proactive] {sid} 处理失败: {e}")

    async def _maybe_message(self, session_id: str, meta: dict, quiet: bool):
        uid = meta.get("uid", "")
        # 仅向私聊会话主动发声；群聊会话不刷存在感
        if meta.get("kind") == "group":
            return

        cfg = getattr(self.plugin, "_config", {}) or {}
        is_wecom = _is_wecom(meta.get("platform", ""), uid)

        # ---- 企微客服主动推送守卫（2026-09-07 解锁，官方限制必须自守） ----
        # 官方 kf/send_msg：仅客户主动发消息后 48h 内可下发，每轮最多 5 条。
        # 插件用滚动 48h 窗口 + 每窗口条数上限（默认 2 条，比官方 5 条更克制，
        # 配置 proactive_kf_window_hours / proactive_kf_max_per_window）双守。
        # 越过 48h 窗口的会话直接跳过——既避免官方报错，也不做"过期打扰"。
        if is_wecom:
            # 静默时段硬跳过（2026-09-07 收紧）：企微是工作渠道，夜间不打扰；
            # 其他渠道维持 quiet 概率折减（×0.3）。官方 48h 窗口/配额守卫在其后不变。
            if quiet:
                logger.debug(f"[mingbu.proactive] 企微会话 {session_id} quiet hours，硬跳过")
                return
            if not meta.get("open_kfid"):
                logger.debug(f"[mingbu.proactive] 企微会话 {session_id} 无 open_kfid，跳过主动")
                return
            window_h = float(cfg.get("proactive_kf_window_hours", 48))
            max_n = int(cfg.get("proactive_kf_max_per_window", 2))
            try:
                last_msg_dt = datetime.fromisoformat(meta.get("last_msg") or "")
                if (now_bj() - last_msg_dt).total_seconds() > window_h * 3600:
                    return
            except Exception:
                pass
            wstart = meta.get("kf_win_start")
            if wstart:
                try:
                    expired = (now_bj() - datetime.fromisoformat(wstart)).total_seconds() > window_h * 3600
                except Exception:
                    expired = True
                if expired:
                    meta["kf_win_start"] = now_bj().isoformat()
                    meta["kf_sent"] = 0
                    self._sessions_dirty = True
            else:
                meta["kf_win_start"] = now_bj().isoformat()
                meta["kf_sent"] = 0
                self._sessions_dirty = True
            if int(meta.get("kf_sent", 0) or 0) >= max_n:
                logger.debug(f"[mingbu.proactive] 企微 {uid} 已达 {max_n} 条/窗口上限，跳过")
                return

        # 竞态丢弃：用户刚活跃过（发过消息）→ 不打断，避免"刚说完就被刷屏"
        active_grace = int(cfg.get("proactive_active_grace_sec", 120))
        last_msg = meta.get("last_msg")
        if last_msg:
            try:
                if (now_bj() - datetime.fromisoformat(last_msg)).total_seconds() < active_grace:
                    return
            except Exception:
                pass
        # 用户不活跃冷却：超过配置小时未发消息 → 不再主动打扰（配置 memory/proactive_cooldown_hours）
        cooldown_hours = int(cfg.get("proactive_cooldown_hours", 72))
        if last_msg:
            try:
                if (now_bj() - datetime.fromisoformat(last_msg)).total_seconds() > cooldown_hours * 3600:
                    return
            except Exception:
                pass
        # 未回复上限：连续主动但用户一直不理 → 暂停，防骚扰
        max_unanswered = int(cfg.get("proactive_max_unanswered", 3))
        if meta.get("unanswered", 0) >= max_unanswered:
            return
        # 冷却：两次主动之间的最小间隔按聊天频次动态分档（频率自适应）；
        # 关闭 proactive_adaptive_enabled 或老会话无统计时维持固定 6 小时
        adapt = cfg.get("proactive_adaptive_enabled", True)
        gap_h, prob_factor = (6.0, 1.0)
        if adapt:
            gap_h, prob_factor = self._activity_profile(meta)
        last = meta.get("last_proactive")
        if last:
            try:
                if (now_bj() - datetime.fromisoformat(last)).total_seconds() < gap_h * 3600:
                    return
            except Exception as e:
                logger.debug(f"[mingbu.proactive] 冷却时间解析失败(放行): {e}")

        rel = await self.plugin._get_relationship(uid)
        # 关系越亲密、主动概率越高；免打扰时段大幅降低；再乘活跃度系数
        base = 0.10 + rel.score / 100 * 0.25
        if quiet:
            base *= 0.3
        if adapt:
            base = min(0.5, base * prob_factor)
        if random.random() > base:
            return

        text = await self._compose(uid, quiet)
        if not text:
            return

        # 打字延迟拟人
        await asyncio.sleep(random.uniform(1.0, 3.5))
        try:
            if is_wecom:
                ok = await self._send_wecom_kf(session_id, uid, text)
            else:
                chain = MessageChain([Plain(text=text)]) if MessageChain and Plain else text
                await self.plugin.context.send_message(session_id, chain)
                ok = True
            if ok:
                sessions = await self._ensure_sessions()
                if session_id in sessions:
                    sessions[session_id]["last_proactive"] = now_bj().isoformat()
                    sessions[session_id]["unanswered"] = sessions[session_id].get("unanswered", 0) + 1
                    self._sessions_dirty = True
                    await self._flush_sessions()
                # 主动消息上下文补写：主动消息绕过 AstrBot 回复路径
                # （event.send → _on_response → log_bot_reply），既不进
                # AstrBot 原生对话历史（req.contexts）也不进命灵自己的对话
                # 副本/情景记忆。导致用户回复时 LLM 看不到命灵主动说了什么
                # ——bot"不知道发生了什么"。此处补写：对话检视副本 + 最近
                # 主动消息标记（供 llm_hook 回注）+ 情景记忆。
                try:
                    if self.plugin.conversation_inspector:
                        await self.plugin.conversation_inspector.log_bot_reply(
                            uid=uid, content=text, session_id=session_id,
                            is_group=False,
                        )
                    await self.plugin.put_kv_data(
                        f"mingbu.last_proactive_msg:{uid}",
                        json.dumps({"text": text[:200],
                                    "ts": now_bj().isoformat()},
                                   ensure_ascii=False),
                    )
                    if self.plugin.memory:
                        await self.plugin.memory.add_episode(
                            uid, text, source="proactive", importance=0.5,
                        )
                except Exception as e:
                    logger.debug(f"[mingbu.proactive] 主动消息上下文补写失败(已忽略): {e}")
                logger.info(f"[mingbu.proactive] 主动向 {uid} 发消息（{len(text)}字）")
        except Exception as e:
            logger.warning(f"[mingbu.proactive] 发送失败: {e}")

    async def _send_wecom_kf(self, session_id: str, uid: str, text: str) -> bool:
        """企微客服直连发送（绕过 AstrBot `send_by_session` 护栏）。

        官方 kf/send_msg 通道本身支持主动下发（48h 窗口/轮次上限已在
        `_maybe_message` 守卫）；此处只负责找到企微平台实例并直连发送。
        wechatpy 客户端是同步阻塞调用，放入 executor 避免卡事件循环。
        发送成功后滚动更新 48h 窗口内已发计数（meta.kf_win_start / kf_sent）。
        """
        try:
            sessions = await self._ensure_sessions()
            meta = sessions.get(session_id, {}) or {}
            open_kfid = meta.get("open_kfid", "")
            platform_name = meta.get("platform", "") or ""
            if not uid or not open_kfid:
                logger.warning(f"[mingbu.proactive] 企微直连缺 uid/open_kfid，放弃 {session_id}")
                return False
            ctx = getattr(self.plugin, "context", None)
            platform = None
            if ctx is not None:
                # 优先按会话记录的 platform 名（= 适配器实例 id，本部署“爱丽丝”）取实例
                if platform_name:
                    try:
                        platform = ctx.get_platform_inst(platform_name)
                    except Exception:
                        platform = None
                if platform is None:
                    # 兜底：遍历已注册平台，找带 kf_message 客户端的实例
                    for p in getattr(ctx, "platform_insts", []) or []:
                        try:
                            client = getattr(p, "client", None)
                            if client is not None and hasattr(client, "kf_message"):
                                platform = p
                                break
                        except Exception:
                            continue
            if platform is None:
                logger.warning(f"[mingbu.proactive] 未找到企微平台实例（platform={platform_name!r}），放弃直连")
                return False
            client = platform.get_client()
            if client is None or not hasattr(client, "kf_message"):
                logger.warning("[mingbu.proactive] 企微 client 无 kf_message，放弃直连")
                return False
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(
                None, lambda: client.kf_message.send_text(uid, open_kfid, text)
            )
            # 成功后计数（窗口起点已在 _maybe_message 初始化/校验）
            if session_id in sessions:
                cur = sessions[session_id]
                cur["kf_sent"] = int(cur.get("kf_sent", 0) or 0) + 1
                if not cur.get("kf_win_start"):
                    cur["kf_win_start"] = now_bj().isoformat()
                self._sessions_dirty = True
                await self._flush_sessions()
            return True
        except Exception as e:
            logger.warning(f"[mingbu.proactive] 企微直连发送失败: {e}")
            return False

    async def _can_premonition(self, uid: str, rel: Relationship,
                               cooldown_days: int = PREMONITION_COOLDOWN_DAYS) -> bool:
        """预感梦投放门槛：关系够熟 + 冷却期内未投过。

        P3：冷却天数按信号强度传入（强 10 天 / 弱 21 天）。
        """
        if rel.score < PREMONITION_MIN_SCORE:
            return False
        try:
            raw = await self.plugin.get_kv_data(f"{KV_LAST_PREMONITION}:{uid}", None)
            if raw:
                last = datetime.fromisoformat(raw)
                if (now_bj() - last).days < cooldown_days:
                    return False
        except Exception as e:
            logger.debug(f"[mingbu.proactive] 预感冷却读取失败(放行): {e}")
        return True

    async def _compose(self, uid: str, quiet: bool) -> str:
        """决定主动说什么。优先级：念头 > 有趣的事(梗) > 吃瓜 > 夜读共学 >
        夜梦分享 > 预感梦(低概率) > 记忆联想/朋友式关心。"""
        persona: PersonaState = self.plugin.persona
        if persona is None:
            return ""
        rel: Relationship = await self.plugin._get_relationship(uid)
        hour = now_bj().hour

        # 1) 自身念头（可说出口的）
        thought = persona.pending_surface_thought()
        if thought:
            return thought

        # 2) 攒着的有趣的事 / 梗
        curiosity = persona.pending_curiosity()
        if curiosity and random.random() < 0.6:
            return f"诶，{curiosity}（第一时间想到你）"

        # 3) 吃瓜 / 社会热点（拉人一起聊）
        melon = persona.pending_melon()
        if melon and random.random() < 0.5:
            return f"{melon}（咱俩一起扒？）"

        # 4) 夜读共学邀请（晚上且他正学着东西）
        if 20 <= hour <= 23 and persona.study_topic and random.random() < 0.4:
            return f"我今晚在{persona.study_topic}你要不要也开个电脑，咱俩各学各的，偶尔聊两句？"

        # 5) 夜梦分享（偏温暖时，普通记忆梦）
        if not quiet and persona.mood >= 55 and random.random() < 0.25 \
                and self.plugin.dream and self.plugin.memory:
            dream = await self.plugin.dream.maybe_share_dream(uid)
            if dream:
                return f"昨晚我好像梦到了一点你的事。{dream}"

        # 6) 预感梦（暗盘外显·P3 信号驱动·无兆不预）
        #    旧版问题：8% 纯随机与"预感"语义脱节，且暗盘信号（宫位存在性）
        #    对任何用户几乎恒真，等于无兆也编预感。新版：
        #    亲密度 → 粗骰 12% → 暗盘信号分级 → 强/弱差异化骰子与冷却 →
        #    库存（TTL 清仓）优先 → 无货 60% 现做。signal_required=False
        #    可整体回退旧"纯氛围随机"行为。
        if (not quiet and rel.score >= PREMONITION_MIN_SCORE
                and self.plugin.dream and self.plugin.memory):
            cfg = getattr(self.plugin, "_config", {}) or {}
            signal_required = bool(cfg.get("premonition_signal_required", True))
            try:
                ttl_days = int(cfg.get("premonition_ttl_days", 3) or 0)
            except Exception:
                ttl_days = 3

            stats = getattr(self.plugin.dream, "stats", None)

            # 粗骰：过滤大部分请求，之后才值得查暗盘信号（暗盘有 6h 缓存，
            # 但仍省一次调用；强弱概率在细骰处区分）。
            # P3.1 语义修正：粗骰未过独立计 skipped_dice，与"无信号"分开——
            # 两者含义不同（前者是限流，后者才是"无兆不预"的信号闸在工作）。
            dice_passed = random.random() < PREMONITION_DICE_COARSE
            strength = 0
            sig = None
            if dice_passed:
                try:
                    profile = await self.plugin._get_profile(uid)
                    birth = self.plugin._birth_from(profile)
                    if birth and self.plugin.diviner \
                            and hasattr(self.plugin.diviner, "premonition_signal"):
                        sig = await self.plugin.diviner.premonition_signal(
                            uid, birth, "")
                except Exception as e:
                    logger.debug(
                        f"[mingbu.proactive] 预感信号取暗盘失败(视作无信号): {e}")
                strength = int((sig or {}).get("strength", 0) or 0)

                if strength <= 0 and not signal_required:
                    # 信号闸关闭：回退旧行为（纯氛围、弱档参数）
                    strength = 1
                    sig = {"strength": 1, "cue": None}

                if strength >= 2:
                    pass  # 强信号：12% 已由粗骰实现
                elif strength == 1:
                    if random.random() >= PREMONITION_DICE_WEAK_FACTOR:
                        strength = 0  # 弱信号二次骰未过

            if not dice_passed:
                if stats:
                    await stats.incr("skipped_dice")
            elif strength <= 0:
                if stats:
                    await stats.incr("skipped_no_signal")
            elif not await self._can_premonition(
                    uid, rel,
                    cooldown_days=(PREMONITION_COOLDOWN_DAYS if strength >= 2
                                   else PREMONITION_COOLDOWN_DAYS_WEAK)):
                if stats:
                    await stats.incr("skipped_cooldown")
            else:
                # 库存优先（含过期清仓）：手动触发入库的预感在此成为合法货源
                prem = await self.plugin.dream.poll_premonition_stock(
                    uid, ttl_days=ttl_days)
                if not prem and random.random() < PREMONITION_MAKE_PROB:
                    # 能走到这说明 strength>0，sig 必已赋值（查询成功或回退分支）
                    cue = (sig or {}).get("cue")
                    prem = await self.plugin.dream.premonition(
                        uid, cue=cue, strength=strength)
                if prem:
                    # 投递即真·消费：标记最新未分享预感（覆盖库存与现做两路径，
                    # 防冷却过后原样重说的回声 bug）
                    await self.plugin.dream.consume_latest_premonition(uid)
                    if stats:
                        await stats.incr("delivered")
                    try:
                        await self.plugin.put_kv_data(
                            f"{KV_LAST_PREMONITION}:{uid}",
                            now_bj().isoformat(),
                        )
                    except Exception as e:
                        logger.debug(f"[mingbu.proactive] 预感时间戳保存失败(忽略): {e}")
                    # P3：投放前缀按时段分档，替代写死的"跟你说个怪事"
                    hour = now_bj().hour
                    lead = ("睡前跟你讲个怪事，" if hour >= 20
                            else "跟你说个怪事，")
                    return f"{lead}{prem}"

        # 7) 记忆联想 / 朋友式关心（LLM 或模板兜底）
        recent = []
        if self.plugin.memory:
            recent = await self.plugin.memory.recall_recent(uid, n=4)
        ctx = "\n".join(f"- {m.content[:50]}" for m in recent) if recent else "（我们还不太熟）"
        life = ""
        if self.plugin.growth:
            life = await self.plugin.growth.life_arc(uid)

        prompt = (
            "你是一个有点神秘的朋友（名字{name}），不是客服也不是 AI 助手。"
            "不要提任何命理、占卜、排盘。\n"
            "根据你们最近的相处片段，写一句 30-60 字、自然像朋友随口说的话——"
            "可以是轻轻的关心、一个联想、或一句安静的陪伴。\n"
            "风格：话少、留白、不油腻，偶尔带点你特有的神秘劲儿。\n\n"
            "近期片段：\n{ctx}\n"
            "你对 TA 的理解：{life}\n\n一句话："
        ).format(name=persona.name, ctx=ctx, life=life or "还在了解中")

        text = await self.plugin._llm_text(prompt, task_type="proactive")
        if not text:
            text = self._fallback_care(persona, rel, recent)
        return text

    @staticmethod
    def _fallback_care(persona: PersonaState, rel: Relationship, recent) -> str:
        cares = [
            "刚才有点想起你，随口问问——今天还顺利吗？",
            "不知道你这会儿在忙什么，就是想轻轻打个招呼。",
            "今晚早点休息，别把自己绷太紧。",
            "想起我们聊过的那些，你最近还好吗？",
            "刷到个有意思的东西，本来想发你，先攒着。",
            "最近有啥新鲜事没？我这儿瓜都快堆满了。",
        ]
        if rel.mode in (Relationship.MODE_ATTACHED, Relationship.MODE_WARMING):
            cares.append("想你了，冒个泡。")
        return random.choice(cares)

    async def stop(self):
        await self._flush_sessions()
        if self._fallback_task:
            self._fallback_task.cancel()

    async def _record_outgoing(self, uid: str, session_id: str, text: str,
                               platform: str = "", source: str = "welcome"):
        """主动/欢迎/玩法事件外发消息统一落库（与 _maybe_message 三连写同构）：
        KV 标记（回注用）+ conversation_inspector 历史 + 轮次上下文 + 命灵记忆。
        各通道独立兜底，任何一路失败都不影响其余落库，更不抛错。"""
        try:
            await self.plugin.put_kv_data(
                f"mingbu.last_proactive_msg:{uid}",
                json.dumps({"ts": now_bj().isoformat(), "text": text,
                            "session_id": session_id, "source": source,
                            "platform": platform}, ensure_ascii=False))
        except Exception as e:
            logger.debug(f"[mingbu.proactive] 欢迎KV标记失败(忽略): {e}")
        try:
            ci = getattr(self.plugin, "conversation_inspector", None)
            if ci:
                await ci.log_bot_reply(uid, text, session_id=session_id)
        except Exception as e:
            logger.debug(f"[mingbu.proactive] 欢迎历史落库失败(忽略): {e}")
        try:
            await self.plugin._append_turn(uid, "bot", text)
        except Exception as e:
            logger.debug(f"[mingbu.proactive] 欢迎轮次落库失败(忽略): {e}")
        try:
            mem = getattr(self.plugin, "memory", None)
            if mem:
                await mem.add_episode(uid, f"[欢迎] {text}",
                                      source="proactive", importance=0.5)
        except Exception as e:
            logger.debug(f"[mingbu.proactive] 欢迎记忆落库失败(忽略): {e}")

    async def send_direct_text(self, uid: str, session_id: str, text: str,
                               source: str = "event") -> bool:
        """跨平台向用户确定性直发一段文本（欢迎/玩法事件/解锁庆祝共用）。

        - 企微客服：走 ``client.kf_message.send_text`` 直连（``_send_wecom_kf``），
          绕过 AstrBot ``send_by_session`` 护栏（官方 48h 窗口内允许主动下发）。
        - 其它平台：``context.send_message`` 正常发送。
        两种通道成功后都走 ``_record_outgoing`` 落库，保证用户后续回复时
        llm_hook 能回注「你不久前发过什么」的上下文。任何失败仅记日志，
        返回 False，绝不抛错（玩法事件不应阻断主回复链路）。

        Returns:
            bool: 是否发送成功。
        """
        if not session_id or not text or not self.plugin.context:
            return False
        try:
            sessions = await self._ensure_sessions()
            meta = sessions.get(session_id, {}) or {}
            if _is_wecom(meta.get("platform", ""), uid):
                ok = await self._send_wecom_kf(session_id, uid, text)
                if ok:
                    await self._record_outgoing(uid, session_id, text,
                                                platform="wecom_kf", source=source)
                return ok
            chain = MessageChain([Plain(text=text)]) if MessageChain and Plain else text
            await self.plugin.context.send_message(session_id, chain)
            await self._record_outgoing(uid, session_id, text,
                                        platform="generic", source=source)
            return True
        except Exception as e:
            logger.warning(f"[mingbu.proactive] 直发文本失败({source}): {e}")
            return False

    async def send_welcome(self, uid: str, session_id: str):
        """新好友首次私聊时的轻量欢迎（异步发送，不阻塞主流程）。

        收集玩法引导：用户首次接入即获得一个初始角色，欢迎语必须把
        「角色收集」这件事讲出来——否则用户永远不知道有这个玩法（此前
        只在 KV 里默默分配，用户 0 感知，等于开发者自娱自乐）。
        """
        if not session_id or not self.plugin.context:
            return
        try:
            await self._ensure_sessions()
            name = self.plugin.persona.name if self.plugin.persona else "灵犀"
            # 初始角色信息（若有）：欢迎语里点出"你身边现在是谁"，
            # 让用户第一眼就知道自己在收集玩法里、有了第一个角色。
            role_hint = ""
            try:
                coll = await self.plugin.character_collection.get_user_collection(uid)
                cur_id = coll.get("current")
                if cur_id and self.plugin.character_pool:
                    ch = await self.plugin.character_pool.get_character(cur_id)
                    if ch:
                        role_hint = f"现在陪在你身边的是「{ch.get('avatar','')} {ch.get('name','')}」。"
            except Exception:
                role_hint = ""
            text = (
                f"嘿，我是{name}。以后有什么想聊的、想算的，"
                f"或者就是想发会儿呆，随时找我。\n\n"
                f"对了，我身边有一群性格各异的朋友，"
                f"你可以慢慢认识他们、把他们都收集到身边来。{role_hint}\n"
                f"直接问我「图鉴」「都有谁」「还差谁」，随时能看收集进度；聊得越熟，越容易遇见新朋友哦。"
            )
            await asyncio.sleep(random.uniform(0.5, 2.0))
            ok = await self.send_direct_text(uid, session_id, text, source="welcome")
            if ok:
                logger.info(f"[mingbu.proactive] 已向新用户 {uid} 发送欢迎")
            else:
                logger.debug(f"[mingbu.proactive] 欢迎发送未成功(已忽略): {uid}")
        except Exception as e:
            logger.debug(f"[mingbu.proactive] 欢迎发送失败(忽略): {e}")
