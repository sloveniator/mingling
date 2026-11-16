"""TTS 音色库管理 API —— 拉取音色列表 + 试听。

v4.9.0 重构：音色库**数据**已迁至插件根 ``tts_config.py``（唯一权威源），
本模块降级为「API mixin + 数据 re-export」薄层：
- 数据（``_DASHSCOPE_TTS_VOICES`` / ``_COSYVOICE_VOICES`` /
  ``_DASHSCOPE_VOICE_NAMES``）经 re-export 保持既有 import 路径兼容
  （main.py / stars/apis/diagnostics.py / tests 均按原名引用，零改动）；
- ``_merge_voices`` 别名等价于 ``tts_config.get_builtin_voices``；
- 三个管理面板 API（/tts/voices、/tts/preview、/tts/preview_long）行为不变。

支持 DashScope Qwen TTS (qwen3-tts-instruct-flash) 和 CosyVoice 两种提供商：
- DashScope：通过 MultiModalConversation SDK 调用，音色列表内置（官方 24 个）
- CosyVoice：通过 HTTP API 调用，音色列表从文档获取

前端通过 /{plugin}/tts/voices 拉取列表，/tts/preview 试听音频。

本模块是 ``MingbuStar`` 的 mixin（不 import core，只通过 ``self.<engine>``
鸭子类型访问引擎，与 ``star_mixins/`` 同一套约定，因此不构成循环依赖）。
"""
from __future__ import annotations

import base64
from typing import Optional

from astrbot.api import logger

from ...provider_router import (
    _direct_dashscope_tts,
)
from ...tts_config import (
    _COSYVOICE_VOICES,
    _DASHSCOPE_TTS_VOICES,
    _DASHSCOPE_VOICE_NAMES,
    get_builtin_voices,
)
from ._base import api_endpoint, error_response, json_response, with_json

__all__ = [
    "_COSYVOICE_VOICES",
    "_DASHSCOPE_TTS_VOICES",
    "_DASHSCOPE_VOICE_NAMES",
    "_merge_voices",
    "TtsVoicesApiMixin",
    "get_builtin_voices",
]

# 兼容旧名：数据与「按提供商取列表」逻辑已上移 tts_config.py，
# 本模块仅保留别名，既有调用方（含 tests/test_tts_voice_whitelist.py）
# 无需改动。
_merge_voices = get_builtin_voices


