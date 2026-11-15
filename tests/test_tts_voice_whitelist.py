"""回归测试：v4.8.133 音色列表真实性 + 无效音色白名单回退。

v4.8.133 线上 bug：硬编码 `_DASHSCOPE_TTS_VOICES` 24 项中 21 项为虚构音色
（Liam/Olivia/Noah/Aria 等），DashScope `qwen3-tts-instruct-flash` 不存在
这些 voice → 试听/合成 400 InvalidParameter，用户感知「点击不发声」。
修复后：① 列表替换为官方 24 音色（真实 Key 实测可合成）；
② 白名单校验 + 无效回退 Cherry 固化于 tts_config.resolve_dashscope_voice，
   配置残留虚构音色不再静默失败。

v4.8.141：运行时 TTS 合成完全交由 AstrBot provider，插件不再直连。原
`_send_voice_chunk_retry_split` 端到端路径测试随之移除；白名单回退逻辑仍由
面板「音色试听」工具复用，故保留对 resolve_dashscope_voice 的直接断言。
"""


def test_voice_list_is_official_24():
    """音色列表必须是官方 24 音色：无虚构名、含新音色、数量钉死。"""
    from astrbot_plugin_mingbu.stars.apis.tts_voices import (
        _DASHSCOPE_TTS_VOICES,
        _DASHSCOPE_VOICE_NAMES,
    )

    assert len(_DASHSCOPE_TTS_VOICES) == 24
    assert len(_DASHSCOPE_VOICE_NAMES) == 24
    # 虚构音色一律不得回潮（v4.8.130~131 线上事故源头）
    for fabricated in ("Liam", "Olivia", "Noah", "Aria", "Sakura", "Felix"):
        assert fabricated not in _DASHSCOPE_VOICE_NAMES
    # 官方列表关键成员在位
    for real in ("Cherry", "Serena", "Ethan", "Chelsie", "Momo", "Moon",
                 "Nofish", "Eldric Sage", "Stella"):
        assert real in _DASHSCOPE_VOICE_NAMES


def test_invalid_voice_falls_back_to_cherry():
    """配置残留虚构音色（Olivia）→ 必须回退 Cherry，不能静默 400。"""
    from astrbot_plugin_mingbu.tts_config import resolve_dashscope_voice

    assert resolve_dashscope_voice("Olivia") == "Cherry"
    assert resolve_dashscope_voice("Liam") == "Cherry"
    assert resolve_dashscope_voice("") == "Cherry"
    assert resolve_dashscope_voice(None) == "Cherry"


def test_valid_voice_passthrough_untouched():
    """官方真实音色（Moon）必须原样透传，白名单不得误伤。"""
    from astrbot_plugin_mingbu.tts_config import resolve_dashscope_voice

    assert resolve_dashscope_voice("Moon") == "Moon"
    assert resolve_dashscope_voice("Cherry") == "Cherry"
    assert resolve_dashscope_voice("Eldric Sage") == "Eldric Sage"
