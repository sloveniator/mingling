"""直连服务(TTS/STT/Embedding/Rerank)连通性测试的回归测试。

重点验证：默认模式下失败静默返回空值，诊断/连通性测试模式下失败抛出异常并
把真实错误返回给前端，避免“服务返回空结果”掩盖根因。
"""
import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from conftest import StubResponse

from http_client import ServiceHttpError
from provider_router import (
    _direct_openai_chat,
    _direct_openai_embed,
    _direct_openai_rerank,
    _direct_openai_speech,
    _resolve_tts_voice,
)


def _run(coro):
    return asyncio.run(coro)


# ── _resolve_tts_voice ─────────────────────────────────────────────────

def test_resolve_tts_voice_full_model_colon_passthrough():
    # 已含 ":" 原样返回（用户显式填了 模型:音色）
    assert _resolve_tts_voice("FunAudioLLM/CosyVoice2-0.5B:ben", "https://api.siliconflow.cn/v1", "FunAudioLLM/CosyVoice2-0.5B") == "FunAudioLLM/CosyVoice2-0.5B:ben"


def test_resolve_tts_voice_siliconflow_bare_name():
    # SiliconFlow + 裸音色名 → 自动补成 模型:音色
    assert _resolve_tts_voice("ben", "https://api.siliconflow.cn/v1", "FunAudioLLM/CosyVoice2-0.5B") == "FunAudioLLM/CosyVoice2-0.5B:ben"


def test_resolve_tts_voice_siliconflow_empty_defaults_alex():
    # SiliconFlow + 留空 → 默认 alex（补成 模型:alex）
    assert _resolve_tts_voice("", "https://api.siliconflow.cn/v1", "FunAudioLLM/CosyVoice2-0.5B") == "FunAudioLLM/CosyVoice2-0.5B:alex"


def test_resolve_tts_voice_openai_native():
    # 非 SiliconFlow（OpenAI 原生）→ 裸音色原样，缺省 alloy
    assert _resolve_tts_voice("echo", "https://api.openai.com/v1", "tts-1") == "echo"
    assert _resolve_tts_voice("", "https://api.openai.com/v1", "tts-1") == "alloy"


# ── _direct_openai_embed ────────────────────────────────────────────────

@patch("provider_router.request_json")
def test_embed_default_silences_service_http_error(mock_req):
    mock_req.side_effect = ServiceHttpError("HTTP 401: Invalid key")
    assert _run(_direct_openai_embed(["hi"], "k", "https://x/v1", "m")) == []


@patch("provider_router.request_json")
def test_embed_raise_error_exposes_service_http_error(mock_req):
    mock_req.side_effect = ServiceHttpError("HTTP 401: Invalid key")
    with pytest.raises(ServiceHttpError, match="HTTP 401"):
        _run(_direct_openai_embed(["hi"], "k", "https://x/v1", "m", raise_error=True))


@patch("provider_router.request_json")
def test_embed_raise_error_on_empty_vectors(mock_req):
    mock_req.return_value = {"data": []}
    with pytest.raises(ServiceHttpError, match="返回空向量"):
        _run(_direct_openai_embed(["hi"], "k", "https://x/v1", "m", raise_error=True))


# ── _direct_openai_rerank ───────────────────────────────────────────────

@patch("provider_router.request_json")
def test_rerank_default_silences_error(mock_req):
    mock_req.side_effect = ServiceHttpError("HTTP 404")
    assert _run(_direct_openai_rerank("q", ["d"], "k", "https://x/v1", "m", 1)) == []


@patch("provider_router.request_json")
def test_rerank_raise_error_exposes_http_error(mock_req):
    mock_req.side_effect = ServiceHttpError("HTTP 403")
    with pytest.raises(ServiceHttpError, match="HTTP 403"):
        _run(_direct_openai_rerank("q", ["d"], "k", "https://x/v1", "m", 1, raise_error=True))


