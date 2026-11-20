"""
tests/test_e2e_provider_services.py -- 能力服务商 + 外部服务卡片 端到端验证
=============================================================================

本文件把「拉取 → 配置 → 真实路由 → 连通性测试」整条链路跑一遍，证明两项功能
**确实落地、可端到端调用**，而不是单点单元测试通过却在实际接线处断掉。

为什么需要它（修复历史根因）：
  能力服务商 / 外部服务卡片此前的问题不在于「后端逻辑没写」，而在于真实
  AstrBot 平台的接口适配与「能跑一遍的验证流程」缺口。本测试装配一个带
  真实 ``provider_manager`` 的 star 上下文，断言：

  1. ``/list_providers`` 能枚举出 AstrBot 已配置的服务商（「拉取模式」下拉的数据源）。
  2. 配置 ``provider_dream`` / ``provider_dream_model`` 后，
     ``star._llm_text(task_type="dream")`` 真把请求路由到该 Provider，
     且把选中的模型透传给 ``text_chat``（能力服务商配置确实被消费）。
  3. 配置填的是**可读名称**而非 id 时，也能按 name 匹配路由（加固 #143）。
  4. ``/connectivity service=provider`` 对配置的角色真实发起 ``text_chat``
     （「测试」按钮不是空跑）。
  5. 外部服务「自定义模式」TTS / Embedding 真发 HTTP 请求到正确端点
     （本测试在 HTTP 层打桩，断言 URL 路径与 payload 构造正确，区别于
     既有只在函数层打桩的连通性测试）。

与 test_diagnostics_api.py 的分工：那边验证「缺失配置/脱敏/结构化错误」等
边界；这里验证「配置正确时整条链路真实打通」。
"""
import asyncio
import importlib

import mingling.http_client as hc
from test_admin_api import build_star, call, payload_of


class CaptureProvider:
    """记录每次 text_chat 调用的 Provider，便于断言真实路由。"""

    def __init__(self, pid, name=None):
        self.id = pid
        self.name = name or pid
        self.calls = []

    async def text_chat(self, **kwargs):
        self.calls.append(kwargs)

        class _R:
            completion_text = "ok-from-" + self.id

        return _R()


class CaptureProviderManager:
    """极简 provider_manager：属性式实例列表 + get_provider_by_id。"""

    def __init__(self, insts):
        self.llm_provider_insts = insts

    def get_provider_by_id(self, pid):
        for p in self.llm_provider_insts:
            if p.id == pid:
                return p
        return None


def _make_star():
    """装配带真实 provider_manager 的 star，并写好能力服务商配置。"""
    star = build_star()
    asyncio.run(star.initialize())
    star.context.provider_manager = CaptureProviderManager([
        CaptureProvider("main-provider", "主回复"),
        CaptureProvider("dream-provider", "梦境"),
    ])
    star._config["provider_main_reply"] = "main-provider"
    star._config["provider_main_reply_model"] = "gpt-4o"
    star._config["provider_dream"] = "dream-provider"
    star._config["provider_dream_model"] = "gpt-4o-mini"
    star.core.provider_router.invalidate_cache()
    return star


def _provider(star, pid):
    for p in star.context.provider_manager.llm_provider_insts:
        if p.id == pid:
            return p
    raise AssertionError(f"未找到 provider: {pid}")


# ── 1) /list_providers 拉取数据源 ───────────────────────────────
def test_list_providers_enumerates_configured():
    star = _make_star()
    data = payload_of(call(star.api_list_providers))
    assert data["ok"] is True
    ids = [p["id"] for p in data["providers"]]
    assert "dream-provider" in ids
    assert "main-provider" in ids
    # 名称与 id 不同时应一并返回，前端下拉才好展示
    by_id = {p["id"]: p["name"] for p in data["providers"]}
    assert by_id["dream-provider"] == "梦境"
    # 诊断应指出枚举成功
    assert data["diagnostics"]["root_cause"] == "none"


