"""STT / Rerank provider 兼容与降级测试。"""
import asyncio
from types import SimpleNamespace

import pytest

from provider_router import (
    call_rerank,
    get_rerank_provider,
    get_stt_provider,
    get_tts_provider,
    parse_rerank_indices,
    rerank_memory_list,
)
from stt import (
    collect_record_audio_urls,
    is_stt_enabled,
    strip_record_components,
    transcribe_audio,
    transcribe_event_voice,
)


class FakeSTT:
    def __init__(self, text="你好"):
        self.text = text

    def get_text(self, url):
        return self.text


class FakeTTS:
    def get_audio(self, text):
        return "/tmp/audio.wav"


class FakeRerank:
    def __init__(self, order=None):
        self.order = order  # 期望返回的索引顺序；None 表示反转

    def rerank(self, query, documents, top_n):
        n = len(documents)
        if self.order is not None:
            return [{"index": i, "relevance_score": 1.0 - i * 0.1} for i in self.order]
        return [{"index": i, "relevance_score": 1.0 - i * 0.1} for i in range(n)][::-1]


class FakeCtx:
    def __init__(self, stt=None, tts=None, rerank=None, by_id=None):
        self._stt = stt
        self._tts = tts
        self._rerank = rerank
        self._by_id = by_id or {}

    def get_using_stt_provider(self):
        return self._stt

    def get_using_tts_provider(self):
        return self._tts

    def get_all_stt_providers(self):
        return [self._stt] if self._stt else []

    def get_all_tts_providers(self):
        return [self._tts] if self._tts else []

    async def get_provider_by_id(self, pid):
        return self._by_id.get(pid)

    @property
    def provider_manager(self):
        pm = SimpleNamespace()
        pm.rerank_provider_insts = [self._rerank] if self._rerank else []
        return pm


def _run(coro):
    return asyncio.run(coro)


# ---------- STT provider 获取 ----------
def test_get_stt_provider_active():
    stt = FakeSTT()
    ctx = FakeCtx(stt=stt)
    assert _run(get_stt_provider(ctx)) is stt


def test_get_stt_provider_by_id():
    stt = FakeSTT()
    ctx = FakeCtx(by_id={"mystt": stt})
    assert _run(get_stt_provider(ctx, "mystt")) is stt


def test_get_stt_provider_none():
    ctx = FakeCtx()
    assert _run(get_stt_provider(ctx)) is None


def test_get_tts_provider_active():
    tts = FakeTTS()
    ctx = FakeCtx(tts=tts)
    assert _run(get_tts_provider(ctx)) is tts


# ---------- Rerank provider 获取 ----------
def test_get_rerank_provider_active():
    rk = FakeRerank()
    ctx = FakeCtx(rerank=rk)
    assert _run(get_rerank_provider(ctx)) is rk


def test_get_rerank_provider_by_id():
    rk = FakeRerank()
    ctx = FakeCtx(by_id={"myrk": rk})
    assert _run(get_rerank_provider(ctx, "myrk")) is rk


def test_get_rerank_provider_none():
    ctx = FakeCtx()
    assert _run(get_rerank_provider(ctx)) is None


# ---------- rerank 解析 / 调用 ----------
def test_parse_rerank_indices_dict():
    res = [{"index": 2}, {"index": 0}, {"index": 1}]
    assert parse_rerank_indices(res) == [2, 0, 1]


def test_parse_rerank_indices_tuple_and_int():
    res = [(1, 0.9), 0, {"index": 2}]
    assert parse_rerank_indices(res) == [1, 0, 2]


def test_parse_rerank_indices_no_index():
    # 仅含 document / relevance_score 无法映射，应跳过 -> 空
    res = [{"document": "x", "relevance_score": 0.9}]
    assert parse_rerank_indices(res) == []


def test_call_rerank_returns_indices():
    rk = FakeRerank()
    idxs = _run(call_rerank(rk, "query", ["a", "b", "c"], 3))
    # 反转 -> [2,1,0]
    assert idxs == [2, 1, 0]


def test_rerank_memory_list_reorders():
    mems = [SimpleNamespace(content="a"), SimpleNamespace(content="b"), SimpleNamespace(content="c")]
    rk = FakeRerank(order=[2, 0, 1])
    out = _run(rerank_memory_list(rk, "q", mems))
    assert [m.content for m in out] == ["c", "a", "b"]


def test_rerank_memory_list_no_provider():
    mems = [SimpleNamespace(content="a"), SimpleNamespace(content="b")]
    # 无 provider -> 原序
    assert _run(rerank_memory_list(None, "q", mems)) == mems


# ---------- STT 语音检测 / 转写 ----------
class FakeRecord:
    type = "Record"

    def __init__(self, url=""):
        self.url = url