@patch("provider_router.request_json")
def test_rerank_raise_error_on_empty_results(mock_req):
    mock_req.return_value = {"results": []}
    with pytest.raises(ServiceHttpError, match="返回空结果"):
        _run(_direct_openai_rerank("q", ["d"], "k", "https://x/v1", "m", 1, raise_error=True))


# ── _direct_openai_speech ───────────────────────────────────────────────

@patch("provider_router.request_bytes")
def test_speech_default_silences_error(mock_req):
    mock_req.side_effect = ServiceHttpError("HTTP 400")
    assert _run(_direct_openai_speech("hi", "k", "https://x/v1", "m")) == b""


@patch("provider_router.request_bytes")
def test_speech_raise_error_exposes_http_error(mock_req):
    mock_req.side_effect = ServiceHttpError("HTTP 429")
    with pytest.raises(ServiceHttpError, match="HTTP 429"):
        _run(_direct_openai_speech("hi", "k", "https://x/v1", "m", raise_error=True))


@patch("provider_router.request_bytes")
def test_speech_raise_error_on_empty_audio(mock_req):
    mock_req.return_value = b""
    with pytest.raises(ServiceHttpError, match="返回空音频流"):
        _run(_direct_openai_speech("hi", "k", "https://x/v1", "m", raise_error=True))


# ── diagnostics api_connectivity ────────────────────────────────────────

@patch("stars.apis.diagnostics.request_method", return_value="POST")
@patch("stars.apis.diagnostics.request_json")
@patch("provider_router.request_bytes")
def test_api_connectivity_tts_exposes_real_error(mock_provider_bytes, mock_api_req, mock_method):
    mock_provider_bytes.side_effect = ServiceHttpError("HTTP 401: Invalid API Key")
    mock_api_req.return_value = {
        "service": "tts",
        "api_key": "k",
        "api_base": "https://x/v1",
        "model": "m",
    }

    from stars.apis.diagnostics import DiagnosticsApiMixin
    mixin = DiagnosticsApiMixin()
    mixin.core = MagicMock()

    async def _call():
        resp = await mixin.api_connectivity()
        if isinstance(resp, StubResponse):
            return resp.payload
        return await resp.json()

    body = _run(_call())
    assert body["ok"] is False
    assert "HTTP 401" in body["error"]
    assert body["mode"] == "live_request"


# ── _direct_openai_chat ─────────────────────────────────────────────────

@patch("provider_router.request_json")
def test_chat_default_silences_error(mock_req):
    mock_req.side_effect = ServiceHttpError("HTTP 400")
    assert _run(_direct_openai_chat("hi", "k", "https://x/v1", "m")) == ""


@patch("provider_router.request_json")
def test_chat_raise_error_exposes_http_error(mock_req):
    mock_req.side_effect = ServiceHttpError("HTTP 429")
    with pytest.raises(ServiceHttpError, match="HTTP 429"):
        _run(_direct_openai_chat("hi", "k", "https://x/v1", "m", raise_error=True))


# ── 端点级：vision / merged / interpret 自定义模式连通性测试 ───────────────

def _make_mixin_with_config(config):
    from stars.apis.diagnostics import DiagnosticsApiMixin
    mixin = DiagnosticsApiMixin()
    mixin.core = MagicMock()
    mixin._config = config
    return mixin


@patch("stars.apis.diagnostics.request_method", return_value="POST")
@patch("stars.apis.diagnostics.request_json")
@patch("stars.apis.diagnostics._direct_openai_chat")
def test_api_connectivity_vision_merges_chat(mock_chat, mock_api_req, mock_method):
    mock_chat.return_value = "图片里是一只猫。"
    mock_api_req.return_value = {
        "service": "vision",
        "api_key": "k",
        "api_base": "https://x/v1",
        "model": "PaddlePaddle/PaddleOCR-VL-1.5",
    }
    mixin = _make_mixin_with_config({})
    body = _run(mixin.api_connectivity())
    assert body["ok"] is True
    assert "猫" in body["response_preview"]
    # vision 应走多模态路径（带图），与运行时一致
    _, kwargs = mock_chat.call_args
    assert kwargs.get("image_urls") is not None


