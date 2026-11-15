"""
tests/test_model_catalog_persist.py -- 模型目录持久化（修复「拉取保存后再次进入全部消失」）
=================================================================================

根因：``fetchModels`` 把拉取的模型只存进前端内存变量 ``MC_MODELS``，从不写后端；
重新进入页面 ``loadModelsTab`` 仅从 ``/config`` 加载配置（仅有各角色选中的模型名），
``MC_MODELS`` 为空 → 目录与下拉全部消失。

修复：新增 ``/model_catalog_save``（落盘到 KV ``mingbu.model_catalog``）与
``/model_catalog_get``（读取），拉取成功后立即持久化，页面加载时恢复。
"""
import asyncio
import json

from test_admin_api import build_star, call, payload_of


def test_model_catalog_persist_routes_registered():
    """/model_catalog_save 与 /model_catalog_get 路由必须注册（否则前端恢复/落盘调用 404）。"""
    from core import PLUGIN_NAME
    from stars.admin import StarAdmin

    class _Dummy:
        def __getattr__(self, name):
            return lambda *a, **k: None

    specs = StarAdmin._route_specs(_Dummy())
    routes = {r: (m, d) for (r, h, m, d) in specs}
    save = f"/{PLUGIN_NAME}/model_catalog_save"
    get = f"/{PLUGIN_NAME}/model_catalog_get"
    assert save in routes, "model_catalog_save 路由未注册"
    assert "POST" in routes[save][0]
    assert get in routes, "model_catalog_get 路由未注册"
    assert "GET" in routes[get][0]


def test_model_catalog_save_persists_and_get_returns_it():
    star = build_star()
    models = [
        {"id": "gpt-4o", "name": "gpt-4o", "cats": ["chat"], "owned_by": "openai"},
        {"id": "text-embedding-3", "name": "text-embedding-3", "cats": ["embedding"], "owned_by": "openai"},
    ]
    body = {
        "base_url": "https://api.example.com/v1",
        "api_key": "sk-test",
        "extra_headers": {"X-Title": "mingbu"},
        "models": models,
    }
    resp = call(star.api_model_catalog_save, "POST", body=body)
    p = payload_of(resp)
    assert p["ok"] is True, p
    assert p["count"] == 2

    # 读取不再返回明文 API Key；extra_headers 经 JSON 往返仍是 dict。
    g = call(star.api_model_catalog_get, "GET")
    gp = payload_of(g)
    assert gp["ok"] is True
    assert gp["catalog"] is not None
    assert gp["catalog"]["base_url"] == "https://api.example.com/v1"
    assert gp["catalog"]["api_key"] == ""
    assert gp["catalog"]["api_key_masked"] == "*******"
    assert gp["catalog"]["extra_headers"] == {"X-Title": "mingbu"}
    assert gp["catalog"]["models"] == models


def test_model_catalog_get_returns_null_when_empty():
    star = build_star()
    g = call(star.api_model_catalog_get, "GET")
    gp = payload_of(g)
    assert gp["ok"] is True
    assert gp["catalog"] is None


def test_model_catalog_save_overwrites_previous():
    star = build_star()
    call(star.api_model_catalog_save, "POST", body={"base_url": "u1", "api_key": "k1", "extra_headers": "", "models": [{"id": "a"}]})
    call(star.api_model_catalog_save, "POST", body={"base_url": "u2", "api_key": "k2", "extra_headers": "", "models": [{"id": "b"}, {"id": "c"}]})
    g = call(star.api_model_catalog_get, "GET")
    gp = payload_of(g)
    assert gp["catalog"]["base_url"] == "u2"
    assert len(gp["catalog"]["models"]) == 2


def test_model_catalog_falls_back_to_saved_mc_api_key(monkeypatch):
    """前端留空时，/model_catalog 应自动使用运行时配置中已保存的 mc_api_key。"""
    star = build_star(mc_api_key="sk-saved")
    captured = {}

    async def fake_fetch(base_url, api_key="", extra_headers=None):
        captured.update({"base_url": base_url, "api_key": api_key, "extra_headers": extra_headers})
        return {"ok": True, "models": [{"id": "gpt-4o", "cats": ["chat"]}]}

    monkeypatch.setattr("provider_router.fetch_model_catalog", fake_fetch)
    resp = call(star.api_model_catalog, "POST", body={"base_url": "https://api.example.com/v1", "api_key": "", "extra_headers": ""})
    p = payload_of(resp)
    assert p["ok"] is True
    assert captured["api_key"] == "sk-saved"
    assert captured["base_url"] == "https://api.example.com/v1"


