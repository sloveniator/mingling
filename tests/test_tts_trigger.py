"""TTS 自动触发策略（长文本>=阈值必触发，短文本不触发）与长文本切分单元测试。

需求（2026-08-18）：短文本给文字、长文本(>=500)给语音；故默认随机触发概率已置 0，
短文本不再随机朗读。随机分支通过显式传入 rate 保留覆盖。
"""
from main import (
    TTS_LONG_TEXT_THRESHOLD,
    TTS_MAX_CHUNK_CHARS,
    TTS_MIN_RANDOM_LEN,
    TTS_RANDOM_RATE,
    _split_tts_chunks,
    _user_requests_voice,
    tts_should_trigger,
)


def test_long_text_always_triggers():
    text = "命" * (TTS_LONG_TEXT_THRESHOLD + 1)
    ok, reason = tts_should_trigger(text)
    assert ok and reason == "长文本"


def test_threshold_boundary():
    # 恰好等于阈值视为长文本
    ok, reason = tts_should_trigger("命" * TTS_LONG_TEXT_THRESHOLD)
    assert ok and reason == "长文本"
    # 少一个字符不再按长文本触发（默认 rate=0，随机不命中）
    ok2, reason2 = tts_should_trigger("命" * (TTS_LONG_TEXT_THRESHOLD - 1), rng=lambda: 1.0)
    assert not ok2 and reason2 == ""


def test_short_text_random_hit_when_rate_set():
    # 随机分支仍保留：显式传入 rate 时短文本可命中
    text = "命" * (TTS_MIN_RANDOM_LEN + 5)
    ok, reason = tts_should_trigger(text, rate=0.086, rng=lambda: 0.0)
    assert ok and reason == "随机"


def test_short_text_random_miss_when_rate_set():
    text = "命" * (TTS_MIN_RANDOM_LEN + 5)
    ok, reason = tts_should_trigger(text, rate=0.086, rng=lambda: 1.0)
    assert not ok and reason == ""


def test_below_min_random_len_never_random():
    ok, reason = tts_should_trigger("短", rng=lambda: 0.0)
    assert not ok and reason == ""


def test_empty_never_triggers():
    ok, reason = tts_should_trigger("")
    assert not ok and reason == ""


def test_default_random_rate_disabled():
    # 需求：短文本给文字，默认不随机朗读
    assert TTS_RANDOM_RATE == 0.0


# ---- 长文本切分（微信 60s 限制 45007 修复）----


def test_short_text_is_single_chunk():
    text = "这是一段很短的朗读文本。"
    chunks = _split_tts_chunks(text, 200)
    assert chunks == [text]


def test_empty_text_no_chunks():
    assert _split_tts_chunks("", 200) == []
    assert _split_tts_chunks("   ", 200) == []


def test_long_text_splits_by_sentence_boundary():
    # 三段各约 120 字，单段上限 200 → 期望 3 段（均在断句符处切分）
    seg = "命" * 120 + "。"
    text = seg + seg + seg
    chunks = _split_tts_chunks(text, 200)
    assert len(chunks) == 3
    for c in chunks:
        assert len(c) <= 200
        assert c.endswith("。")


def test_no_boundary_long_run_is_hard_split():
    # 无断句符的 500 字长串，单段上限 200 → 硬切为 3 段
    text = "命" * 500
    chunks = _split_tts_chunks(text, 200)
    assert len(chunks) == 3
    assert sum(len(c) for c in chunks) == 500


def test_chunk_respects_max_chars():
    text = "事" * 95 + "。" + "事" * 95 + "。"
    chunks = _split_tts_chunks(text, 100)
    for c in chunks:
        assert len(c) <= 100


def test_default_max_chars_constant_used():
    # 不传 max_chars 时回落到默认常量
    text = "语" * 250 + "。"
    chunks = _split_tts_chunks(text)
    assert all(len(c) <= TTS_MAX_CHUNK_CHARS for c in chunks)


# ---- 用户点名语音（v4.8.128）----

def test_user_requests_voice_positive():
    for t in ("发语音", "发条语音", "发一段语音", "来段语音", "用语音说",
              "想听语音", "换语音", "要语音", "语音呢", "语音回复我",
              "语音形式发我", "说给我听", "读给我听", "你发个语音呗"):
        assert _user_requests_voice(t), t


def test_user_requests_voice_negative_context():
    for t in ("别发语音", "不要发语音", "不要语音了", "别再发语音",
              "不想听语音", "禁止发语音", "转文字", "转成文字",
              "转换为文字吧", ""):
        assert not _user_requests_voice(t), t


def test_user_requests_voice_false_positives_guarded():
    # 含「语音」但不构成点名诉求的普通话术不触发
    for t in ("语音识别怎么用", "刚才的语音识别错了"):
        assert not _user_requests_voice(t), t