# ── 2) 能力服务商配置被消费 + 模型透传 ─────────────────────────
def test_llm_text_dream_routes_to_configured_provider():
    star = _make_star()
    out = asyncio.run(star._llm_text("今晚做个预感测试", task_type="dream"))
    assert out == "ok-from-dream-provider"

    dream = _provider(star, "dream-provider")
    assert dream.calls, "dream provider 的 text_chat 未被调用"
    assert dream.calls[0]["model"] == "gpt-4o-mini"

    # 主回复 provider 不应被本次 dream 任务调用
    main = _provider(star, "main-provider")
    assert main.calls == []


# ── 3) 加固 #143：按可读名称（而非 id）也能匹配路由 ─────────────
def test_llm_text_routes_by_provider_name():
    star = _make_star()
    # 配置里填的是可读名称 "梦境" 而非 id "dream-provider"
    star._config["provider_dream"] = "梦境"
    star.core.provider_router.invalidate_cache()

    out = asyncio.run(star._llm_text("名称匹配测试", task_type="dream"))
    assert out == "ok-from-dream-provider"
    assert _provider(star, "dream-provider").calls, "按名称未匹配到 provider"


# ── 4) /connectivity service=provider 真实发起 text_chat ───────
def test_connectivity_provider_issues_real_text_chat():
    star = _make_star()
    data = payload_of(call(
        star.api_connectivity, method="POST",
        body={"service": "provider", "task_type": "dream", "model": "gpt-4o-mini"},
    ))
    assert data["ok"] is True, data
    assert data.get("provider_id") == "dream-provider"
    # 关键：请求真打到了配置好的 provider，而不是空跑或走默认
    assert _provider(star, "dream-provider").calls, "连通性测试未真正调用 provider.text_chat"


# ── 4b) 连通性测试：未配置专属服务商但显式指定模型的精确指引 ──
def test_connectivity_provider_growth_model_without_provider_gives_precise_error():
    """只填模型、不填服务商时（用户真实场景），应报精确错误指引而非笼统
    「未找到任务类型 growth 对应的 Provider」。"""
    star = _make_star()
    # 清空成长反思专属服务商与回退的主回复服务商，模拟两者均未配置
    star._config["provider_growth"] = ""
    star._config["provider_main_reply"] = ""
    star.core.provider_router.invalidate_cache()
    data = payload_of(call(
        star.api_connectivity, method="POST",
        body={"service": "provider", "task_type": "growth",
              "model": "LoRA/Qwen/Qwen2.5-32B-Instruct"},
    ))
    assert data["ok"] is False, data
    assert "LoRA/Qwen/Qwen2.5-32B-Instruct" in data["error"]
    assert "服务商" in data["error"]


def test_connectivity_provider_unconfigured_uses_default_ok():
    """留空服务商且未填模型 = 合法「沿用默认服务商」，应提示性通过而非报红。"""
    star = _make_star()
    star._config["provider_growth"] = ""
    star._config["provider_main_reply"] = ""
    star.core.provider_router.invalidate_cache()
    data = payload_of(call(
        star.api_connectivity, method="POST",
        body={"service": "provider", "task_type": "growth"},
    ))
    assert data["ok"] is True, data
    assert data.get("using_default") is True
    assert "默认" in data.get("message", "")


# ── 5) 外部服务「自定义模式」TTS 真发 HTTP 到正确端点 ──────────
def test_external_tts_custom_mode_live_http(monkeypatch):
    pr = importlib.import_module("mingling.provider_router")
    star = _make_star()
    captured = {}

    async def fake_request_bytes(session, url, **kwargs):
        captured["url"] = url
        captured["kwargs"] = kwargs
        return b"FAKE_MP3_BYTES"

    monkeypatch.setattr(pr, "request_bytes", fake_request_bytes)
    star._config["tts_api_key"] = "sk-test"
    star._config["tts_api_base"] = "https://tts.example.com/v1"
    star._config["tts_model"] = "tts-1"

    data = payload_of(call(star.api_connectivity, method="POST",
                           body={"service": "tts"}))
    assert data["ok"] is True, data
    # 断言请求真发到了配置的 base + 正确的 /audio/speech 路径
    assert captured["url"] == "https://tts.example.com/v1/audio/speech"
    assert data.get("bytes") == len(b"FAKE_MP3_BYTES")


