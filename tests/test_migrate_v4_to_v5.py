"""v4 → v5 配置迁移单元测试。"""
from migrate_v4_to_v5 import map_config


def test_map_config_enables_and_maps_quiet_hours():
    old = {
        "quiet_hours_start": 23,
        "quiet_hours_end": 8,
        "proactive_enabled": True,
        "persona_name": "灵犀",
    }
    cfg = map_config(old)
    assert cfg["enabled"] is True
    assert cfg["proactive_quiet_start_hour"] == 23
    assert cfg["proactive_quiet_end_hour"] == 8
    assert cfg["proactive_enabled"] is True
    assert "persona_name" not in cfg  # v5 不再读取人格名


def test_map_config_coerces_legacy_string_numbers():
    cfg = map_config({"state_layer_max_tokens": "300", "emotion_anchor_threshold": "0.5"})
    assert cfg["state_layer_max_tokens"] == 300
    assert cfg["emotion_anchor_threshold"] == 0.5


def test_map_config_defaults_when_empty():
    cfg = map_config({})
    assert cfg["enabled"] is True
    assert cfg["timeline_max_lines"] == 500
