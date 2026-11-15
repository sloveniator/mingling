"""默认对话服务商回退 + 异步 get_provider_by_id 兼容（v4.8.81 修复）。

真机日志（AstrBot v4.26.5）暴露两个根因缺陷：

1. **Context 没有 llm_request()**
   ``ProviderRouter.llm_request`` 的兜底段判据是 ``hasattr(ctx, "llm_request")``，
   而真实 ``astrbot.core.star.Context`` 只有 ``llm_generate(chat_provider_id=...)``
   与 ``get_using_provider_async()``，**从来没有** ``llm_request``。于是所有未单独
   配置服务商的子任务（image_vision / dream / growth / proactive / news / search /
   merged_forward）一律走到 ``logger.warning("没有可用 Provider")`` 并返回 None。
   日志实证：``[mingbu] 没有可用 Provider，任务 image_vision 失败``。

2. **provider_manager.get_provider_by_id 是 async def**
   同步的 ``get_provider()`` 直接调用只拿到 coroutine，却把它当 Provider 缓存，
   于是「明明填了 provider_xxx 也解析不到」，还刷 never-awaited 警告。

本文件用「贴近真机结构」的假 Context（无 llm_request、异步
get_provider_by_id、带 get_using_provider_async）守住这两条不变量。
"""
import asyncio

from provider_router import (
    ProviderRouter,
    default_llm_request,
    get_default_chat_provider,
)


class _CaptureProvider:
    """记录 text_chat 入参的假 Provider。"""

    def __init__(self, pid):
        self.id = pid
        self.calls = []

    async def text_chat(self, **kwargs):
        self.calls.append(kwargs)

        class _R:
            completion_text = f"reply-from-{kwargs.get('model') or 'default'}"

        return _R()


class _AsyncPM:
    """真机结构：provider_manager.get_provider_by_id 为 async def。"""

    def __init__(self, by_id=None, insts=None):
        self._by_id = by_id or {}
        self.provider_insts = list(insts or [])

    async def get_provider_by_id(self, pid):
        return self._by_id.get(pid)


class _RealisticCtx:
    """贴近真实 AstrBot Context：没有 llm_request，get_provider_by_id 同步。"""

    def __init__(self, pm=None, using=None, using_async=None, all_providers=None):
        if pm is not None:
            self.provider_manager = pm
        self._using = using
        self._using_async = using_async
        self._all = all_providers
        if using_async is not None:
            self.get_using_provider_async = self._get_using_provider_async
        if using is not None:
            self.get_using_provider = self._get_using_provider
        if all_providers is not None:
            self.get_all_providers = self._get_all_providers

    async def _get_using_provider_async(self, umo=None):
        return self._using_async

    def _get_using_provider(self, umo=None):
        return self._using

    def _get_all_providers(self):
        return list(self._all or [])


class _FakePlugin:
    def __init__(self, cfg, ctx):
        self._config = cfg
        self.context = ctx


def _req(prompt="hi", session="s1"):
    from astrbot.api.provider import ProviderRequest

    return ProviderRequest(prompt=prompt, session_id=session)


# ── 缺陷 2：异步 get_provider_by_id ────────────────────────────────


def test_async_get_provider_by_id_is_awaited():
    """provider_manager.get_provider_by_id 为 async 时也要解析出真 Provider。

    修复前：同步调用返回 coroutine → hasattr(p, "text_chat") 为 False →
    请求落到兜底段，配置好的专属服务商形同虚设。
    """
    prov = _CaptureProvider("vision-provider")
    ctx = _RealisticCtx(pm=_AsyncPM({"vision-provider": prov}))
    pr = ProviderRouter(_FakePlugin({"provider_image_vision": "vision-provider"}, ctx))

    got = asyncio.run(pr.get_provider_async("image_vision"))
    assert got is prov, "异步 get_provider_by_id 必须被 await 后取到实例"

    resp = asyncio.run(pr.llm_request("image_vision", _req(), model="gpt-4o"))
    assert len(prov.calls) == 1
    assert prov.calls[0]["model"] == "gpt-4o"
    assert resp is not None


def test_sync_get_provider_skips_coroutine_and_falls_back():
    """同步 get_provider 遇到 async pm 时不得把 coroutine 当 Provider 缓存。"""
    prov = _CaptureProvider("dream-provider")
    ctx = _RealisticCtx(pm=_AsyncPM({"dream-provider": prov}))
    # 真实 Context.get_provider_by_id 是同步的，作为同步路径的兜底
    ctx.get_provider_by_id = lambda pid: prov if pid == "dream-provider" else None
    pr = ProviderRouter(_FakePlugin({"provider_dream": "dream-provider"}, ctx))

    got = pr.get_provider("dream")
    assert got is prov
    assert not asyncio.iscoroutine(got)
    assert not asyncio.iscoroutine(pr._provider_cache.get("dream"))


def test_sync_get_provider_returns_none_when_only_async_available():
    """只有异步入口、且无同步兜底时，同步解析应干净地返回 None（而非 coroutine）。"""
    prov = _CaptureProvider("dream-provider")
    ctx = _RealisticCtx(pm=_AsyncPM({"dream-provider": prov}))
    pr = ProviderRouter(_FakePlugin({"provider_dream": "dream-provider"}, ctx))
    got = pr.get_provider("dream")
    assert got is None
    assert "dream" not in pr._provider_cache