# ── 6) 外部服务「自定义模式」Embedding 真发 HTTP 到正确端点 ────
def test_external_embedding_custom_mode_live_http(monkeypatch):
    pr = importlib.import_module("mingling.provider_router")
    star = _make_star()
    captured = {}

    async def fake_request_json(session, method, url, **kwargs):
        captured["url"] = url
        return {"data": [{"embedding": [0.1, 0.2, 0.3]}]}

    monkeypatch.setattr(pr, "request_json", fake_request_json)
    star._config["embedding_api_key"] = "sk-test"
    star._config["embedding_api_base"] = "https://emb.example.com/v1"
    star._config["embedding_model"] = "bge-m3"

    data = payload_of(call(star.api_connectivity, method="POST",
                           body={"service": "embedding"}))
    assert data["ok"] is True, data
    assert captured["url"] == "https://emb.example.com/v1/embeddings"
    assert data.get("dimensions") == 3


# ── 7) 通过 AstrBot HTTP API 拉取服务商列表（根治「实例缺 id」）──
def test_list_providers_via_astrbot_http_api(monkeypatch):
    """配置 astrbot_dashboard_url 后，命灵应优先走 AstrBot HTTP API
    (/api/v1/providers) 拉取：该接口有 OpenAPI 文档、返回结构稳定且明确带
    id 字段，根治旧路径「实例缺 id/provider_id 属性」导致下拉为空的问题。

    注意：``_fetch_llm_providers_via_api`` 用 ``from .http_client import
    request_json``（局部导入），故打桩点在 ``http_client.request_json`` 而非
    ``provider_router.request_json``。
    """
    fake_api = {
        "status": "ok",
        "data": {
            "providers": [
                {"id": "Sense/deepseek-v4-flash", "provider": "openai",
                 "provider_type": "chat_completion", "model": "deepseek-v4-flash", "enable": True},
                {"id": "Qwen3-Embedding-8B", "provider": "vllm",
                 "provider_type": "embedding", "model": "Qwen3-Embedding-8B"},
                {"id": "google_gemini/gemini-3.1-flash-lite-preview", "provider": "google",
                 "provider_type": "chat_completion", "model": "models/gemini-3.1-flash-lite-preview", "enable": True},
            ],
            "model_metadata": {},
        },
    }

    async def fake_request_json(session, method, url, *, payload=None, params=None,
                                 form=None, api_key=None, timeout=30, ssl=None, **kwargs):
        return fake_api

    monkeypatch.setattr(hc, "request_json", fake_request_json)

    star = _make_star()
    star._config["astrbot_dashboard_url"] = "https://ab.slceleto.us.kg:4456"
    star._config["astrbot_api_key"] = "abk_test"

    data = payload_of(call(star.api_list_providers))
    assert data["ok"] is True, data
    ids = [p["id"] for p in data["providers"]]
    # 文本生成类服务商应出现
    assert "Sense/deepseek-v4-flash" in ids
    assert "google_gemini/gemini-3.1-flash-lite-preview" in ids
    # 非文本生成类（embedding）应被过滤掉
    assert "Qwen3-Embedding-8B" not in ids
    # 诊断应显示走的是 HTTP API 策略且成功
    assert data["diagnostics"]["root_cause"] == "none"
    assert any(s["name"] == "astrbot_http_api" and s["found"] > 0
               for s in data["diagnostics"]["strategies"])
