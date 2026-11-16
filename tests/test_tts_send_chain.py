"""回归测试：_send_tts 必须把语音组件包进 MessageChain 再 send。

生产根因（2026-08-18）：企业微信等平台 event.send 要求 message 带 .chain，
裸传 Record(...) 会抛 AttributeError: 'Record' object has no attribute 'chain'，
导致已合成的语音无法发出。修复后用 MessageChain([Record(...)]) 包裹。

v4.8.141：TTS 合成完全走 AstrBot provider（provider.get_audio），插件不再直连。
测试通过注入 fake provider 模拟合成，验证 MessageChain 包裹与 voice 透传。
"""


def _make_tts_plugin(extra_cfg=None):
    """构造带 fake AstrBot TTS provider 的插件（get_tts_provider 返回桩）。"""
    import asyncio
    from unittest.mock import patch

    from astrbot_plugin_mingbu import provider_router

    from main import MingbuPlugin
    from tests.test_admin_api import FakeContext

    cfg = {"background_watch_enabled": False, "provider_tts": "test-provider"}
    if extra_cfg:
        cfg.update(extra_cfg)
    plugin = MingbuPlugin(FakeContext(), cfg)

    captured = {}

    class _FakeTTSProvider:
        async def get_audio(self, text, voice=None):
            captured["called"] = True
            captured["voice"] = voice
            return b"RIFFfakespeech"

    async def fake_get_tts_provider(ctx, provider_id=""):
        captured["provider_id"] = provider_id
        return _FakeTTSProvider()

    return plugin, captured, patch.object(provider_router, "get_tts_provider", fake_get_tts_provider)


class FakeEvent:
    def __init__(self):
        self.sent = None

    async def send(self, message):
        self.sent = message


def test_send_tts_wraps_record_in_message_chain():
    import asyncio

    plugin, captured, patcher = _make_tts_plugin()

    with patcher:
        ev = FakeEvent()
        asyncio.run(plugin._send_tts(
            ev, "你好世界，这是一段用来测试语音合成是否被正确包裹的较长文本。"))

    assert captured.get("called"), "fake TTS provider.get_audio 未被调用"
    assert ev.sent is not None, "event.send 未被调用"
    # 必须是 MessageChain（带 .chain），而非裸 Record，否则企业微信会崩
    assert hasattr(ev.sent, "chain"), "语音未用 MessageChain 包裹（企业微信会崩）"
    assert len(ev.sent.chain) == 1
    assert getattr(ev.sent.chain[0], "type", None) == "Record"


def test_send_tts_passes_configured_voice():
    """回归：tts_voice 配置必须透传到 provider.get_audio 的 voice 参数。"""
    import asyncio

    plugin, captured, patcher = _make_tts_plugin({
        "tts_model": "FunAudioLLM/CosyVoice2-0.5B",
        "tts_voice": "FunAudioLLM/CosyVoice2-0.5B:ben",
    })

    with patcher:
        ev = FakeEvent()
        asyncio.run(plugin._send_tts(
            ev, "测试音色透传的较长文本，用来验证 voice 参数是否被传入 provider 合成。"))

    assert captured.get("voice") == "FunAudioLLM/CosyVoice2-0.5B:ben", "tts_voice 未透传到 provider.get_audio"
    assert ev.sent is not None, "event.send 未被调用"
    assert hasattr(ev.sent, "chain"), "语音未用 MessageChain 包裹"


def test_send_voice_with_backoff_retries_on_95001():
    """回归（2026-08-18）：微信客服 95001 限流时，_send_voice_with_backoff 应退避重试并最终成功。"""
    import asyncio

    from main import _send_voice_with_backoff

    class FakeEvent:
        def __init__(self):
            self.attempts = 0
            self.sent = []

        async def send(self, message):
            self.attempts += 1
            if self.attempts < 3:  # 前两次限流，第三次成功
                raise RuntimeError("Error code: 95001, message: send msg count limit")
            self.sent.append(message)

    ev = FakeEvent()
    ok = asyncio.run(_send_voice_with_backoff(ev, "data:audio/mp3;base64,AAAA", max_retries=3, backoff_base=0.01))
    assert ok is True
    assert ev.attempts == 3
    assert len(ev.sent) == 1


def test_send_voice_with_backoff_gives_up_after_retries():
    """回归：持续 95001 时应在达到最大重试次数后放弃并返回 False。"""
    import asyncio

    from main import _send_voice_with_backoff

    class FakeEvent:
        def __init__(self):
            self.attempts = 0

        async def send(self, message):
            self.attempts += 1
            raise RuntimeError("Error code: 95001, message: send msg count limit")

    ev = FakeEvent()
    ok = asyncio.run(_send_voice_with_backoff(ev, "x", max_retries=3, backoff_base=0.01))
    assert ok is False
    assert ev.attempts == 3


def test_send_tts_fallback_skips_voice_for_genie_provider():
    """回归（2026-08-18）：回退到不接受 voice 参数的 Provider（如 Genie）时不应抛 TypeError。"""
    import asyncio
    from unittest.mock import patch

    from main import MingbuPlugin
    from tests.test_admin_api import FakeContext

    # v4.8.141：合成一律走 Provider（直连已移除），这里直接构造不接受
    # voice 关键字的 Genie provider 验证安全探测
    plugin = MingbuPlugin(FakeContext(), {
        "background_watch_enabled": False,
        "tts_voice": "anna",  # 设置了 voice，但 Genie 不接受该关键字
    })

    class FakeEvent:
        def __init__(self):
            self.sent = None

        async def send(self, message):
            self.sent = message

    class GenieProvider:
        # Genie 的 get_audio 不接受 voice 关键字
        async def get_audio(self, text):
            return "/tmp/genie_test.wav"

    async def fake_get_tts_provider(ctx, provider_id=""):
        return GenieProvider()

    with patch("astrbot_plugin_mingbu.provider_router.get_tts_provider", fake_get_tts_provider):
        ev = FakeEvent()
        asyncio.run(plugin._send_tts(
            ev, "这是一段会触发语音合成的中文回复文本，用来验证 Genie 回退路径不会因 voice 参数报错。"))

    assert ev.sent is not None, "Genie 回退路径未发送语音（voice 参数导致 TypeError）"
    assert hasattr(ev.sent, "chain")