class TtsVoicesApiMixin:
    """TTS 音色库管理 API mixin。"""

    @api_endpoint("拉取 TTS 音色列表")
    @with_json
    async def api_tts_voices(self, body: Optional[dict] = None):
        """拉取 TTS 音色列表。

        请求体：``{"provider": "dashscope"|"cosyvoice", "api_key": str,
        "skip_verify": bool}``

        - ``skip_verify=True``：跳过 Key 校验与 DashScope 外部调用，直接返回
          内置静态音色表（供前端「音色」下拉零成本填充；音色表本身是纯静态
          数据，与 Key 有效性无关）。
        - 默认（不传）：验证 API Key（调 DashScope /models）后返回——供
          「拉取音色」按钮做 Key 有效性确认。
        """
        body = body or {}
        provider = (body.get("provider") or "dashscope").lower()
        api_key = (body.get("api_key") or "").strip()
        skip_verify = bool(body.get("skip_verify"))

        if not skip_verify:
            if not api_key:
                # 回退到运行时配置
                merged = await self._merged_config()
                api_key = merged.get("tts_api_key") or merged.get("dashscope_api_key") or ""

            if not api_key:
                return error_response("缺少 DashScope API Key，请在配置中填写")

            # 验证 API Key 有效性（轻量级：调用 /models 端点）
            try:
                from ...http_client import request_json
                result = await request_json(
                    None, "GET",
                    "https://dashscope.aliyuncs.com/compatible-mode/v1/models",
                    api_key=api_key,
                    timeout=10,
                )
                if not isinstance(result, dict) or "data" not in result:
                    return error_response("API Key 无效或无法访问 DashScope 服务")
            except Exception as e:
                logger.warning(f"[mingbu.tts] API Key 验证失败：{e}")
                # 不阻断，继续返回音色列表（Key 可能有效但模型列表接口受限）

        voices = _merge_voices(provider)
        return json_response({
            "ok": True,
            "voices": voices,
            "provider": provider,
            "count": len(voices),
        })

    @api_endpoint("TTS 试听")
    @with_json
    async def api_tts_preview(self, body: Optional[dict] = None):
        """TTS 试听：合成短文本并返回音频数据。

        请求体：``{"text": str, "voice": str, "provider": "dashscope"|"cosyvoice", "api_key": str}``
        返回：``{"ok": True, "audio_b64": str, "duration_ms": int, "voice": str}``
        """
        body = body or {}
        text = (body.get("text") or "你好，今天天气真好").strip()
        voice = (body.get("voice") or "Cherry").strip()
        provider = (body.get("provider") or "dashscope").lower()
        api_key = (body.get("api_key") or "").strip()

        if not text:
            return error_response("文本不能为空")
        if not voice:
            return error_response("音色不能为空")
        if not api_key:
            # 回退到运行时配置
            merged = await self._merged_config()
            api_key = merged.get("tts_api_key") or merged.get("dashscope_api_key") or ""

        if not api_key:
            return error_response("缺少 API Key，请在配置中填写")

        try:
            if provider == "dashscope":
                # DashScope Qwen TTS：使用 MultiModalConversation SDK
                audio_bytes = await _direct_dashscope_tts(
                    text=text,
                    api_key=api_key,
                    model="qwen3-tts-instruct-flash",
                    voice=voice,
                    instructions="",  # 试听不需要语气指令
                    optimize_instructions=False,
                )
            elif provider == "cosyvoice":
                # CosyVoice：走 HTTP API（需要额外实现）
                # 这里先返回错误，后续补充 CosyVoice 直连逻辑
                return error_response("CosyVoice 试听功能暂未实现，请先使用 DashScope")
            else:
                return error_response(f"不支持的提供商：{provider}")

            if not audio_bytes:
                return error_response("音频合成失败：返回空数据")

            # 估算时长（WAV 格式：44100Hz, 16bit, mono → 88200 bytes/sec）
            duration_ms = int(len(audio_bytes) / 88200 * 1000)

            # 返回 base64 编码的音频数据
            audio_b64 = base64.b64encode(audio_bytes).decode("utf-8")

            return json_response({
                "ok": True,
                "audio_b64": audio_b64,
                "duration_ms": duration_ms,
                "voice": voice,
                "provider": provider,
                "format": "wav",
            })

        except Exception as e:
            logger.error(f"[mingbu.tts] 试听失败：{e}")
            return error_response(f"试听失败：{str(e)[:200]}")

    @api_endpoint("TTS 试听长文本")
    @with_json
    async def api_tts_preview_long(self, body: Optional[dict] = None):
        """TTS 试听长文本（≤1600 token，约 1200 字）。

        请求体：同 api_tts_preview，但 text 可更长。
        返回：同 api_tts_preview。
        """
        body = body or {}
        text = (body.get("text") or "这是一个较长的测试文本，用于验证长文本 TTS 合成的效果。").strip()
        voice = (body.get("voice") or "Cherry").strip()
        provider = (body.get("provider") or "dashscope").lower()
        api_key = (body.get("api_key") or "").strip()
        instructions = (body.get("instructions") or "").strip()

        if not text:
            return error_response("文本不能为空")
        if len(text) > 1600:
            return error_response("文本过长（超过 1600 token，约 1200 字），请缩短后重试")

        if not api_key:
            merged = await self._merged_config()
            api_key = merged.get("tts_api_key") or merged.get("dashscope_api_key") or ""

        if not api_key:
            return error_response("缺少 API Key，请在配置中填写")

        try:
            audio_bytes = await _direct_dashscope_tts(
                text=text,
                api_key=api_key,
                model="qwen3-tts-instruct-flash",
                voice=voice,
                instructions=instructions,
                optimize_instructions=True,
            )

            if not audio_bytes:
                return error_response("音频合成失败：返回空数据")

            duration_ms = int(len(audio_bytes) / 88200 * 1000)
            audio_b64 = base64.b64encode(audio_bytes).decode("utf-8")

            return json_response({
                "ok": True,
                "audio_b64": audio_b64,
                "duration_ms": duration_ms,
                "voice": voice,
                "provider": provider,
                "format": "wav",
            })

        except Exception as e:
            logger.error(f"[mingbu.tts] 长文本试听失败：{e}")
            return error_response(f"试听失败：{str(e)[:200]}")

    async def _merged_config(self) -> dict:
        """从运行时配置读取合并后的配置。"""
        try:
            from ...config import get_config
            return get_config()
        except Exception:
            return {}