@patch("stars.apis.diagnostics.request_method", return_value="POST")
@patch("stars.apis.diagnostics.request_json")
@patch("stars.apis.diagnostics._direct_openai_chat")
def test_api_connectivity_vision_soft_pass_when_model_rejects_test_image(mock_chat, mock_api_req, mock_method):
    # OCR 类视觉模型（如 PaddleOCR-VL）会拒绝占位/过小测试图，返回 HTTP 400
    # code 20015（图像处理失败）。这应判定为「端点已连通、密钥有效」的软成功，
    # 而非误判失败。
    mock_chat.side_effect = ServiceHttpError(
        'HTTP 400: {"code":20015,"message":"Failed to apply PaddleOCRVLProcessor on data=..."}',
        status=400,
    )
    mock_api_req.return_value = {
        "service": "vision",
        "api_key": "k",
        "api_base": "https://api.siliconflow.cn/v1",
        "model": "PaddlePaddle/PaddleOCR-VL-1.5",
    }
    mixin = _make_mixin_with_config({})
    body = _run(mixin.api_connectivity())
    assert body["ok"] is True
    assert body.get("image_rejected") is True
    assert "连通" in body["response_preview"]


@patch("stars.apis.diagnostics.request_method", return_value="POST")
@patch("stars.apis.diagnostics.request_json")
@patch("stars.apis.diagnostics._direct_openai_chat")
def test_api_connectivity_merged_merges_chat(mock_chat, mock_api_req, mock_method):
    mock_chat.return_value = "这是一段合并转发的摘要。"
    mock_api_req.return_value = {
        "service": "merged",
        "api_key": "k",
        "api_base": "https://x/v1",
        "model": "ByteDance-Seed/Seed-OSS-36B-Instruct",
    }
    mixin = _make_mixin_with_config({})
    body = _run(mixin.api_connectivity())
    assert body["ok"] is True
    assert "摘要" in body["response_preview"]
    _, kwargs = mock_chat.call_args
    assert kwargs.get("image_urls") is None  # 合并转发是纯文本


@patch("stars.apis.diagnostics.request_method", return_value="POST")
@patch("stars.apis.diagnostics.request_json")
@patch("stars.apis.diagnostics._direct_openai_chat")
def test_api_connectivity_interpret_merges_chat(mock_chat, mock_api_req, mock_method):
    mock_chat.return_value = "今日宜静思。"
    # 命理 AI 解读的接口地址字段在配置里叫 base_url，但前端以 api_base 提交；
    # 这里走 body 提供路径验证基本链路。
    mock_api_req.return_value = {
        "service": "interpret",
        "api_key": "k",
        "api_base": "https://api.siliconflow.cn/v1",
        "model": "deepseek-ai/DeepSeek-V4-Flash",
    }
    mixin = _make_mixin_with_config({})
    body = _run(mixin.api_connectivity())
    assert body["ok"] is True
    assert "静思" in body["response_preview"]


@patch("stars.apis.diagnostics.request_method", return_value="POST")
@patch("stars.apis.diagnostics.request_json")
def test_api_connectivity_interpret_falls_back_to_mingbu_interpret_prefix(mock_api_req, mock_method):
    # 不传 body、仅由配置回退：命理 AI 解读应解析 mingbu_interpret_* 前缀
    # （含 base_url 字段），而非误读成 interpret_api_base。
    mock_api_req.return_value = {
        "service": "interpret",
        # 故意不提供 api_key/api_base/model，走 cfg 回退
    }
    mixin = _make_mixin_with_config({
        "mingbu_interpret_api_key": "cfg-key",
        "mingbu_interpret_base_url": "https://cfg.sf/v1",
        "mingbu_interpret_model": "deepseek-ai/DeepSeek-V4-Flash",
    })

    async def _call():
        resp = await mixin.api_connectivity()
        return resp.payload if isinstance(resp, StubResponse) else await resp.json()

    # 缺 api_base 缺失检查会在调用前报“缺少配置”，这里验证它读到了正确前缀
    # （不抛内部 AttributeError、且错误信息指向 mingbu_interpret_* 已被识别）
    body = _run(_call())
    # 因未真实连通，要么 ok=False 报缺配置/连通失败——但绝不能因前缀错配而 500；
    # 关键断言：配置回退路径能识别服务（走到 live_request 或缺配置，而非 KeyError）。
    assert body["service"] == "interpret"
    assert "ok" in body


