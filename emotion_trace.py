from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field

from store import Store


def _clamp(x: float, lo: float = -1.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


# 情绪词检测（轻量启发式，用于 last_mood 记录）
_MOOD_SIGNALS = {
    "sad": ("难过", "伤心", "哭", "失落", "委屈", "抑郁", "低落", "难受"),
    "angry": ("生气", "愤怒", "气死", "烦死", "火大", "受不了"),
    "anxious": ("紧张", "焦虑", "担心", "害怕", "不安"),
}


def detect_mood(text: str) -> str | None:
    if not text:
        return None
    for mood, kws in _MOOD_SIGNALS.items():
        if any(k in text for k in kws):
            return mood
    return None


@dataclass
class EmotionState:
    pleasantness: float = 0.0
    tension: float = 0.0
    arousal: float = 0.0
    certainty: float = 0.0
    updated_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict | None) -> "EmotionState":
        if not isinstance(d, dict):
            return cls()
        return cls(
            pleasantness=float(d.get("pleasantness", 0.0)),
            tension=float(d.get("tension", 0.0)),
            arousal=float(d.get("arousal", 0.0)),
            certainty=float(d.get("certainty", 0.0)),
            updated_at=float(d.get("updated_at", time.time())),
        )


class EmotionTrace:
    MAX_TRACE = 10
    DECAY_PER_HOUR = 0.06  # 向基线回归速率

    def __init__(self, store: Store):
        self._store = store

    async def _load_trace(self, umo: str) -> list[dict]:
        raw = await self._store.load(umo, "emotion_trace", [])
        return raw if isinstance(raw, list) else []

    async def load(self, umo: str) -> EmotionState:
        raw = await self._store.load(umo, "emotion", None)
        st = EmotionState.from_dict(raw)
        hours = max(0.0, (time.time() - st.updated_at) / 3600.0)
        if hours > 0:
            factor = max(0.0, 1.0 - self.DECAY_PER_HOUR * hours)
            st.pleasantness *= factor
            st.tension *= factor
            st.arousal *= factor
            st.certainty *= factor
            st.updated_at = time.time()
        return st

    async def nudge(self, umo: str, delta: dict[str, float], cause: str) -> EmotionState:
        st = await self.load(umo)
        for dim in ("pleasantness", "tension", "arousal", "certainty"):
            if dim in delta:
                setattr(st, dim, _clamp(getattr(st, dim) + float(delta[dim])))
        st.updated_at = time.time()
        await self._store.save(umo, "emotion", st.to_dict())

        trace = await self._load_trace(umo)
        trace.append({
            "at": time.time(),
            "cause": cause[:80],
            "delta": {k: round(float(v), 3) for k, v in delta.items()},
        })
        trace = trace[-self.MAX_TRACE:]
        await self._store.save(umo, "emotion_trace", trace)
        await self._store.append_timeline(umo, "emotion", {"delta": delta, "cause": cause[:80]})
        return st

    async def recent_cause(self, umo: str, window_sec: float = 1800) -> str | None:
        trace = await self._load_trace(umo)
        now = time.time()
        for item in reversed(trace):
            try:
                if now - float(item.get("at", 0)) <= window_sec:
                    return item.get("cause") or None
            except (TypeError, ValueError):
                continue
        return None

    @staticmethod
    def style_hint(st: EmotionState) -> str:
        hints = []
        if st.pleasantness <= -0.3 and st.tension >= 0.3:
            hints.append("语气更温和简短，少用热情表达，多共情短句")
        if st.arousal >= 0.5 and st.certainty <= -0.2:
            hints.append("语速感更口语化，可多追问一句")
        if st.certainty >= 0.5 and st.arousal <= 0.2:
            hints.append("表达更笃定平实")
        return "；".join(hints)
