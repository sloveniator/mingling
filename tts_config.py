"""tts_config.py -- TTS 单一数据源（v4.9.0 重构）
====================================================

背景（为什么存在这个模块）：
v4.8.133 之前，TTS 的「数据」和「配置读取」散落三处：
1. 音色库数据在 stars/apis/tts_voices.py（管理面板 API 层）；
2. 执行/触发配置散在 main.py 的 11 处 self._config.get("tts_*")；
3. 音色白名单校验又内联在 main.py._send_voice_chunk_retry_split。
依赖方向还是反的：main.py（入口层）import stars/apis（子 Star 层），
而音色数据恰恰是 main 与 diagnostics 都需要的底层资产。

本模块把三者合一：
- 音色库数据（DashScope 官方 24 音色 + CosyVoice 预置）——唯一权威源；
  stars/apis/tts_voices.py 改为 re-export，既有 import 路径全部兼容；
- TTSSettings：把散落的 tts_* 扁平键一次性收敛为带默认值/钳制的类型化
  对象，执行路径统一经由它读配置，消除"同一配置多处读、默认值漂移"；
- resolve_dashscope_voice：白名单校验 + 无效回退 Cherry（v4.8.132 线上
  bug 的修复逻辑在此固化，main.py 内联实现删除）。

import 方向（自上而下单向）：tts_config ← main.py / stars/apis/*。
本模块是叶子：不得 import main、provider_router 或任何 Star。
Python 3.9 兼容（CI 矩阵下限），禁用 3.10+ 语法。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

# ── 音色库数据（唯一权威源） ─────────────────────────────────────────────
# DashScope Qwen TTS 官方音色列表 (24 个)
# 来源：https://help.aliyun.com/zh/model-studio/qwen-tts-voice-list
# 适用模型：qwen3-tts-instruct-flash（面板「音色库」全部音色均已实测可合成）
# 更新：2026-09-10 修复：旧列表 24 项中 21 项为虚构音色名（Liam/Olivia/Noah/Aria/
# Hiro/Sakura/Minjun/Yuki/Jian/Luna/Oliver/Mei/Chen/Zoe/Pierre/Sofia/Diego/
# Chiara/Marco/Anna/Felix），DashScope 返回 400 InvalidParameter 导致试听无声。
_DASHSCOPE_TTS_VOICES = [
    {"voice": "Cherry", "language": "Chinese (Mandarin)", "gender": "Female", "description": "芊悦｜阳光积极、亲切自然的小姐姐"},
    {"voice": "Serena", "language": "Chinese (Mandarin)", "gender": "Female", "description": "苏瑶｜温柔知性小姐姐"},
    {"voice": "Ethan", "language": "Chinese (Mandarin)", "gender": "Male", "description": "晨煦｜标准普通话带部分北方口音，阳光温暖活力"},
    {"voice": "Chelsie", "language": "Chinese (Mandarin)", "gender": "Female", "description": "千雪｜二次元虚拟女友"},
    {"voice": "Momo", "language": "Chinese (Mandarin)", "gender": "Female", "description": "茉兔｜撒娇搞怪"},
    {"voice": "Vivian", "language": "Chinese (Mandarin)", "gender": "Female", "description": "十三｜拽拽的、可爱的小暴躁"},
    {"voice": "Moon", "language": "Chinese (Mandarin)", "gender": "Male", "description": "月白｜率性帅气"},
    {"voice": "Maia", "language": "Chinese (Mandarin)", "gender": "Female", "description": "四月｜知性与温柔"},
    {"voice": "Kai", "language": "Chinese (Mandarin)", "gender": "Male", "description": "凯｜耳朵的一场SPA"},
    {"voice": "Nofish", "language": "Chinese (Mandarin)", "gender": "Male", "description": "不吃鱼｜不会翘舌音的设计师"},
    {"voice": "Bella", "language": "Chinese (Mandarin)", "gender": "Female", "description": "萌宝｜喝酒不打醉拳的小萝莉"},
    {"voice": "Eldric Sage", "language": "Chinese (Mandarin)", "gender": "Male", "description": "沧明子｜沉稳睿智老者"},
    {"voice": "Mia", "language": "Chinese (Mandarin)", "gender": "Female", "description": "乖小妹｜温顺乖巧"},
    {"voice": "Mochi", "language": "Chinese (Mandarin)", "gender": "Male", "description": "沙小弥｜聪明伶俐小大人"},
    {"voice": "Bellona", "language": "Chinese (Mandarin)", "gender": "Female", "description": "燕铮莺｜声音洪亮"},
    {"voice": "Vincent", "language": "Chinese (Mandarin)", "gender": "Male", "description": "田叔｜沙哑烟嗓"},
    {"voice": "Bunny", "language": "Chinese (Mandarin)", "gender": "Female", "description": "萌小姬｜萌属性小萝莉"},
    {"voice": "Neil", "language": "Chinese (Mandarin)", "gender": "Male", "description": "阿闻｜新闻主持人"},
    {"voice": "Elias", "language": "Chinese (Mandarin)", "gender": "Female", "description": "墨讲师｜讲师"},
    {"voice": "Arthur", "language": "Chinese (Mandarin)", "gender": "Male", "description": "徐大爷｜质朴嗓音"},
    {"voice": "Nini", "language": "Chinese (Mandarin)", "gender": "Female", "description": "邻家妹妹｜软糯"},
    {"voice": "Seren", "language": "Chinese (Mandarin)", "gender": "Female", "description": "小婉｜助眠"},
    {"voice": "Pip", "language": "Chinese (Mandarin)", "gender": "Male", "description": "顽屁小孩｜调皮"},
    {"voice": "Stella", "language": "Chinese (Mandarin)", "gender": "Female", "description": "少女阿月｜迷糊少女·美少女战士"},
]

# 有效音色名集合：合成入口白名单校验用——配置了不存在的音色时回退 Cherry，
# 避免 400 InvalidParameter 导致全部语音合成失败（v4.8.132）。
_DASHSCOPE_VOICE_NAMES = frozenset(v["voice"] for v in _DASHSCOPE_TTS_VOICES)

# CosyVoice 音色列表 (官方公开)
# 来源：https://help.aliyun.com/zh/model-studio/cosyvoice-tts-python-sdk
# 注：CosyVoice 支持自定义音色克隆，这里只列出官方预置音色
_COSYVOICE_VOICES = [
    {"voice": "Cherry", "language": "Chinese (Mandarin)", "gender": "Female", "description": "CosyVoice 默认女性音色"},
    {"voice": "Serena", "language": "Chinese (Mandarin)", "gender": "Female", "description": "CosyVoice 知性女性音色"},
    {"voice": "Ethan", "language": "Chinese (Mandarin)", "gender": "Male", "description": "CosyVoice 默认男性音色"},
    {"voice": "Chelsie", "language": "Chinese (Mandarin)", "gender": "Female", "description": "CosyVoice 甜美女声音色"},
]

# DashScope 合成兜底音色：白名单校验失败 / 配置为空时的最终回退值。
DEFAULT_TTS_VOICE = "Cherry"

# 单段硬上限（微信客服 60s 限制的源头防御，v4.8.110）：
# 45007 在企微适配器内被吞、event.send 不抛异常，命灵无法感知失败 →
# 唯一可靠保障是源头不超时。按最保守语速 ~2.5 字/秒，150 字 ≈ 60s 内。
TTS_MAX_CHUNK_CHARS = 150

# 长文本触发默认阈值（可被 tts_long_text_threshold 覆盖，schema 限 100~2000）。
DEFAULT_TTS_LONG_TEXT_THRESHOLD = 500


def get_builtin_voices(provider: str) -> List[dict]:
    """按提供商返回内置音色列表；未知提供商回退 DashScope（主提供商）。"""
    if provider == "cosyvoice":
        return _COSYVOICE_VOICES
    return _DASHSCOPE_TTS_VOICES


def is_valid_dashscope_voice(voice: str) -> bool:
    """音色名是否在 DashScope 官方白名单内。"""
    return (voice or "").strip() in _DASHSCOPE_VOICE_NAMES


def resolve_dashscope_voice(voice: str) -> str:
    """白名单校验 + 无效回退 Cherry（v4.8.132 线上修复固化于此）。

    历史教训：历史遗留的虚构音色（如 Olivia）会让 DashScope 返回 400
    InvalidParameter，整段语音无声。合成入口统一走本函数，杜绝旁路。
    """
    v = (voice or "").strip()
    if v not in _DASHSCOPE_VOICE_NAMES:
        return DEFAULT_TTS_VOICE
    return v


def is_dashscope_model(model: str) -> bool:
    """是否 DashScope Qwen TTS 模型（模型名含 qwen-tts / qwen3-tts）。"""
    m = (model or "").lower()
    return "qwen-tts" in m or "qwen3-tts" in m


# ── 配置模型 ─────────────────────────────────────────────────────────────

@dataclass
class TTSSettings:
    """TTS 执行所需的全部配置，一次性从扁平 _config 收敛而来。

    字段默认值与 _conf_schema.json 保持一致；max_chunk_chars 在加载时
    已按 TTS_MAX_CHUNK_CHARS 钳制（上游面板可配更大值，但执行端必须
    回钳——v4.8.110 教训）。
    """
    enabled: bool = False
    auto_for_long_text: bool = True
    long_text_threshold: int = DEFAULT_TTS_LONG_TEXT_THRESHOLD
    emotion_routing_enabled: bool = True
    model: str = ""
    voice: str = ""
    provider_id: str = ""
    max_chunk_chars: int = TTS_MAX_CHUNK_CHARS
    voice_map: Dict[str, str] = field(default_factory=dict)

    def voice_for_emotion(self, emotion: str) -> Optional[str]:
        """情绪 → 音色映射（tts_voice_map JSON 解析结果）。无映射返回 None。"""
        if not emotion:
            return None
        return self.voice_map.get(emotion)


def load_tts_settings(cfg: Any) -> TTSSettings:
    """从扁平配置 dict（self._config / merged config）构建 TTSSettings。

    容错原则：单键类型异常只回退该键默认值，绝不让整个加载失败——
    TTS 是体验增强，配置脏数据不应打断主对话流。
    """
    get = cfg.get if hasattr(cfg, "get") else (lambda _k, _d=None: _d)

    # tts_voice_map 兼容两种形态：JSON 字符串（面板/旧配置）与 dict（测试桩）
    raw_map = get("tts_voice_map", "") or ""
    voice_map: Dict[str, str] = {}
    if raw_map:
        try:
            parsed = json.loads(raw_map) if isinstance(raw_map, str) else raw_map
            if isinstance(parsed, dict):
                voice_map = {str(k): str(v) for k, v in parsed.items() if v}
        except Exception:  # noqa: BLE001 - 脏 JSON 只丢弃映射，不影响其余配置
            voice_map = {}

    def _int(key: str, default: int) -> int:
        try:
            return int(get(key, default))
        except (TypeError, ValueError):
            return default

    return TTSSettings(
        enabled=bool(get("tts_enabled", False)),
        auto_for_long_text=bool(get("tts_auto_for_long_text", True)),
        long_text_threshold=_int("tts_long_text_threshold", DEFAULT_TTS_LONG_TEXT_THRESHOLD),
        emotion_routing_enabled=bool(get("tts_emotion_routing_enabled", True)),
        model=str(get("tts_model", "") or ""),
        voice=str(get("tts_voice", "") or ""),
        provider_id=str(get("provider_tts", "") or ""),
        # 上限钳制到微信 60s 硬限（v4.8.110）；不设下限，与既有行为一致
        max_chunk_chars=min(_int("tts_max_chunk_chars", TTS_MAX_CHUNK_CHARS), TTS_MAX_CHUNK_CHARS),
        voice_map=voice_map,
    )
