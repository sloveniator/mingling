"""P2 缓存失效：面板改 provider 配置后清缓存，get_provider 重新读取最新配置。

见代码审查报告 P2：原 get_provider 命中 _provider_cache 后，面板改了外部服务
配置需重启才生效。新增 ProviderRouter.invalidate_cache()，由 system._save_config
在保存成功后调用，使配置免重启生效。
"""
from provider_router import ProviderRouter


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
        self._pm = pm

    def get_provider_by_id(self, pid):
        fn = getattr(self._pm, "get_provider_by_id", None)
        if callable(fn):
            return fn(pid)
        return None


class _FakePlugin:
    def __init__(self, cfg, ctx):
        self._config = cfg
        self.context = ctx


def _router(cfg):
    pm = _FakePM({pid: _FakeProvider(pid) for pid in ("main-provider", "dream-provider")})
    return ProviderRouter(_FakePlugin(cfg, _FakeCtx(pm)))


def test_invalidate_cache_rereads_updated_config():
    """清缓存前命中旧值，清缓存后按新配置重新解析（面板免重启生效）。"""
    pr = _router({"provider_dream": "dream-provider"})
    assert pr.get_provider("dream").id == "dream-provider"
    assert "dream" in pr._provider_cache  # 已缓存

    # 模拟面板改了外部服务配置
    pr.plugin._config["provider_dream"] = "main-provider"
    # 清缓存前仍命中旧值
    assert pr.get_provider("dream").id == "dream-provider"
    # 清缓存后应按新配置重新解析
    pr.invalidate_cache()
    assert pr.get_provider("dream").id == "main-provider"


def test_invalidate_cache_safe_when_unconfigured():
    """未配置 provider 时清缓存不应抛异常。"""
    pr = _router({})
    assert pr.get_provider("dream") is None
    pr.invalidate_cache()
    assert pr.get_provider("dream") is None


# ---- 复合 Provider ID 兼容（v4.8.102）：Dashboard API 返回 服务商/模型 复合 id ----

def test_provider_id_candidates_strips_model_suffix():
    """_provider_id_candidates 先原样、再剥离 /模型 后缀。"""
    from provider_router import _provider_id_candidates
    assert _provider_id_candidates("google_gemini/models/gemini-3.1-flash") == [
        "google_gemini/models/gemini-3.1-flash",
        "google_gemini",
    ]
    assert _provider_id_candidates("pure-provider") == ["pure-provider"]
    assert _provider_id_candidates("") == []


def test_get_provider_async_resolves_compound_id():
    """异步路径：复合 ID（服务商/模型）剥离后缀后仍能解析到 provider。"""
    import asyncio

    from provider_router import ProviderRouter

    class _AsyncPM(_FakePM):
        async def get_provider_by_id(self, pid):
            return self._by_id.get(pid)

    pm = _AsyncPM({"google_gemini": _FakeProvider("google_gemini")})
    cfg = {"provider_image_vision": "google_gemini/models/gemini-3.1-flash-lite-preview"}
    pr = ProviderRouter(_FakePlugin(cfg, _FakeCtx(pm)))
    p = asyncio.run(pr.get_provider_async("image_vision"))
    assert p is not None
    assert p.id == "google_gemini"


def test_get_provider_async_prefers_exact_match():
    """精确匹配优先：inst_map 里有完整复合 id 时用原样。"""
    import asyncio

    from provider_router import ProviderRouter

    class _AsyncPM(_FakePM):
        async def get_provider_by_id(self, pid):
            return self._by_id.get(pid)

    compound = "google_gemini/models/gemini-3.1-flash-lite-preview"
    pm = _AsyncPM({compound: _FakeProvider(compound)})
    cfg = {"provider_dream": compound}
    pr = ProviderRouter(_FakePlugin(cfg, _FakeCtx(pm)))
    p = asyncio.run(pr.get_provider_async("dream"))
    assert p is not None
    assert p.id == compound  # 精确命中复合 id 本体


def test_get_provider_sync_resolves_compound_id():
    """同步路径同样兼容复合 ID。"""
    from provider_router import ProviderRouter

    pm = _FakePM({"google_gemini": _FakeProvider("google_gemini")})
    cfg = {"provider_main_reply": "google_gemini/models/gemini-3.1-flash-lite-preview"}
    pr = ProviderRouter(_FakePlugin(cfg, _FakeCtx(pm)))
    p = pr.get_provider("main_reply")
    assert p is not None
    assert p.id == "google_gemini"


# ---- 复合 ID 兼容：_pick（TTS/STT）与 get_rerank/get_embedding（v4.8.102 补全） ----

def test_pick_resolves_compound_id():
    """_pick（STT/TTS 共用）对复合 Provider ID 剥离后缀后解析。"""
    import asyncio

    from provider_router import _pick

    class _STTProvider(_FakeProvider):
        def get_text(self, *a, **k):
            return ""

    class _AsyncPM(_FakePM):
        async def get_provider_by_id(self, pid):
            return self._by_id.get(pid)

    pm = _AsyncPM({"openai": _STTProvider("openai")})
    ctx = _FakeCtx(pm)
    p = asyncio.run(_pick(
        ctx, "openai/models/whisper-1",
        using="get_using_stt_provider",
        all_method="get_all_stt_providers",
        legacy="get_stt_provider",
        cap="get_text",
    ))
    assert p is not None and p.id == "openai"


def test_rerank_and_embedding_resolve_compound_id():
    """get_rerank_provider / get_embedding_provider 同样兼容复合 ID。"""
    import asyncio

    from provider_router import get_embedding_provider, get_rerank_provider

    class _CapProvider(_FakeProvider):
        def rerank(self, *a, **k):
            return None

        def embed(self, *a, **k):
            return None

        def get_embeddings(self, *a, **k):
            return []

    class _AsyncPM(_FakePM):
        async def get_provider_by_id(self, pid):
            return self._by_id.get(pid)

    pm = _AsyncPM({"jina": _CapProvider("jina")})
    ctx = _FakeCtx(pm)
    r = asyncio.run(get_rerank_provider(ctx, "jina/models/rerank-v3"))
    assert r is not None and r.id == "jina"
    e = asyncio.run(get_embedding_provider(ctx, "jina/models/embed-v3"))
    assert e is not None and e.id == "jina"


# ---- v4.8.141：LLM 请求完全交由 AstrBot Provider，移除「自定义直连优先」分支 ----
# 原 test_llm_request_direct_connect_priority / direct_fallback_to_provider 随直连
# 分支一并下线（直连语义已从运行时执行路径移除，凭据由 AstrBot Provider 承载）。
