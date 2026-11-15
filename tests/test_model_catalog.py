"""模型统一配置：分类 + 拉取 + 路由注册测试。"""
import asyncio
from types import SimpleNamespace

import pytest

from provider_router import ProviderRouter, _classify_model, fetch_model_catalog


# ───────────────────────── 分类 ─────────────────────────
@pytest.mark.parametrize("mid,expected", [
    ("gpt-4o", {"vision", "chat"}),
    ("gpt-4o-mini", {"vision", "chat"}),
    ("gpt-4-vision-preview", {"vision", "chat"}),
    ("gpt-4-turbo", {"vision", "chat"}),
    ("claude-3.5-sonnet", {"vision", "chat"}),
    ("qwen2.5-vl-72b-instruct", {"vision", "chat"}),
    ("llava-13b", {"vision", "chat"}),
    # 纯文本模型：不能进 vision，否则会污染「图片转述」的下拉选项
    ("gpt-4", {"chat"}),
    ("gpt-4-32k", {"chat"}),
    ("gpt-3.5-turbo", {"chat"}),
    ("deepseek-chat", {"chat"}),
    ("text-embedding-3-small", {"embedding"}),
    ("BAAI/bge-m3", {"embedding"}),
    ("BAAI/bge-reranker-v2-m3", {"rerank"}),
    ("jina-reranker-v2", {"rerank"}),
    ("whisper-1", {"stt"}),
    ("FunAudioLLM/CosyVoice-300M", {"tts"}),
    ("fish-speech-1.5", {"tts"}),
])
def test_classify_model(mid, expected):
    assert set(_classify_model(mid)) == expected


# ───────────────────────── 拉取：缺地址 ─────────────────────────
def test_fetch_empty_base():
    res = asyncio.run(fetch_model_catalog(""))
    assert res["ok"] is False
    assert "Base URL" in res["error"]


# ───────────────────────── 拉取：成功（mock aiohttp） ─────────────────────────
class _FakeResp:
    def __init__(self, status, payload=None, text=""):
        self.status = status
        self._payload = payload
        self._text = text

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def json(self):
        return self._payload

    async def text(self):
        return self._text


class _FakeSession:
    def __init__(self, resp):
        self._resp = resp

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    def get(self, *a, **k):
        return self._resp


_PAYLOAD = {
    "object": "list",
    "data": [
        {"id": "gpt-4o", "owned_by": "openai"},
        {"id": "text-embedding-3-small", "owned_by": "openai"},
        {"id": "BAAI/bge-reranker-v2-m3", "owned_by": "siliconflow"},
        {"id": "whisper-1", "owned_by": "openai"},
        {"id": "deepseek-chat", "owned_by": "deepseek"},
    ],
}


def test_fetch_model_catalog_success(monkeypatch):
    fake = _FakeSession(_FakeResp(200, _PAYLOAD))
    monkeypatch.setattr("provider_router.aiohttp.ClientSession", lambda *a, **k: fake)
    res = asyncio.run(fetch_model_catalog("https://api.example.com/v1", "sk-x"))
    assert res["ok"] is True
    ids = {m["id"] for m in res["models"]}
    assert ids == {
        "gpt-4o", "text-embedding-3-small",
        "BAAI/bge-reranker-v2-m3", "whisper-1", "deepseek-chat",
    }
    by_id = {m["id"]: m for m in res["models"]}
    assert set(by_id["gpt-4o"]["cats"]) == {"vision", "chat"}
    assert by_id["text-embedding-3-small"]["cats"] == ["embedding"]
    assert by_id["deepseek-chat"]["cats"] == ["chat"]
    # 按 id 排序
    assert [m["id"] for m in res["models"]][0] == "BAAI/bge-reranker-v2-m3"


def test_fetch_model_catalog_http_error(monkeypatch):
    fake = _FakeSession(_FakeResp(401, text="unauthorized"))
    monkeypatch.setattr("provider_router.aiohttp.ClientSession", lambda *a, **k: fake)
    res = asyncio.run(fetch_model_catalog("https://api.example.com/v1", "bad"))
    assert res["ok"] is False
    assert "401" in res["error"]


# ───────────────────────── 路由注册 ─────────────────────────
def test_model_catalog_route_registered():
    from core import PLUGIN_NAME
    from stars.admin import StarAdmin

    # _route_specs 仅引用 self.api_* 处理器名，用惰性桩即可，无需真正实例化
    class _Dummy:
        def __getattr__(self, name):
            return lambda *a, **k: None

    specs = StarAdmin._route_specs(_Dummy())
    routes = [(r, m, d) for (r, h, m, d) in specs]
    target = f"/{PLUGIN_NAME}/model_catalog"
    matched = [r for r in routes if r[0] == target]
    assert matched, "model_catalog 路由未注册"
    assert "POST" in matched[0][1]


# ───────────────────────── Provider 路由（外部服务可代替 AstrBot） ─────────────────────────


class _FakeProvider:
    def __init__(self, pid):
        self.id = pid

    def text_chat(self, *a, **k):
        return None


class _FakePM:
    def __init__(self, by_id):
        self._by_id = by_id

    def get_provider_by_id(self, pid):
        return self._by_id.get(pid)