class FakeEvent:
    def __init__(self, chain):
        self.message_obj = SimpleNamespace(message=chain)
        self.message_str = ""


def test_collect_record_audio_urls():
    chain = [FakeRecord(url="http://x.wav"), SimpleNamespace(type="Plain")]
    urls = collect_record_audio_urls(chain)
    assert urls == ["http://x.wav"]


def test_transcribe_audio_ok():
    ctx = FakeCtx(stt=FakeSTT("转写文本"))
    assert _run(transcribe_audio(ctx, "http://x.wav")) == "转写文本"


def test_transcribe_audio_no_provider():
    ctx = FakeCtx()
    assert _run(transcribe_audio(ctx, "http://x.wav")) is None


def test_transcribe_event_voice():
    event = FakeEvent([FakeRecord(url="http://x.wav")])
    ctx = FakeCtx(stt=FakeSTT("听到了"))
    assert _run(transcribe_event_voice(event, ctx)) == "听到了"


def test_transcribe_event_voice_no_record():
    event = FakeEvent([SimpleNamespace(type="Plain")])
    ctx = FakeCtx(stt=FakeSTT("听到了"))
    assert _run(transcribe_event_voice(event, ctx)) is None


# ---------- Record 移除（转写后防 audio 分片进请求体） ----------
def test_strip_record_components_removes_records():
    chain = [FakeRecord(url="http://x.wav"), SimpleNamespace(type="Plain"), FakeRecord(url="http://y.wav")]
    out = strip_record_components(chain)
    assert len(out) == 1
    assert out[0].type == "Plain"


def test_strip_record_components_no_record_unchanged():
    chain = [SimpleNamespace(type="Plain"), SimpleNamespace(type="Image")]
    assert strip_record_components(chain) == chain


def test_strip_record_components_non_list_passthrough():
    assert strip_record_components(None) is None
    assert strip_record_components("not-a-list") == "not-a-list"


# ---------- STT 启用判定（总开关逻辑） ----------
def test_is_stt_enabled_explicit_true():
    assert is_stt_enabled({"stt_enabled": True}) is True


def test_is_stt_enabled_explicit_false_even_with_base():
    # 显式关闭 -> 即使填了直连 base 也强制关
    assert is_stt_enabled({"stt_enabled": False, "stt_api_base": "https://api.siliconflow.cn/v1"}) is False



def test_is_stt_enabled_unset_no_base():
    # 未显式设置 + 无 base -> 关闭
    assert is_stt_enabled({}) is False
    assert is_stt_enabled({"stt_api_key": "sk-x"}) is False


# ---------- STT 只走 AstrBot Provider（直连已移除的不变量） ----------
# v4.8.141 起 STT 完全交由 AstrBot STT provider：插件不持有任何直连凭据，
# 也不做直连 HTTP 调用。历史版本的 stt_api_base/stt_api_key/stt_model 已从
# schema 移除且无任何读取处。下面用例守住这条不变量，防止将来回退。

import provider_router as _pr  # noqa: E402


def test_transcribe_ignores_direct_keys_uses_provider():
    """填了 stt_api_* 直连键也不生效，仍按 provider_stt 走 AstrBot provider。"""
    ctx = FakeCtx(stt=FakeSTT("来自 provider"))
    # 直连键即便全填，也不会触发直连；只走 provider
    out = _run(transcribe_event_voice(
        FakeEvent([FakeRecord(url="http://x.wav")]), ctx, "",
    ))
    assert out == "来自 provider"


def test_transcribe_never_makes_direct_http_call(monkeypatch):
    """即使无 provider，也不得发起任何 OpenAI 兼容 /audio/transcriptions 直连。"""
    import http_client as _hc
    seen = []

    def _spy(*a, **kw):
        seen.append((a, kw))
        raise AssertionError("STT 不应发起直连 HTTP 请求")

    monkeypatch.setattr(_hc, "request_bytes", _spy)
    monkeypatch.setattr(_hc, "request_json", _spy)
    ctx = FakeCtx(stt=None)  # 无 STT provider
    # 直连键即便填了也不该触发直连；provider_id 留空
    out = _run(transcribe_event_voice(
        FakeEvent([FakeRecord(url="http://x.wav")]), ctx, "",
    ))
    assert out is None
    assert seen == [], "无 provider 时应优雅返回 None，而非直连"


def test_transcribe_no_plugin_cfg_param_needed():
    """transcribe_audio / transcribe_event_voice 的直连 plugin_cfg 已移除，按 provider_id 路由。"""
    import inspect
    for fn in (transcribe_audio, transcribe_event_voice):
        params = list(inspect.signature(fn).parameters)
        assert "plugin_cfg" not in params, f"{fn.__name__} 不应再接受 plugin_cfg"
        assert "provider_id" in params
