"""配置 schema 与前端字段一致性（v5 面板）。"""
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_frontend_keys_are_declared_in_schema():
    schema = json.loads((ROOT / "_conf_schema.json").read_text(encoding="utf-8"))
    html = (ROOT / "pages" / "admin" / "index.html").read_text(encoding="utf-8")
    keys = set(re.findall(r'data-key="([^"]+)"', html))
    assert keys
    assert keys <= set(schema), f"面板字段未在 schema 声明: {sorted(keys - set(schema))}"


def test_frontend_covers_v5_active_config():
    html = (ROOT / "pages" / "admin" / "index.html").read_text(encoding="utf-8")
    for key in (
        "enabled", "semantic_router_enabled", "semantic_router_confidence",
        "state_layer_max_tokens", "proactive_max_per_day",
        "counseling_enabled", "health_enabled", "growth_engine_enabled",
        "mingbu_api_url",
    ):
        assert f'data-key="{key}"' in html, f"面板缺少活动配置 {key}"


def test_frontend_uses_v5_admin_endpoints():
    js = (ROOT / "pages" / "admin" / "app.js").read_text(encoding="utf-8")
    for endpoint in ("overview", "config", "users", "timeline"):
        assert f'apiGet("{endpoint}"' in js or f'apiPost("{endpoint}"' in js
    assert "provider_${r.key}" not in js