class _FakeCtx:
    def __init__(self, pm):
        self.provider_manager = pm


class _FakePlugin:
    def __init__(self, cfg, ctx):
        self._config = cfg
        self.context = ctx


def _router(cfg):
    pm = _FakePM({pid: _FakeProvider(pid) for pid in ("main-provider", "dream-provider")})
    return ProviderRouter(_FakePlugin(cfg, _FakeCtx(pm)))


def test_get_provider_uses_configured_id():
    """配置了 provider_dream，外部服务商应被选中（代替 AstrBot 默认）。"""
    pr = _router({"provider_dream": "dream-provider"})
    got = pr.get_provider("dream")
    assert got is not None and got.id == "dream-provider"


def test_get_provider_empty_returns_none():
    """未配置任何 provider_*，返回 None → 调用方回退 AstrBot 默认服务商。"""
    pr = _router({})
    assert pr.get_provider("dream") is None
    assert pr.get_provider("growth") is None


def test_get_provider_falls_back_to_main_reply():
    """具体任务未填、但主回复填了 → 复用主回复服务商。"""
    pr = _router({"provider_main_reply": "main-provider"})
    got = pr.get_provider("dream")
    assert got is not None and got.id == "main-provider"


def test_get_provider_unknown_id_returns_none():
    """填了不存在的 Provider ID，不崩溃、返回 None（优雅降级）。"""
    pr = _router({"provider_dream": "nope"})
    assert pr.get_provider("dream") is None


# ───────────────────────── 价格：内置价表 ─────────────────────────
def test_builtin_price_for_match():
    from provider_router import _builtin_price_for
    assert _builtin_price_for("gpt-4o") == (2.5, 10)
    assert _builtin_price_for("claude-3-5-sonnet") == (3, 15)
    assert _builtin_price_for("deepseek-chat") == (0.27, 1.10)


def test_builtin_price_specific_before_generic():
    # 更具体的型号必须先命中，不能被泛化 "gpt-4o" 抢走
    from provider_router import _builtin_price_for
    assert _builtin_price_for("gpt-4o-mini") == (0.15, 0.6)


def test_builtin_price_no_match_local():
    from provider_router import _builtin_price_for
    # 不在价表中的自定义名返回 None（本地/开源部署且无对应 API 型号）
    assert _builtin_price_for("my-custom-model") is None
    assert _builtin_price_for("foo-bar-baz") is None


# ───────────────────────── 价格：端点提取（OpenRouter 风格） ─────────────────────────
def test_extract_provider_price_openrouter():
    from provider_router import _extract_provider_price
    raw = {"pricing": {"prompt": "0.0000025", "completion": "0.00001"}}
    assert _extract_provider_price(raw) == (2.5, 10)


def test_extract_provider_price_missing():
    from provider_router import _extract_provider_price
    assert _extract_provider_price({"id": "x"}) is None
    assert _extract_provider_price({}) is None


# ───────────────────────── 价格：组合解析 ─────────────────────────
def test_resolve_price_provider_priority():
    from provider_router import _resolve_model_price
    raw = {"pricing": {"prompt": "0.0000025", "completion": "0.00001"}}
    p = _resolve_model_price(raw, "gpt-4o")
    assert p["source"] == "provider"
    assert p["in"] == 2.5 and p["out"] == 10
    assert p["unit"] == "USD/1M"


def test_resolve_price_builtin_fallback():
    from provider_router import _resolve_model_price
    p = _resolve_model_price({}, "claude-3-5-sonnet")
    assert p["source"] == "builtin"
    assert p["in"] == 3 and p["out"] == 15


def test_resolve_price_none_for_local():
    from provider_router import _resolve_model_price
    assert _resolve_model_price({}, "local-llama-8b") is None


# ───────────────────────── 价格：随拉取结果返回 ─────────────────────────
def test_fetch_attaches_price(monkeypatch):
    from provider_router import fetch_model_catalog
    fake = _FakeSession(_FakeResp(200, _PAYLOAD))
    monkeypatch.setattr("provider_router.aiohttp.ClientSession", lambda *a, **k: fake)
    res = asyncio.run(fetch_model_catalog("https://api.example.com/v1", "sk-x"))
    by_id = {m["id"]: m for m in res["models"]}
    assert by_id["gpt-4o"]["price"]["source"] == "builtin"
    assert by_id["deepseek-chat"]["price"] == {
        "in": 0.27, "out": 1.10, "unit": "USD/1M", "source": "builtin",
    }
    # 端点精确价优先于内置表
    payload = {"data": [{"id": "gpt-4o", "pricing": {"prompt": "0.000001", "completion": "0.000003"}}]}
    fake2 = _FakeSession(_FakeResp(200, payload))
    monkeypatch.setattr("provider_router.aiohttp.ClientSession", lambda *a, **k: fake2)
    res2 = asyncio.run(fetch_model_catalog("https://api.example.com/v1", "sk-x"))
    p = res2["models"][0]["price"]
    assert p["source"] == "provider"
    assert p["in"] == 1.0 and p["out"] == 3.0
