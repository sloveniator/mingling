from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


def _as_bool(v: Any, default: bool = False) -> bool:
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return bool(v)
    if isinstance(v, str):
        return v.strip().lower() in {"1", "true", "yes", "on", "y"}
    return default


def _as_int(v: Any, default: int) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _as_float(v: Any, default: float) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


@dataclass
class CompanionConfig:
    # 总开关：False 时插件在 initialize() 立即返回。
    # v5 本地构建默认开启：升级后不应因缺少 enabled 键而静默失效。
    enabled: bool = True

    # 子开关（仅 enabled=True 时生效）
    state_layer_enabled: bool = True
    emotion_trace_enabled: bool = True
    relationship_event_enabled: bool = True
    topic_stack_enabled: bool = True
    open_loop_enabled: bool = True
    relation_memory_enabled: bool = True
    proactive_enabled: bool = True
    divination_tools_enabled: bool = True
    counseling_tools_enabled: bool = True

    # StateLayer 预算
    state_layer_max_tokens: int = 220
    state_layer_max_anchors: int = 2

    # 主动消息参数
    proactive_max_per_day: int = 2
    proactive_mood_window_hours: int = 48
    proactive_daily_greet_min_gap_hours: int = 20
    proactive_quiet_start_hour: int = 0
    proactive_quiet_end_hour: int = 7
    proactive_max_unanswered_open_loop: int = 2
    proactive_max_unanswered_daily: int = 1

    # 未闭环话题
    open_loop_window_days: int = 7

    # 关系记忆
    relation_memory_max_items: int = 120
    relation_memory_half_life_days: dict = field(
        default_factory=lambda: {
            "episodic": 7.0,
            "semantic": 30.0,
            "emotion": 14.0,
            "growth": 90.0,
        }
    )
    emotion_anchor_threshold: float = 0.7
    emotion_anchor_multiplier: float = 3.0

    # 时间线
    timeline_max_lines: int = 500

    @classmethod
    def from_raw(cls, raw: dict | None) -> "CompanionConfig":
        cfg = cls()
        if not isinstance(raw, dict):
            return cfg
        # 兼容 AstrBot 配置面板把整块放在 private_companion_mode 下的情况
        inner = raw.get("private_companion_mode")
        if isinstance(inner, dict) and inner:
            raw = inner

        cfg.enabled = _as_bool(raw.get("enabled"), cfg.enabled)

        # v4 → v5 配置兼容：旧版没有 enabled / proactive_quiet_* 键，
        # 这里按旧版语义补默认值，避免升级后主动消息落在错误时段。
        if "proactive_quiet_start_hour" not in raw and "quiet_hours_start" in raw:
            raw["proactive_quiet_start_hour"] = raw.get("quiet_hours_start")
        if "proactive_quiet_end_hour" not in raw and "quiet_hours_end" in raw:
            raw["proactive_quiet_end_hour"] = raw.get("quiet_hours_end")

        bool_fields = (
            "state_layer_enabled", "emotion_trace_enabled", "relationship_event_enabled",
            "topic_stack_enabled", "open_loop_enabled", "relation_memory_enabled",
            "proactive_enabled", "divination_tools_enabled", "counseling_tools_enabled",
        )
        for name in bool_fields:
            if name in raw:
                setattr(cfg, name, _as_bool(raw.get(name), getattr(cfg, name)))

        int_fields = (
            "state_layer_max_tokens", "state_layer_max_anchors",
            "proactive_max_per_day", "proactive_mood_window_hours",
            "proactive_daily_greet_min_gap_hours",
            "proactive_quiet_start_hour", "proactive_quiet_end_hour",
            "proactive_max_unanswered_open_loop", "proactive_max_unanswered_daily",
            "open_loop_window_days", "relation_memory_max_items",
            "timeline_max_lines",
        )
        for name in int_fields:
            if name in raw:
                setattr(cfg, name, _as_int(raw.get(name), getattr(cfg, name)))

        float_fields = ("emotion_anchor_threshold", "emotion_anchor_multiplier")
        for name in float_fields:
            if name in raw:
                setattr(cfg, name, _as_float(raw.get(name), getattr(cfg, name)))

        hl = raw.get("relation_memory_half_life_days")
        if isinstance(hl, dict):
            for k, v in hl.items():
                try:
                    cfg.relation_memory_half_life_days[k] = float(v)
                except (TypeError, ValueError):
                    continue
        return cfg

    def to_dict(self) -> dict:
        return asdict(self)
