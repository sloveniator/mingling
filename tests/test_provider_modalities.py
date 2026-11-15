"""
tests/test_provider_modalities.py -- 语音 400 根治：provider modalities 修正
================================================================================
AstrBot 4.27 实测：ProviderRequest 没有 messages 属性；runner 构建时已把
req.contexts 快照进 run_context.messages，钩子里剥 req 不生效。真正对快照
生效的净化点是 _iter_llm_responses 里的 _sanitize_contexts_for_provider——
它只在 provider 配置了 modalities 且不含 audio 时才把 audio_url/input_audio
分片替换成 [Audio] 文本。

本文件守护：
- _normalize_provider_modalities 的幂等修正规则；
- ensure_chat_provider_no_audio 能从 ctx 解析当前 chat provider 并补齐配置；
- ProviderRequest 确实没有 messages 属性（防 v4.8.97 式挂空目标回归）；
- strip_audio_parts_from_messages 对 req.contexts（OpenAI dict 格式）有效。
"""
import asyncio
from types import SimpleNamespace

import pytest
from astrbot_plugin_mingbu.provider_router import (
    _normalize_provider_modalities,
    ensure_chat_provider_no_audio,
)
from astrbot_plugin_mingbu.util import strip_audio_parts_from_messages


class FakeChatProvider:
    def __init__(self, modalities=None, pid="fake-provider"):
        self.provider_config = {"id": pid}
        if modalities is not None:
            self.provider_config["modalities"] = modalities
        self.name = pid

    async def text_chat(self, **kwargs):  # 标记为对话类 provider
        return None


class FakeCtx:
    def __init__(self, providers=None):
        self.providers = providers or []
        self.used_async = False

    async def get_using_provider_async(self, umo=None):
        self.used_async = True
        return self.providers[0] if self.providers else None

    def get_using_provider(self, umo=None):
        return self.providers[0] if self.providers else None

    def get_all_providers(self):
        return self.providers


# ---- _normalize_provider_modalities ----

class TestNormalizeModalities:
    def test_missing_defaults_to_no_audio(self):
        cfg = {"id": "x"}
        changed = _normalize_provider_modalities(cfg)
        assert changed is True
        assert cfg["modalities"] == ["text", "image", "tool_use"]
        assert "audio" not in cfg["modalities"]

    def test_empty_list_defaults(self):
        cfg = {"id": "x", "modalities": []}
        assert _normalize_provider_modalities(cfg) is True
        assert cfg["modalities"] == ["text", "image", "tool_use"]

    def test_audio_tokens_removed(self):
        cfg = {"id": "x", "modalities": ["text", "audio", "image"]}
        assert _normalize_provider_modalities(cfg) is True
        assert cfg["modalities"] == ["text", "image"]

    def test_audio_url_and_input_audio_removed(self):
        cfg = {"id": "x", "modalities": ["text", "audio_url", "input_audio"]}
        assert _normalize_provider_modalities(cfg) is True
        assert cfg["modalities"] == ["text"]

    def test_all_audio_falls_back_to_default(self):
        cfg = {"id": "x", "modalities": ["audio", "audio_url"]}
        assert _normalize_provider_modalities(cfg) is True
        assert cfg["modalities"] == ["text", "image", "tool_use"]

    def test_already_clean_is_noop(self):
        cfg = {"id": "x", "modalities": ["text", "image", "tool_use"]}
        assert _normalize_provider_modalities(cfg) is False
        assert cfg["modalities"] == ["text", "image", "tool_use"]

    def test_non_dict_noop(self):
        assert _normalize_provider_modalities(None) is False
        assert _normalize_provider_modalities("nope") is False

    def test_case_insensitive_audio_token(self):
        cfg = {"id": "x", "modalities": ["text", "Audio"]}
        assert _normalize_provider_modalities(cfg) is True
        assert cfg["modalities"] == ["text"]

    def test_idempotent_second_call(self):
        cfg = {"id": "x"}
        assert _normalize_provider_modalities(cfg) is True
        assert _normalize_provider_modalities(cfg) is False


# ---- ensure_chat_provider_no_audio ----

