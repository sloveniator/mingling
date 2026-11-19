from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from store import Store

STAGES = ["normal", "warming", "attached", "refusing", "hurt"]

PROMOTE_RULES = [(0.45, "warming"), (0.75, "attached")]

STAGE_EVENTS = {
    ("normal", "warming"): ("称呼变化", "开始用更亲近的称呼"),
    ("warming", "attached"): ("私密通道", "可以聊更私密的话题了"),
    ("*", "hurt"): ("关系受损", "回复变得更克制"),
    ("hurt", "warming"): ("关系修复", "气氛重新缓和"),
}

STAGE_HINTS = {
    "normal": "",
    "warming": "你们最近聊得比较多，关系在变近",
    "attached": "你们的关系已经比较亲近，可以更自然地关心对方",
    "refusing": "你此刻在克制回应，保持礼貌但疏离",
    "hurt": "关系刚刚受损，回复应更克制，避免过度热情",
}

HURT_REPAIR_THRESHOLD = 3


@dataclass
class RelationshipState:
    intimacy: float = 0.0
    stage: str = "normal"
    updated_at: float = 0.0
    repair_signals: int = 0

    def to_dict(self) -> dict:
        return {
            "intimacy": round(self.intimacy, 4),
            "stage": self.stage,
            "updated_at": self.updated_at,
            "repair_signals": self.repair_signals,
        }

    @classmethod
    def from_dict(cls, d: dict | None) -> "RelationshipState":
        if not isinstance(d, dict):
            return cls(updated_at=time.time())
        return cls(
            intimacy=float(d.get("intimacy", 0.0)),
            stage=str(d.get("stage", "normal")),
            updated_at=float(d.get("updated_at", time.time())),
            repair_signals=int(d.get("repair_signals", 0)),
        )


class RelationshipManager:
    def __init__(self, store: Store):
        self._store = store

    async def get(self, umo: str) -> RelationshipState:
        raw = await self._store.load(umo, "relationship", None)
        st = RelationshipState.from_dict(raw)
        if st.updated_at == 0.0:
            st.updated_at = time.time()
        return st

    def _target_stage(self, intimacy: float) -> str:
        target = "normal"
        for threshold, name in PROMOTE_RULES:
            if intimacy >= threshold:
                target = name
        return target

    async def nudge(self, umo: str, delta: float, reason: str) -> dict[str, Any]:
        st = await self.get(umo)
        st.intimacy = max(0.0, min(1.0, st.intimacy + delta))
        st.updated_at = time.time()
        event: dict | None = None
        old_stage = st.stage

        # 1. hurt 状态优先处理修复
        if st.stage == "hurt":
            if delta > 0:
                st.repair_signals += 1
                if st.repair_signals >= HURT_REPAIR_THRESHOLD:
                    st.stage = "warming"
                    st.repair_signals = 0
                    event = self._make_event("hurt", "warming")
                    await self._store.append_timeline(umo, "relationship_repair", {
                        "from": "hurt", "to": "warming",
                    })
            await self._save_and_timeline(umo, st, delta, reason, event)
            return {"state": st, "event": event}

        # 2. refusing 状态不自动变化（由外部显式解除）
        if st.stage == "refusing":
            await self._save_and_timeline(umo, st, delta, reason, None)
            return {"state": st, "event": None}

        # 3. 正常 promote
        target = self._target_stage(st.intimacy)
        if target != st.stage:
            st.stage = target
            event = self._make_event(old_stage, target)
            await self._store.append_timeline(umo, "relationship_stage", {
                "from": old_stage, "to": target, "intimacy": st.intimacy,
            })

        await self._save_and_timeline(umo, st, delta, reason, event)
        return {"state": st, "event": event}

    async def _save_and_timeline(
        self,
        umo: str,
        st: RelationshipState,
        delta: float,
        reason: str,
        event: dict | None,
    ) -> None:
        await self._store.save(umo, "relationship", st.to_dict())
        await self._store.append_timeline(umo, "relationship_nudge", {
            "delta": round(delta, 3),
            "reason": reason[:80],
            "stage": st.stage,
            "intimacy": round(st.intimacy, 4),
            "event": event,
        })

    async def mark_hurt(self, umo: str, reason: str) -> None:
        st = await self.get(umo)
        old = st.stage
        st.stage = "hurt"
        st.repair_signals = 0
        st.updated_at = time.time()
        await self._store.save(umo, "relationship", st.to_dict())
        await self._store.append_timeline(umo, "relationship_stage", {
            "from": old, "to": "hurt", "reason": reason[:80],
        })

    @staticmethod
    def _make_event(old: str, new: str) -> dict | None:
        title = STAGE_EVENTS.get((old, new)) or STAGE_EVENTS.get(("*", new))
        if not title:
            return None
        return {"title": title[0], "detail": title[1], "from": old, "to": new}

    @staticmethod
    def stage_hint(stage: str) -> str:
        return STAGE_HINTS.get(stage, "")
