#!/usr/bin/env python3
"""v4（astrbot_plugin_mingbu）→ v5（mingling）配置与数据迁移。

v5 是破坏性重构：插件标识、配置文件名、plugin_data 目录都已改为 mingling。
本脚本只做**文件级**迁移，不直接改 AstrBot 数据库：

1. 读取旧配置 config/astrbot_plugin_mingbu.json，映射为 v5 的 CompanionConfig
   键，写入 config/mingling.json；
2. 复制 plugin_data/astrbot_plugin_mingbu 下的时间线 jsonl 到
   plugin_data/mingling；
3. 旧 KV（AstrBot 数据库内按插件名隔离）无法安全自动迁移，脚本会明确提示。

默认 dry-run，只打印将要执行的动作；确认后加 --apply。
"""
from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path
from typing import Any

OLD_PLUGIN = "astrbot_plugin_mingbu"
NEW_PLUGIN = "mingling"

# v5 CompanionConfig 实际读取的键及默认值。
V5_DEFAULTS: dict[str, Any] = {
    "enabled": True,
    "mingbu_api_url": "http://localhost:2963",
    "semantic_router_enabled": True,
    "semantic_router_confidence": 0.65,
    "semantic_router_timeout": 8,
    "semantic_router_min_chars": 4,
    "counseling_enabled": True,
    "tarot_enabled": True,
    "health_enabled": True,
    "growth_engine_enabled": True,
    "state_layer_enabled": True,
    "emotion_trace_enabled": True,
    "relationship_event_enabled": True,
    "topic_stack_enabled": True,
    "open_loop_enabled": True,
    "relation_memory_enabled": True,
    "proactive_enabled": True,
    "divination_tools_enabled": True,
    "counseling_tools_enabled": True,
    "state_layer_max_tokens": 220,
    "state_layer_max_anchors": 2,
    "proactive_max_per_day": 2,
    "proactive_mood_window_hours": 48,
    "proactive_daily_greet_min_gap_hours": 20,
    "proactive_quiet_start_hour": 0,
    "proactive_quiet_end_hour": 7,
    "proactive_max_unanswered_open_loop": 2,
    "proactive_max_unanswered_daily": 1,
    "open_loop_window_days": 7,
    "relation_memory_max_items": 120,
    "emotion_anchor_threshold": 0.7,
    "emotion_anchor_multiplier": 3.0,
    "timeline_max_lines": 500,
}


def _find_config_dir(root: Path) -> Path | None:
    for candidate in (root / "config", root / "data" / "config", root):
        if (candidate / f"{OLD_PLUGIN}.json").exists():
            return candidate
    return None


def _find_plugin_data_dir(root: Path) -> Path | None:
    for candidate in (
        root / "plugin_data" / OLD_PLUGIN,
        root / "data" / "plugin_data" / OLD_PLUGIN,
    ):
        if candidate.is_dir():
            return candidate
    return None


def map_config(old: dict[str, Any]) -> dict[str, Any]:
    """把 v4 配置映射成 v5 配置；未知键不写入，避免把废弃项带进新面板。"""
    cfg = dict(V5_DEFAULTS)
    for key in V5_DEFAULTS:
        if key in old and old[key] is not None:
            cfg[key] = old[key]

    # 旧版免打扰字段改名
    if "quiet_hours_start" in old:
        cfg["proactive_quiet_start_hour"] = old["quiet_hours_start"]
    if "quiet_hours_end" in old:
        cfg["proactive_quiet_end_hour"] = old["quiet_hours_end"]

    # 旧版 config 没有 enabled；迁移即视为用户希望继续使用。
    cfg["enabled"] = True

    # 数值兜底，防止旧面板写入字符串。
    for key in (
        "state_layer_max_tokens", "state_layer_max_anchors",
        "proactive_max_per_day", "proactive_mood_window_hours",
        "proactive_daily_greet_min_gap_hours",
        "proactive_quiet_start_hour", "proactive_quiet_end_hour",
        "proactive_max_unanswered_open_loop", "proactive_max_unanswered_daily",
        "open_loop_window_days", "relation_memory_max_items", "timeline_max_lines",
    ):
        try:
            cfg[key] = int(cfg[key])
        except (TypeError, ValueError):
            cfg[key] = V5_DEFAULTS[key]
    for key in ("emotion_anchor_threshold", "emotion_anchor_multiplier"):
        try:
            cfg[key] = float(cfg[key])
        except (TypeError, ValueError):
            cfg[key] = V5_DEFAULTS[key]
    return cfg


def migrate(data_dir: Path, apply: bool, force: bool) -> int:
    config_dir = _find_config_dir(data_dir)
    data_root = _find_plugin_data_dir(data_dir)
    if config_dir is None and data_root is None:
        print(f"[error] 在 {data_dir} 下找不到旧配置或 plugin_data/{OLD_PLUGIN}")
        return 2

    actions: list[tuple[str, Path, Path]] = []
    new_cfg_path = None
    if config_dir is not None:
        old_cfg_path = config_dir / f"{OLD_PLUGIN}.json"
        new_cfg_path = config_dir / f"{NEW_PLUGIN}.json"
        actions.append(("config", old_cfg_path, new_cfg_path))

    new_data_dir = None
    if data_root is not None:
        new_data_dir = data_root.parent / NEW_PLUGIN
        actions.append(("data", data_root, new_data_dir))

    print(f"data_dir = {data_dir}")
    for kind, src, dst in actions:
        print(f"[plan] {kind}: {src} -> {dst}")

    if not apply:
        print("[dry-run] 未写入任何文件；确认后加 --apply")
        return 0

    if new_cfg_path is not None and config_dir is not None:
        if new_cfg_path.exists() and not force:
            print(f"[skip] {new_cfg_path} 已存在（--force 可覆盖）")
        else:
            old = json.loads((config_dir / f"{OLD_PLUGIN}.json").read_text(encoding="utf-8"))
            cfg = map_config(old if isinstance(old, dict) else {})
            new_cfg_path.write_text(
                json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
            print(f"[ok] 写入 {new_cfg_path}")

    if new_data_dir is not None and data_root is not None:
        if new_data_dir.exists() and not force:
            print(f"[skip] {new_data_dir} 已存在（--force 可覆盖）")
        else:
            shutil.copytree(data_root, new_data_dir, dirs_exist_ok=True)
            print(f"[ok] 复制 {data_root} -> {new_data_dir}")

    print(
        "[warn] AstrBot KV（数据库内按插件名隔离）未自动迁移。"
        "旧插件的 KV 记录仍在 astrbot_plugin_mingbu 命名空间；"
        "如需保留关系/情绪/记忆，请在 AstrBot 数据库中按旧命名空间导出后导入 mingling。"
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="命灵 v4 → v5 文件级迁移")
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path.home() / ".astrbot",
        help="AstrBot 数据根目录（默认 ~/.astrbot）",
    )
    parser.add_argument("--apply", action="store_true", help="实际写入（默认 dry-run）")
    parser.add_argument("--force", action="store_true", help="覆盖已存在的 mingling 配置/数据")
    args = parser.parse_args()
    return migrate(args.data_dir.expanduser(), args.apply, args.force)


if __name__ == "__main__":
    raise SystemExit(main())
