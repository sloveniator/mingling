"""
tests/test_config_hotreload.py -- 面板配置校验与热重载护栏
=========================================================
``/config`` POST 承担两件容易悄悄失效的事：

1. **校验**：以 ``_conf_schema.json`` 为唯一真值源（建议④）。
   拆分前 ``_validate_config`` 手写了一份类型/边界/enum 分支，与 schema
   构成两份真值；新增字段只改 schema 就会静默漏校验。
2. **热重载**：保存后把值推到**活着的**引擎实例上，免重启生效。
   这条路径此前无测试——改成只写 KV 不推引擎，功能测试全绿，
   但用户在面板上改完「暗盘缓存 TTL」得重启才生效，且无任何报错。

两者都是「不报错的静默退化」，只能靠断言钉死。
"""
import asyncio
import json

import pytest

from stars.apis.system import KV_RUNTIME_CONFIG
from tests.test_admin_api import build_star, call, payload_of


def _post_config(star, body):
    return payload_of(call(star.api_config, method="POST", body=body))


def _get_config(star):
    return payload_of(call(star.api_config, method="GET"))


@pytest.fixture()
def star():
    s = build_star()
    asyncio.run(s.initialize())
    return s


# ══════════════════════════════════════════════════════════════
# schema 驱动的校验（建议④）
# ══════════════════════════════════════════════════════════════

class TestSchemaDrivenValidation:
    def test_schema_loads_and_is_cached(self):
        """schema 必须能读到，且不是每次调用都重新 open 文件。"""
        from stars.apis._schema import load_schema

        first = load_schema()
        assert isinstance(first, dict) and first, "读不到 _conf_schema.json"
        assert "proactive_interval_min" in first
        # 命中缓存时返回同一个对象
        assert load_schema() is first

    def test_int_bounds_enforced_from_schema(self):
        """proactive_interval_min 在 schema 里声明 5..1440。"""
        from stars.apis._schema import coerce_by_schema

        _, errors = coerce_by_schema({"proactive_interval_min": 2})
        assert errors and "不能小于" in errors[0]

        _, errors = coerce_by_schema({"proactive_interval_min": 99999})
        assert errors and "不能大于" in errors[0]

        data, errors = coerce_by_schema({"proactive_interval_min": 42})
        assert not errors
        assert data["proactive_interval_min"] == 42

    def test_float_bounds_enforced_from_schema(self):
        """weight_threshold 在 schema 里声明 0..1。"""
        from stars.apis._schema import coerce_by_schema

        _, errors = coerce_by_schema({"weight_threshold": 1.5})
        assert errors

        data, errors = coerce_by_schema({"weight_threshold": 0.8})
        assert not errors
        assert data["weight_threshold"] == pytest.approx(0.8)

    def test_enum_enforced_from_schema(self):
        """llm_persona_style 是枚举字段。"""
        from stars.apis._schema import coerce_by_schema

        _, errors = coerce_by_schema({"llm_persona_style": "不存在的风格"})
        assert errors and "必须是" in errors[0]

        data, errors = coerce_by_schema({"llm_persona_style": "mysterious"})
        assert not errors
        assert data["llm_persona_style"] == "mysterious"

    def test_unknown_keys_are_dropped(self):
        """未在 schema 声明的键不得写进运行时覆盖层。"""
        from stars.apis._schema import coerce_by_schema

        data, errors = coerce_by_schema({"__evil__": 1, "weight_threshold": 0.5})
        assert not errors
        assert "__evil__" not in data
        assert "weight_threshold" in data

    def test_string_false_is_not_true(self):
        """表单提交的 "false" 用 bool() 转换会反转成 True。"""
        from stars.apis._schema import coerce_by_schema

        data, _ = coerce_by_schema({"mingbu_interpret_enabled": "false"})
        assert data["mingbu_interpret_enabled"] is False

        data, _ = coerce_by_schema({"mingbu_interpret_enabled": True})
        assert data["mingbu_interpret_enabled"] is True

    def test_type_error_is_reported_not_raised(self):
        from stars.apis._schema import coerce_by_schema

        _, errors = coerce_by_schema({"proactive_interval_min": "不是数字"})
        assert errors and "格式错误" in errors[0]

    def test_api_rejects_out_of_range_value(self, star):
        """越界值必须被接口拒绝，而不是写进 KV。"""
        resp = call(star.api_config, method="POST",
                    body={"proactive_interval_min": 99999})
        assert resp.status_code == 400
        assert _get_config(star).get("proactive_interval_min") != 99999


