"""tests/test_tts_config.py -- tts_config.py 单一数据源单元测试（v4.9.0）
=====================================================================

覆盖三块资产：
1. 音色库数据完整性（24 官方音色、白名单集合、provider 回退）；
2. resolve_dashscope_voice 白名单回退（v4.8.132 线上 bug 的固化逻辑）；
3. TTSSettings / load_tts_settings 扁平配置收敛（默认值、钳制、脏数据容错）。
"""
import json

import pytest

from tts_config import (
    _DASHSCOPE_TTS_VOICES,
    _DASHSCOPE_VOICE_NAMES,
    DEFAULT_TTS_LONG_TEXT_THRESHOLD,
    DEFAULT_TTS_VOICE,
    TTS_MAX_CHUNK_CHARS,
    get_builtin_voices,
    is_dashscope_model,
    is_valid_dashscope_voice,
    load_tts_settings,
    resolve_dashscope_voice,
)

# ── 音色库数据 ──────────────────────────────────────────────────────────


class TestVoiceData:
    def test_dashscope_24_voices(self):
        assert len(_DASHSCOPE_TTS_VOICES) == 24

    def test_every_voice_has_required_fields(self):
        for v in _DASHSCOPE_TTS_VOICES:
            assert v["voice"] and v["language"] and v["gender"] and v["description"], v

    def test_whitelist_matches_data(self):
        names = {v["voice"] for v in _DASHSCOPE_TTS_VOICES}
        assert _DASHSCOPE_VOICE_NAMES == frozenset(names)

    def test_no_fictional_voices(self):
        """v4.8.133 教训：21 项虚构音色名导致 400 InvalidParameter 全部无声。"""
        fictional = {
            "Liam", "Olivia", "Noah", "Aria", "Hiro", "Sakura", "Minjun",
            "Yuki", "Jian", "Luna", "Oliver", "Mei", "Chen", "Zoe",
            "Pierre", "Sofia", "Diego", "Chiara", "Marco", "Anna", "Felix",
        }
        assert not fictional & _DASHSCOPE_VOICE_NAMES

    def test_no_duplicate_voice_names(self):
        names = [v["voice"] for v in _DASHSCOPE_TTS_VOICES]
        assert len(names) == len(set(names))

    def test_get_builtin_voices_dispatch(self):
        cosy = get_builtin_voices("cosyvoice")
        assert cosy[0]["voice"] == "Cherry"
        # 未知 / 空 provider 回退 DashScope（主提供商）
        assert get_builtin_voices("unknown") is _DASHSCOPE_TTS_VOICES
        assert get_builtin_voices("") is _DASHSCOPE_TTS_VOICES


# ── 白名单校验与回退 ────────────────────────────────────────────────────


class TestVoiceResolution:
    @pytest.mark.parametrize("voice", ["Cherry", "Serena", "Ethan", "Chelsie", "Stella"])
    def test_valid_voice_passthrough(self, voice):
        assert resolve_dashscope_voice(voice) == voice

    @pytest.mark.parametrize("bad", ["Olivia", "Liam", "不存在的音色", "", None])
    def test_invalid_voice_falls_back_to_cherry(self, bad):
        assert resolve_dashscope_voice(bad) == DEFAULT_TTS_VOICE

    def test_whitespace_is_stripped(self):
        assert resolve_dashscope_voice("  Cherry  ") == "Cherry"

    def test_is_valid_mirror(self):
        assert is_valid_dashscope_voice("Cherry") is True
        assert is_valid_dashscope_voice("Olivia") is False
        assert is_valid_dashscope_voice(None) is False


# ── 模型判定 ────────────────────────────────────────────────────────────


class TestModelDetection:
    @pytest.mark.parametrize("model", [
        "qwen3-tts-instruct-flash", "qwen-tts", "Qwen3-TTS-Instruct-Flash",
        "dashscope/qwen3-tts-instruct",
    ])
    def test_dashscope_models(self, model):
        assert is_dashscope_model(model) is True

    @pytest.mark.parametrize("model", ["", None, "cosyvoice-v2", "FunAudioLLM/CosyVoice2-0.5B"])
    def test_non_dashscope_models(self, model):
        assert is_dashscope_model(model) is False


# ── TTSSettings / load_tts_settings ────────────────────────────────────


class TestLoadTtsSettings:
    def test_empty_config_uses_schema_defaults(self):
        s = load_tts_settings({})
        assert s.enabled is False
        assert s.auto_for_long_text is True
        assert s.long_text_threshold == DEFAULT_TTS_LONG_TEXT_THRESHOLD
        assert s.emotion_routing_enabled is True
        assert s.voice == ""
        assert s.max_chunk_chars == TTS_MAX_CHUNK_CHARS
        assert s.voice_map == {}

    def test_flat_keys_converged(self):
        s = load_tts_settings({
            "tts_enabled": True,
            "tts_voice": "Stella",
            "tts_model": "qwen3-tts-instruct-flash",
            "provider_tts": "my-provider",
            "tts_max_chunk_chars": "120",  # 字符串数字也应收敛
        })
        assert s.enabled is True
        assert s.voice == "Stella"
        assert s.model == "qwen3-tts-instruct-flash"
        assert s.provider_id == "my-provider"
        assert s.max_chunk_chars == 120

    def test_chunk_chars_clamped_to_hard_limit(self):
        """v4.8.110：执行端必须回钳微信 60s 硬限，面板可配更大但不生效。"""
        s = load_tts_settings({"tts_max_chunk_chars": 99999})
        assert s.max_chunk_chars == TTS_MAX_CHUNK_CHARS

    def test_dirty_values_fall_back_per_key(self):
        """单键脏数据只回退该键，绝不让整个加载失败（TTS 是体验增强）。"""
        s = load_tts_settings({
            "tts_long_text_threshold": "not-a-number",
            "tts_max_chunk_chars": [1, 2],  # int() 抛 TypeError 的脏类型
            "tts_voice": 12345,             # 非 str 也应 str() 收敛
        })
        assert s.long_text_threshold == DEFAULT_TTS_LONG_TEXT_THRESHOLD
        assert s.max_chunk_chars == TTS_MAX_CHUNK_CHARS
        assert s.voice == "12345"

    def test_voice_map_json_string(self):
        s = load_tts_settings({"tts_voice_map": json.dumps({"开心": "Stella", "悲伤": "Seren"})})
        assert s.voice_map == {"开心": "Stella", "悲伤": "Seren"}
        assert s.voice_for_emotion("开心") == "Stella"

    def test_voice_map_dict_passthrough(self):
        s = load_tts_settings({"tts_voice_map": {"开心": "Stella"}})
        assert s.voice_map == {"开心": "Stella"}

    def test_voice_map_dirty_json_dropped(self):
        s = load_tts_settings({"tts_voice_map": "{not valid json"})
        assert s.voice_map == {}
        assert s.voice_for_emotion("开心") is None

    def test_voice_map_empty_values_filtered(self):
        s = load_tts_settings({"tts_voice_map": {"开心": "", "平静": None, "愤怒": "Vincent"}})
        assert s.voice_map == {"愤怒": "Vincent"}

    def test_voice_for_emotion_empty(self):
        s = load_tts_settings({"tts_voice_map": {"开心": "Stella"}})
        assert s.voice_for_emotion("") is None
        assert s.voice_for_emotion(None) is None
