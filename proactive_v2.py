from __future__ import annotations

import time
from dataclasses import dataclass

from astrbot.api import logger
from astrbot.api.message_components import Plain

from emotion_trace import EmotionTrace
from open_loops import OpenLoopManager
from relation_memory import RelationMemory
from relationship import RelationshipManager
from store import Store
from topic_stack import TopicStack


@dataclass
class ProactiveCandidate:
    source: str            # open_loop / mood / daily
    text: str
    weight: float
    max_unanswered: int


class ProactiveEngine:
    """
    主动消息来源优先级：未闭环话题回访 > 情绪回访 > 兜底问候。
    调度与发送完全复用 AstrBot 的 cron 与 send_message。
    """

    def __init__(
        self,
        context,
        store: Store,
        cfg,
        emotion: EmotionTrace,
        relationship: RelationshipManager,
        loops: OpenLoopManager,
        topics: TopicStack,
        memory: RelationMemory,
    ):
        self._context = context
        self._store = store
        self._cfg = cfg
        self._emotion = emotion
        self._relationship = relationship
        self._loops = loops
        self._topics = topics
        self._memory = memory

    # ---------- 活跃时段 ----------
    async def record_active_hour(self, umo: str, ts: float | None = None) -> None:
        ts = ts or time.time()
        hour = time.localtime(ts).tm_hour
        raw = await self._store.load(umo, "active_hours", {})
        if not isinstance(raw, dict):
            raw = {}
        raw[str(hour)] = int(raw.get(str(hour), 0)) + 1
        await self._store.save(umo, "active_hours", raw)

    async def _active_hours(self, umo: str) -> list[int]:
        raw = await self._store.load(umo, "active_hours", {})
        if not isinstance(raw, dict) or not raw:
            return list(range(9, 23))
        pairs = []
        for h, c in raw.items():
            try:
                pairs.append((int(h), int(c)))
            except (TypeError, ValueError):
                continue
        pairs.sort(key=lambda x: x[1], reverse=True)
        return [h for h, _ in pairs[:6]]

    # ---------- 当日配额 ----------
    async def _sent_today(self, umo: str, now: float) -> list[float]:
        raw = await self._store.load(umo, "proactive_sent", [])
        if not isinstance(raw, list):
            return []
        lt = time.localtime(now)
        day_start = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0, 0, 0, -1))
        out = []
        for x in raw:
            try:
                if float(x) >= day_start:
                    out.append(float(x))
            except (TypeError, ValueError):
                continue
        return out

    async def _record_sent(self, umo: str, now: float) -> None:
        raw = await self._store.load(umo, "proactive_sent", [])
        if not isinstance(raw, list):
            raw = []
        raw.append(now)
        await self._store.save(umo, "proactive_sent", raw[-20:])

    async def _unanswered_count(self, umo: str) -> int:
        raw = await self._store.load(umo, "proactive_unanswered", 0)
        try:
            return int(raw)
        except (TypeError, ValueError):
            return 0

    async def _set_unanswered(self, umo: str, val: int) -> None:
        await self._store.save(umo, "proactive_unanswered", max(0, int(val)))

    # ---------- 静默判定 ----------
    async def _user_recently_active(self, umo: str, now: float, window_sec: float = 180.0) -> bool:
        last = await self._store.load(umo, "last_user_msg_at", 0.0)
        try:
            return (now - float(last)) <= window_sec
        except (TypeError, ValueError):
            return False

    async def _in_quiet_hours(self, now: float) -> bool:
        hour = time.localtime(now).tm_hour
        start = self._cfg.proactive_quiet_start_hour
        end = self._cfg.proactive_quiet_end_hour
        if start == end:
            return False
        if start < end:
            return start <= hour < end
        return hour >= start or hour < end

    # ---------- 候选生成 ----------
    async def _build_candidates(self, umo: str, now: float) -> list[ProactiveCandidate]:
        cands: list[ProactiveCandidate] = []

        for item in await self._loops.proactive_candidates(umo, now):
            cands.append(ProactiveCandidate(
                source="open_loop",
                text=self._render_open_loop(item["topic"], item["emotion"]),
                weight=0.6 + 0.4 * item["weight"],
                max_unanswered=self._cfg.proactive_max_unanswered_open_loop,
            ))

        last_mood = await self._store.load(umo, "last_mood", None)
        raw_at = await self._store.load(umo, "last_mood_at", 0.0)
        try:
            last_mood_at = float(raw_at)
        except (TypeError, ValueError):
            last_mood_at = 0.0
        if last_mood in {"sad", "angry", "anxious"} and \
                (now - last_mood_at) <= self._cfg.proactive_mood_window_hours * 3600:
            cands.append(ProactiveCandidate(
                source="mood",
                text=self._render_mood_checkin(last_mood),
                weight=0.7,
                max_unanswered=self._cfg.proactive_max_unanswered_open_loop,
            ))

        raw_daily = await self._store.load(umo, "last_daily_greet_at", 0.0)
        try:
            last_daily = float(raw_daily)
        except (TypeError, ValueError):
            last_daily = 0.0
        if (now - last_daily) >= self._cfg.proactive_daily_greet_min_gap_hours * 3600:
            cands.append(ProactiveCandidate(
                source="daily",
                text=self._render_daily(),
                weight=0.2,
                max_unanswered=self._cfg.proactive_max_unanswered_daily,
            ))

        cands.sort(key=lambda c: c.weight, reverse=True)
        return cands

    # ---------- 主循环 ----------
    async def tick(self, umo: str) -> None:
        if not self._cfg.proactive_enabled:
            return
        now = time.time()

        if await self._in_quiet_hours(now):
            return
        if await self._user_recently_active(umo, now):
            return

        if len(await self._sent_today(umo, now)) >= self._cfg.proactive_max_per_day:
            return

        active_hours = await self._active_hours(umo)
        hour = time.localtime(now).tm_hour
        near = {hour, hour - 1, hour + 1}
        if active_hours and not (near & set(active_hours)):
            return

        unanswered = await self._unanswered_count(umo)
        for cand in await self._build_candidates(umo, now):
            if unanswered >= cand.max_unanswered:
                continue
            if await self._send(umo, cand.text):
                await self._record_sent(umo, now)
                await self._set_unanswered(umo, unanswered + 1)
                if cand.source == "daily":
                    await self._store.save(umo, "last_daily_greet_at", now)
                await self._store.append_timeline(umo, "proactive_sent", {
                    "source": cand.source, "text": cand.text,
                })
                return

    async def _send(self, umo: str, text: str) -> bool:
        try:
            await self._context.send_message(umo, [Plain(text)])
            return True
        except Exception:
            import traceback
            logger.error(f"[mingling] proactive send failed: {umo}\n{traceback.format_exc()}")
            return False

    async def mark_user_replied(self, umo: str, now: float | None = None) -> None:
        await self._set_unanswered(umo, 0)
        await self._store.save(umo, "last_user_msg_at", now or time.time())

    # ---------- 文案 ----------
    @staticmethod
    def _render_open_loop(topic: str, emotion: str) -> str:
        t = topic.replace(" / ", "、")
        if emotion in {"sad", "anxious"}:
            return f"你上次说起的{t}，后来怎么样了？"
        return f"忽然想起你之前提到的{t}，现在有结果了吗？"

    @staticmethod
    def _render_mood_checkin(mood: str) -> str:
        if mood == "sad":
            return "今天心情好点了吗？"
        if mood == "angry":
            return "气消一点了吗？"
        return "这两天还顺利吗？"

    @staticmethod
    def _render_daily() -> str:
        return "在忙吗？"