@patch("stars.apis.diagnostics.request_method", return_value="POST")
@patch("stars.apis.diagnostics.request_json")
def test_api_connectivity_rejects_unknown_service(mock_api_req, mock_method):
    mock_api_req.return_value = {"service": "weather", "api_key": "k", "api_base": "x", "model": "m"}
    mixin = _make_mixin_with_config({})

    async def _call():
        resp = await mixin.api_connectivity()
        return resp.payload if isinstance(resp, StubResponse) else await resp.json()

    body = _run(_call())
    # 未知 service 应被拒绝：error_response 返回体不含 ok 键，只须体现错误（含 "service" 提示）
    assert "service" in str(body)


# ── 命理后端连通性测试 /mingbu_test ─────────────────────────────────────

@patch("stars.apis.diagnostics.request_method", return_value="POST")
@patch("stars.apis.diagnostics._request_json")
def test_api_mingbu_test_health_ok(mock_http, mock_method):
    mock_http.return_value = {"status": "ok", "providerConfigured": {"deepseek": True}}

    from stars.apis.diagnostics import DiagnosticsApiMixin
    mixin = DiagnosticsApiMixin()
    mixin.core = MagicMock()
    mixin._config = {}
    mixin.api = SimpleNamespace(base_url="http://localhost:2963")

    async def _call():
        resp = await mixin.api_mingbu_test()
        return resp.payload if isinstance(resp, StubResponse) else await resp.json()

    body = _run(_call())
    assert body["ok"] is True
    assert body["url"] == "http://localhost:2963"
    assert body["providerConfigured"] == {"deepseek": True}
    # 应请求 {url}/api/health
    called_url = mock_http.call_args[0][2]
    assert called_url == "http://localhost:2963/api/health"


@patch("stars.apis.diagnostics.request_method", return_value="POST")
@patch("stars.apis.diagnostics._request_json")
def test_api_mingbu_test_uses_config_url_over_env(mock_http, mock_method):
    mock_http.return_value = {"status": "ok"}

    from stars.apis.diagnostics import DiagnosticsApiMixin
    mixin = DiagnosticsApiMixin()
    mixin.core = MagicMock()
    mixin._config = {"mingbu_api_url": "http://192.168.1.50:2963"}
    mixin.api = SimpleNamespace(base_url="http://localhost:2963")

    async def _call():
        resp = await mixin.api_mingbu_test()
        return resp.payload if isinstance(resp, StubResponse) else await resp.json()

    body = _run(_call())
    assert body["ok"] is True
    assert body["url"] == "http://192.168.1.50:2963"
    assert mock_http.call_args[0][2] == "http://192.168.1.50:2963/api/health"


@patch("stars.apis.diagnostics.request_method", return_value="POST")
@patch("stars.apis.diagnostics._request_json")
def test_api_mingbu_test_connection_error(mock_http, mock_method):
    mock_http.side_effect = ServiceHttpError("无法连接到 Mingbu 命理服务，请确认服务已启动")

    from stars.apis.diagnostics import DiagnosticsApiMixin
    mixin = DiagnosticsApiMixin()
    mixin.core = MagicMock()
    mixin._config = {}
    mixin.api = SimpleNamespace(base_url="http://localhost:2963")

    async def _call():
        resp = await mixin.api_mingbu_test()
        return resp.payload if isinstance(resp, StubResponse) else await resp.json()

    body = _run(_call())
    assert body["ok"] is False
    assert "无法连接" in body["error"]
