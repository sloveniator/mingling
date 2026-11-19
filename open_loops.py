from __future__ import annotations

import time
from dataclasses import asdict, dataclass

from store import Store

_RESOLVE_SIGNALS = (
    "过了", "没成", "成了", "算了", "搞定", "结束", "完成了",
    "放弃了", "有结果", "通过了", "挂了", "失败了", "成功了",
)

_MAX_LOOPS = 20


@dataclass
class OpenLoop:
    topic: str
    opened_at: float
    last_mentioned: float
    emotion: str = "neutral"
    resolved: bool = False

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "OpenLoop":
        return cls(
            topic=d.get("topic", ""),
            opened_at=float(d.get("opened_at", 0.0)),
            last_mentioned=float(d.get("last_mentioned", 0.0)),
            emotion=d.get("emotion", "neutral"),
            resolved=bool(d.get("resolved", False)),
        )


class OpenLoopManager:
    def __init__(self, store: Store, window_days: int = 7):
        self._store = store
        self._window_sec = window_days * 86400

    async def _load(self, umo: str) -> list[OpenLoop]:
        raw = await self._store.load(umo, "open_loops", [])
        if not isinstance(raw, list):
            return []
        return [OpenLoop.from_dict(x) for x in raw if isinstance(x, dict)]

    async def _save(self, umo: str, loops: list[OpenLoop]) -> None:
        now = time.time()
        # 只保留窗口内的（无论 resolved 与否）
        keep = [x for x in loops if (now - x.opened_at) <= self._window_sec]
        await self._store.save(umo, "open_loops", [x.to_dict() for x in keep[-_MAX_LOOPS:]])

    async def register(self, umo: str, topic: str, *, emotion: str = "neutral") -> None:
        if not topic:
            return
        loops = await self._load(umo)
        now = time.time()
        for x in loops:
            if x.topic == topic and not x.resolved:
                x.last_mentioned = now
                await self._save(umo, loops)
                return
        loops.append(OpenLoop(topic=topic, opened_at=now, last_mentioned=now, emotion=emotion))
        await self._save(umo, loops)
        await self._store.append_timeline(umo, "open_loop_new", {
            "topic": topic, "emotion": emotion,
        })

    async def observe_resolution(self, umo: str, text: str) -> list[str]:
        if not text or not any(sig in text for sig in _RESOLVE_SIGNALS):
            return []
        loops = await self._load(umo)
        resolved: list[str] = []
        now = time.time()
        for x in loops:
            if x.resolved:
                continue
            parts = [p for p in x.topic.split(" / ") if p]
            if any(p in text for p in parts):
                x.resolved = True
                x.last_mentioned = now
                resolved.append(x.topic)
        if resolved:
            await self._save(umo, loops)
            for t in resolved:
                await self._store.append_timeline(umo, "open_loop_resolved", {"topic": t})
        return resolved

    async def list_open(self, umo: str) -> list[OpenLoop]:
        loops = await self._load(umo)
        now = time.time()
        return [
            x for x in loops
            if not x.resolved and (now - x.opened_at) <= self._window_sec
        ]

    async def proactive_candidates(self, umo: str, now: float) -> list[dict]:
        loops = await self.list_open(umo)
        out: list[dict] = []
        window_days = self._window_sec / 86400.0
        for x in loops:
            age_days = (now - x.opened_at) / 86400.0
            if age_days < 1.0 or age_days > window_days:
                continue
            weight = max(0.0, 1.0 - age_days / window_days)
            out.append({
                "topic": x.topic,
                "age_days": age_days,
                "emotion": x.emotion,
                "weight": weight,
            })
        return out