# ── 缺陷 1：默认服务商回退 ─────────────────────────────────────────


def test_fallback_uses_get_using_provider_async():
    """未配 provider_* 且 Context 无 llm_request 时，走 get_using_provider_async。"""
    default = _CaptureProvider("default-async")
    ctx = _RealisticCtx(pm=_AsyncPM(), using_async=default)
    assert not hasattr(ctx, "llm_request"), "本用例必须模拟真机（无 llm_request）"
    pr = ProviderRouter(_FakePlugin({}, ctx))

    resp = asyncio.run(pr.llm_request("image_vision", _req(), model="gpt-4o"))
    assert resp is not None, "修复前这里为 None，图片转述等子任务全部静默失效"
    assert len(default.calls) == 1
    assert default.calls[0]["model"] == "gpt-4o"


def test_fallback_uses_deprecated_sync_get_using_provider():
    """只有旧同步 get_using_provider（deprecated）时同样要能用。"""
    default = _CaptureProvider("default-sync")
    ctx = _RealisticCtx(pm=_AsyncPM(), using=default)
    pr = ProviderRouter(_FakePlugin({}, ctx))
    resp = asyncio.run(pr.llm_request("dream", _req()))
    assert resp is not None
    assert len(default.calls) == 1


def test_fallback_uses_all_providers_list():
    """会话未设默认服务商时，取 get_all_providers() 首个可对话实例。"""
    default = _CaptureProvider("first-of-all")
    ctx = _RealisticCtx(pm=_AsyncPM(), all_providers=[default])
    pr = ProviderRouter(_FakePlugin({}, ctx))
    resp = asyncio.run(pr.llm_request("news", _req()))
    assert resp is not None
    assert len(default.calls) == 1


def test_fallback_uses_provider_insts_when_no_accessor():
    """连 get_all_providers 都没有时，退到 provider_manager.provider_insts。"""
    default = _CaptureProvider("inst-0")
    ctx = _RealisticCtx(pm=_AsyncPM(insts=[default]))
    pr = ProviderRouter(_FakePlugin({}, ctx))
    resp = asyncio.run(pr.llm_request("growth", _req()))
    assert resp is not None
    assert len(default.calls) == 1


def test_legacy_llm_request_still_preferred():
    """旧版 AstrBot / 测试桩提供 Context.llm_request 时保持原行为，不回归。"""
    calls = []

    class _LegacyCtx:
        async def llm_request(self, req):
            calls.append(req)
            return "legacy-ok"

    pr = ProviderRouter(_FakePlugin({}, _LegacyCtx()))
    assert asyncio.run(pr.llm_request("dream", _req())) == "legacy-ok"
    assert len(calls) == 1


def test_returns_none_when_truly_no_provider():
    """确实没有任何服务商时仍返回 None（优雅降级，不抛异常）。"""
    ctx = _RealisticCtx(pm=_AsyncPM())
    pr = ProviderRouter(_FakePlugin({}, ctx))
    assert asyncio.run(pr.llm_request("search", _req())) is None


def test_get_default_chat_provider_ignores_non_chat_instances():
    """只认带 text_chat 的实例，TTS/STT 之类不得被当默认对话服务商。"""

    class _TTSOnly:
        async def get_audio(self, *a, **k):
            return b""

    chat = _CaptureProvider("chat")
    ctx = _RealisticCtx(pm=_AsyncPM(insts=[_TTSOnly(), chat]))
    assert asyncio.run(get_default_chat_provider(ctx)) is chat


def test_get_default_chat_provider_handles_none_ctx():
    assert asyncio.run(get_default_chat_provider(None)) is None


# ── _llm_text 无 provider_router 时的兜底 ─────────────────────────


def test_default_llm_request_helper_without_legacy_api():
    """default_llm_request 在无 llm_request 的真机 Context 上也能出结果。"""
    default = _CaptureProvider("helper-default")
    ctx = _RealisticCtx(pm=_AsyncPM(), using_async=default)
    resp = asyncio.run(default_llm_request(ctx, _req(), model="deepseek-chat"))
    assert resp is not None
    assert default.calls[0]["model"] == "deepseek-chat"


def test_llm_text_without_router_uses_default_provider():
    """provider_router 缺失时 _llm_text 不再 AttributeError，而是走默认服务商。"""
    from star_mixins.llm import LLMTextMixin

    default = _CaptureProvider("mixin-default")
    ctx = _RealisticCtx(pm=_AsyncPM(), using_async=default)

    class _FakeCore:
        _in_sys_llm = False

    class _Mixin(LLMTextMixin):
        def __init__(self):
            self._config = {"provider_main_reply_model": "main-model"}
            self.context = ctx
            self.provider_router = None
            self.core = _FakeCore()
            self.token_monitor = None

    text = asyncio.run(_Mixin()._llm_text("prompt", task_type="proactive"))
    assert len(default.calls) == 1
    assert default.calls[0]["model"] == "main-model"
    assert text.startswith("reply-from-")
