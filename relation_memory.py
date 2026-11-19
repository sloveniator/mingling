from __future__ import annotations

import math
import time
from dataclasses import asdict, dataclass

from store import Store


@dataclass
class MemoryItem:
    topic: str
    content: str
    mtype: str
    strength: float
    mentions: int
    created_at: float
    last_access: float
    half_life_days: float
    anchor: bool = False

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "MemoryItem":
        return cls(
            topic=d.get("topic", ""),
            content=d.get("content", ""),
            mtype=d.get("mtype", "episodic"),
            strength=float(d.get("strength", 1.0)),
            mentions=int(d.get("mentions", 1)),
            created_at=float(d.get("created_at", time.time())),
            last_access=float(d.get("last_access", time.time())),
            half_life_days=float(d.get("half_life_days", 7.0)),
            anchor=bool(d.get("anchor", False)),
        )


class RelationMemory:
    """
    关系锚点记忆。只记录与陪伴关系强相关的内容：
    重要事件、情绪峰值、关系变化。
    不承担通用事实记忆职责（交给 AstrBot 原生上下文或其他记忆插件）。
    """

    def __init__(self, store: Store, cfg):
        self._store = store
        self._cfg = cfg

    async def _load(self, umo: str) -> list[MemoryItem]:
        raw = await self._store.load(umo, "relation_memory", [])
        if not isinstance(raw, list):
            return []
        return [MemoryItem.from_dict(x) for x in raw if isinstance(x, dict)]

    async def _save(self, umo: str, items: list[MemoryItem]) -> None:
        items.sort(key=lambda x: self.effective_strength(x, time.time()), reverse=True)
        items = items[: self._cfg.relation_memory_max_items]
        await self._store.save(umo, "relation_memory", [x.to_dict() for x in items])

    @staticmethod
    def effective_strength(item: MemoryItem, now: float) -> float:
        age_days = max(0.0, (now - item.last_access) / 86400.0)
        return item.strength * (0.5 ** (age_days / max(0.5, item.half_life_days)))

    async def remember(
        self,
        umo: str,
        topic: str,
        content: str,
        *,
        mtype: str = "episodic",
        emotion_peak: float = 0.0,
    ) -> None:
        if not topic or not content:
            return
        items = await self._load(umo)
        now = time.time()

        for item in items:
            if item.topic == topic:
                item.mentions += 1
                item.strength = min(2.0, item.strength + 0.15 * math.log1p(item.mentions))
                item.last_access = now
                if emotion_peak >= self._cfg.emotion_anchor_threshold:
                    item.anchor = True
                    base = self._cfg.relation_memory_half_life_days.get(item.mtype, 7.0)
                    item.half_life_days = base * self._cfg.emotion_anchor_multiplier
                await self._save(umo, items)
                await self._store.append_timeline(umo, "memory_reinforce", {
                    "topic": topic, "mentions": item.mentions,
                })
                return

        base_hl = self._cfg.relation_memory_half_life_days.get(mtype, 7.0)
        anchor = emotion_peak >= self._cfg.emotion_anchor_threshold
        item = MemoryItem(
            topic=topic,
            content=content[:200],
            mtype=mtype,
            strength=1.0,
            mentions=1,
            created_at=now,
            last_access=now,
            half_life_days=base_hl * (self._cfg.emotion_anchor_multiplier if anchor else 1.0),
            anchor=anchor,
        )
        items.append(item)
        await self._save(umo, items)
        await self._store.append_timeline(umo, "memory_new", {
            "topic": topic, "mtype": mtype, "anchor": anchor,
        })

    async def anchors_for(
        self, umo: str, topic: str | None, limit: int = 2
    ) -> list[dict]:
        if not topic:
            return []
        items = await self._load(umo)
        now = time.time()
        parts = [p for p in topic.split(" / ") if p]
        if not parts:
            return []
        hits: list[MemoryItem] = []
        for item in items:
            if any(p in item.topic or p in item.content for p in parts):
                hits.append(item)
        hits.sort(key=lambda x: self.effective_strength(x, now), reverse=True)
        out: list[dict] = []
        for item in hits[:limit]:
            item.last_access = now
            out.append({
                "topic": item.topic,
                "content": item.content,
                "anchor": item.anchor,
            })
        if out:
            await self._save(umo, items)
        return out

    async def decay(self, umo: str) -> None:
        items = await self._load(umo)
        now = time.time()
        keep = [x for x in items if self.effective_strength(x, now) >= 0.05]
        if len(keep) != len(items):
            await self._save(umo, keep)
