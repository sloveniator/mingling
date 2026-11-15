"""配置 schema、静态前端字段和关键动态字段的一致性护栏。"""

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_static_frontend_keys_are_declared_in_schema():
    schema = json.loads((ROOT / "_conf_schema.json").read_text(encoding="utf-8"))
    html = (ROOT / "pages" / "admin" / "index.html").read_text(encoding="utf-8")
    keys = set(re.findall(r'data-key="([^"]+)"', html))
    assert keys
    assert keys <= set(schema)


def test_dynamic_provider_keys_are_declared_in_schema():
    schema = json.loads((ROOT / "_conf_schema.json").read_text(encoding="utf-8"))
    js = (ROOT / "pages" / "admin" / "app.js").read_text(encoding="utf-8")
    assert 'data-key="provider_${r.key}"' in js
    for role in ("main_reply", "dream", "growth", "proactive", "news", "search"):
        assert f"provider_{role}" in schema
        assert f"provider_{role}_model" in schema