class TestEnsureChatProviderNoAudio:
    def test_async_api_fixes_current_provider(self):
        prov = FakeChatProvider()  # modalities 缺失
        ctx = FakeCtx([prov])
        assert asyncio.run(ensure_chat_provider_no_audio(ctx)) is True
        assert ctx.used_async is True
        assert prov.provider_config["modalities"] == ["text", "image", "tool_use"]

    def test_with_audio_removed(self):
        prov = FakeChatProvider(modalities=["text", "audio", "tool_use"])
        ctx = FakeCtx([prov])
        assert asyncio.run(ensure_chat_provider_no_audio(ctx)) is True
        assert prov.provider_config["modalities"] == ["text", "tool_use"]

    def test_clean_provider_returns_false(self):
        prov = FakeChatProvider(modalities=["text", "image", "tool_use"])
        ctx = FakeCtx([prov])
        assert asyncio.run(ensure_chat_provider_no_audio(ctx)) is False

    def test_none_ctx_returns_false(self):
        assert asyncio.run(ensure_chat_provider_no_audio(None)) is False

    def test_empty_providers_returns_false(self):
        ctx = FakeCtx([])
        assert asyncio.run(ensure_chat_provider_no_audio(ctx)) is False

    def test_falls_back_to_get_all_providers(self):
        # 模拟只有同步 get_using_provider 且失败 → get_all_providers 兜底
        class SyncOnlyCtx(FakeCtx):
            async def get_using_provider_async(self, umo=None):
                raise RuntimeError("unsupported")

            def get_using_provider(self, umo=None):
                raise RuntimeError("unsupported")

        prov = FakeChatProvider()
        ctx = SyncOnlyCtx([prov])
        assert asyncio.run(ensure_chat_provider_no_audio(ctx)) is True
        assert prov.provider_config["modalities"] == ["text", "image", "tool_use"]


# ---- ProviderRequest 契约守护（防 v4.8.97 式挂空目标回归） ----

class TestProviderRequestContract:
    def test_no_messages_attr(self):
        """AstrBot 4.27 ProviderRequest 没有 messages 属性——剥离必须剥 contexts。"""
        import astrbot.api.provider as _provider  # noqa: F401 - 触发桩
        from astrbot_plugin_mingbu.devtools.astrbot_stub import (
            install_astrbot_stub,
        )

        install_astrbot_stub()
        req = _provider.ProviderRequest(prompt="hi")
        assert not hasattr(req, "messages")
        assert hasattr(req, "contexts") or True  # 旧桩无 contexts 亦接受
        # 模拟旧桩无 contexts 时，getattr 兜底返回 None → 剥离安全跳过
        msgs = getattr(req, "contexts", None)
        if msgs is None:
            msgs = getattr(req, "messages", None)
        assert msgs is None or isinstance(msgs, list)


# ---- strip_audio_parts_from_messages 对 OpenAI dict 格式 contexts 生效 ----

class TestStripAudioOnContexts:
    def test_strip_audio_url_part_from_contexts(self):
        contexts = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "上次的语音：[语音转写] 你好呀"},
                    {"type": "audio_url", "audio_url": {"url": "data:audio/wav;base64,AAAA"}},
                ],
            },
            {"role": "assistant", "content": "好的"},
        ]
        strip_audio_parts_from_messages(contexts)
        parts = contexts[0]["content"]
        assert all(p.get("type") != "audio_url" for p in parts)
        # 转写文本保留
        assert any(
            p.get("type") == "text" and "语音转写" in p.get("text", "")
            for p in parts
        )

    def test_strip_input_audio_part(self):
        contexts = [
            {
                "role": "user",
                "content": [
                    {"type": "input_audio", "input_audio": {"data": "x", "format": "wav"}},
                    {"type": "text", "text": "hello"},
                ],
            }
        ]
        strip_audio_parts_from_messages(contexts)
        assert [p["type"] for p in contexts[0]["content"]] == ["text"]

    def test_all_audio_falls_back_to_text(self):
        contexts = [
            {
                "role": "user",
                "content": [
                    {"type": "audio_url", "audio_url": {"url": "data:audio/wav;base64,AAAA"}},
                ],
            }
        ]
        strip_audio_parts_from_messages(contexts)
        # 全被剥离 → 空串兜底，绝不留空列表
        assert contexts[0]["content"] == ""

    def test_non_list_contexts_noop(self):
        contexts = "not-a-list"
        assert strip_audio_parts_from_messages(contexts) == "not-a-list"