# ══════════════════════════════════════════════════════════════
# 热重载（建议⑩）
# ══════════════════════════════════════════════════════════════

class TestHotReload:
    def test_darkchannel_cache_ttl_reaches_diviner(self, star):
        """改 TTL 后必须落到活着的 diviner 实例上，而不是等重启。"""
        assert star.core.diviner is not None
        _post_config(star, {"darkchannel_cache_ttl": 1234})
        assert star.core.diviner._cache_ttl == 1234

    def test_weight_threshold_reaches_inspector(self, star):
        insp = star.core.conversation_inspector
        if insp is None:
            pytest.skip("对话检视模块未启用")
        _post_config(star, {"weight_threshold": 0.77})
        assert insp.weight_threshold == pytest.approx(0.77)

    def test_proactive_interval_reaches_engine(self):
        """主动引擎默认关闭，这里显式开启后再验证热更。"""
        s = build_star(proactive_enabled=True)
        asyncio.run(s.initialize())
        if s.core.proactive is None:
            pytest.skip("主动引擎未启用")
        _post_config(s, {"proactive_interval_min": 77})
        assert s.core.proactive._interval_min == 77

    def test_hot_reload_map_targets_exist(self, star):
        """热更映射表里的引擎字段必须真实存在，否则等于静默失效。"""
        from stars.apis._schema import load_schema
        from stars.apis.system import _HOT_RELOAD_MAP

        schema = load_schema()
        for cfg_key, (engine_attr, field) in _HOT_RELOAD_MAP.items():
            assert cfg_key in schema, f"热更键 {cfg_key} 未在 schema 中声明"
            engine = getattr(star.core, engine_attr, None)
            if engine is None:
                continue  # 该引擎按配置未启用，跳过
            assert hasattr(engine, field), (
                f"{engine_attr} 上不存在字段 {field}，热更映射已失效"
            )

    def test_missing_engine_does_not_break_save(self):
        """引擎未启用时保存配置不能报错（只是跳过热更）。"""
        s = build_star(proactive_enabled=False)
        asyncio.run(s.initialize())
        resp = call(s.api_config, method="POST",
                    body={"proactive_interval_min": 60})
        assert payload_of(resp).get("ok") is True

    def test_saved_value_survives_reread(self, star):
        """热更之外，值本身要真的落进运行时覆盖层。"""
        _post_config(star, {"darkchannel_cache_ttl": 4321})
        assert _get_config(star)["darkchannel_cache_ttl"] == 4321

    def test_partial_save_does_not_wipe_other_keys(self, star):
        """面板只提交改动项；未提交的键在持久层 KV 里不能被清掉。

        这是真正的回归断言：旧实现把 coerced 直接整条 put_kv，面板只提交改动项时，
        其它已存键会被静默冲掉；老测试只看合并后的内存态（self._config 已被 update），
        所以即便 KV 被清空也依旧全绿——典型的假阳性。这里直接读持久层 KV 验证。
        """
        _post_config(star, {"darkchannel_cache_ttl": 111})
        _post_config(star, {"weight_threshold": 0.5})

        raw = star._kv_store.get(KV_RUNTIME_CONFIG)
        assert raw is not None, "运行时覆盖层未写入 KV"
        persisted = json.loads(raw)
        assert persisted.get("darkchannel_cache_ttl") == 111
        assert persisted.get("weight_threshold") == pytest.approx(0.5)

    def test_partial_save_survives_kv_reload(self, star):
        """保存→重建（模拟重启读同一份 KV）→再从 KV 重载，未提交的键不能丢。"""
        _post_config(star, {"darkchannel_cache_ttl": 222})
        _post_config(star, {"proactive_interval_min": 55})

        # 模拟进程重启：全新 star 复用同一份 KV 持久层。
        # build_star 的 KV 桩基于闭包局部变量而非 self._kv_store，故显式把
        # 重建实例的 KV 方法指向同一存储，真正模拟「重启后读 KV」。
        rebuilt = build_star()
        store = star._kv_store

        async def _get(key, default=None):
            return store.get(key, default)

        async def _put(key, value):
            store[key] = value

        rebuilt.get_kv_data = _get
        rebuilt.put_kv_data = _put
        asyncio.run(rebuilt.initialize())

        cfg = _get_config(rebuilt)
        assert cfg.get("darkchannel_cache_ttl") == 222
        assert cfg.get("proactive_interval_min") == 55
