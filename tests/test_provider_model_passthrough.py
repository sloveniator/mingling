"""模型透传：llm_request / _llm_text 把配置中的 provider_*_model 传给 provider.text_chat。

覆盖 v4.8.20 新增能力：「渠道与服务配置」里为每个能力服务商单独指定模型。"""
import asyncio

import pytest

from provider_router import ProviderRouter


class _CaptureProvider:
    def __init__(self, pid):
        self.id = pid
        self.calls = []

    async def text_chat(self, **kwargs):
        self.calls.append(kwargs)
        # 返回一个带 completion_text 的对象，让调用方不崩
        class _R:
            completion_text = "ok"
        return _R()


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
    providers = {
        "main-provider": _CaptureProvider("main-provider"),
        "dream-provider": _CaptureProvider("dream-provider"),
    }
    pm = _FakePM(providers)
    return ProviderRouter(_FakePlugin(cfg, _FakeCtx(pm))), providers


def test_llm_request_passes_model_to_text_chat():
    """指定 provider 和 model 时，model 透传给 provider.text_chat。"""
    pr, providers = _router({"provider_dream": "dream-provider", "provider_dream_model": "gpt-4o-mini"})
    from astrbot.api.provider import ProviderRequest
    req = ProviderRequest(prompt="hi", session_id="s1")
    asyncio.run(pr.llm_request("dream", req, model="gpt-4o-mini"))
    assert len(providers["dream-provider"].calls) == 1
    assert providers["dream-provider"].calls[0]["model"] == "gpt-4o-mini"


def test_llm_request_model_fallback_from_config():
    """llm_request 未显式传 model，但配置有 provider_*_model，应读取配置透传。"""
    pr, providers = _router({"provider_dream": "dream-provider", "provider_dream_model": "deepseek-chat"})
    from astrbot.api.provider import ProviderRequest
    req = ProviderRequest(prompt="hi", session_id="s1")
    # 调用方（如 _llm_text）会把 model 作为参数传入；这里模拟该行为
    asyncio.run(pr.llm_request("dream", req, model=pr.plugin._config.get("provider_dream_model", "")))
    assert providers["dream-provider"].calls[0]["model"] == "deepseek-chat"


def test_llm_text_injects_model_from_config(monkeypatch):
    """_llm_text 从 _config 读取 provider_*_model 并设置 req.model + 传给 llm_request。"""
    from star_mixins.llm import LLMTextMixin

    captured = {}

    class _FakeProviderRouter:
        async def llm_request(self, task_type, req, model=None):
            captured["task_type"] = task_type
            captured["model"] = model
            captured["req_model"] = getattr(req, "model", None)
            return None

    class _FakeCore:
        _in_sys_llm = False

    class _FakeMonitor:
        async def check_limit(self, task_type: str = "") -> bool:
            return True

        async def record_call(self, *args, **kwargs):
            pass

    class _Mixin(LLMTextMixin):
        def __init__(self):
            self._config = {
                "provider_growth_model": "gpt-4o",
                "provider_main_reply_model": "main-model",
            }
            self.context = None
            self.provider_router = _FakeProviderRouter()
            self.core = _FakeCore()
            self.token_monitor = _FakeMonitor()

    m = _Mixin()
    asyncio.run(m._llm_text("prompt", task_type="growth"))
    assert captured["task_type"] == "growth"
    assert captured["model"] == "gpt-4o"
    assert captured["req_model"] == "gpt-4o"


def test_llm_text_falls_back_to_main_reply_model(monkeypatch):
    """子任务未配置模型时，沿用 provider_main_reply_model。"""
    from star_mixins.llm import LLMTextMixin

    captured = {}

    class _FakeProviderRouter:
        async def llm_request(self, task_type, req, model=None):
            captured["model"] = model
            return None

    class _FakeCore:
        _in_sys_llm = False

    class _FakeMonitor:
        async def check_limit(self, task_type: str = "") -> bool:
            return True

        async def record_call(self, *args, **kwargs):
            pass

    class _Mixin(LLMTextMixin):
        def __init__(self):
            self._config = {"provider_main_reply_model": "fallback-model"}
            self.context = None
            self.provider_router = _FakeProviderRouter()
            self.core = _FakeCore()
            self.token_monitor = _FakeMonitor()

    m = _Mixin()
    asyncio.run(m._llm_text("prompt", task_type="proactive"))
    assert captured["model"] == "fallback-model"