def test_model_catalog_prefers_explicit_api_key_over_saved(monkeypatch):
    """前端显式填写 api_key 时，不应被保存的 mc_api_key 覆盖。"""
    star = build_star(mc_api_key="sk-saved")
    captured = {}

    async def fake_fetch(base_url, api_key="", extra_headers=None):
        captured["api_key"] = api_key
        return {"ok": True, "models": []}

    monkeypatch.setattr("provider_router.fetch_model_catalog", fake_fetch)
    call(star.api_model_catalog, "POST", body={"base_url": "https://api.example.com/v1", "api_key": "sk-explicit", "extra_headers": ""})
    assert captured["api_key"] == "sk-explicit"


def test_model_catalog_save_invalid_kv_still_returns_error():
    """put_kv_data 抛异常时返回 error_response 而非 500。"""
    star = build_star()

    async def boom(key, value):
        raise RuntimeError("disk full")

    star.put_kv_data = boom
    resp = call(star.api_model_catalog_save, "POST", body={"base_url": "u", "api_key": "k", "extra_headers": "", "models": []})
    p = payload_of(resp)
    # AstrBot error_response 形态：{"status": "error", "message": ...}（无 ok 键）
    assert p.get("status") == "error", p
    assert "保存模型目录失败" in p.get("message", ""), p


def test_model_catalog_save_persists_credentials_to_runtime_config():
    """「拉取」保存时应把 mc_base_url/mc_api_key/mc_extra_headers 一并写入运行时配置，
    否则仅靠「保存全部」一条路径，刷新后凭据会消失。"""
    star = build_star()
    call(star.api_model_catalog_save, "POST", body={
        "base_url": "https://api.example.com/v1",
        "api_key": "sk-test",
        "extra_headers": '{"X-Title":"mingbu"}',
        "models": [{"id": "gpt-4o", "cats": ["chat"]}],
    })
    runtime = json.loads(star._kv_store["mingbu.runtime_config"])
    assert runtime["mc_base_url"] == "https://api.example.com/v1"
    assert runtime["mc_api_key"] == "sk-test"
    assert runtime["mc_extra_headers"] == '{"X-Title":"mingbu"}'


def test_model_catalog_save_empty_key_preserves_existing_mc_api_key():
    """重新拉取时若 Key 留空（已掩码），不得把已保存的 mc_api_key 清掉。"""
    star = build_star(mc_api_key="sk-saved")
    call(star.api_model_catalog_save, "POST", body={
        "base_url": "https://api.example.com/v1",
        "api_key": "",
        "extra_headers": "",
        "models": [{"id": "gpt-4o", "cats": ["chat"]}],
    })
    runtime = json.loads(star._kv_store["mingbu.runtime_config"])
    # api_key 为空 → 不写入覆盖层 → 运行时配置里没有 mc_api_key，静态配置里的 sk-saved 保持有效
    assert "mc_api_key" not in runtime


def test_model_catalog_falls_back_to_saved_mc_base_url(monkeypatch):
    """前端留空时，/model_catalog 应自动使用运行时配置中已保存的 mc_base_url。"""
    star = build_star(mc_base_url="https://saved.example.com/v1", mc_api_key="sk-saved")
    captured = {}

    async def fake_fetch(base_url, api_key="", extra_headers=None):
        captured.update({"base_url": base_url, "api_key": api_key})
        return {"ok": True, "models": [{"id": "gpt-4o", "cats": ["chat"]}]}

    monkeypatch.setattr("provider_router.fetch_model_catalog", fake_fetch)
    call(star.api_model_catalog, "POST", body={"base_url": "", "api_key": "", "extra_headers": ""})
    assert captured["base_url"] == "https://saved.example.com/v1"
    assert captured["api_key"] == "sk-saved"
