"""回归测试：v4.8.132 TTS 三接口 body 注入（漏挂 @with_json）。

v4.8.130~v4.8.131 线上 bug：`api_tts_voices` / `api_tts_preview` /
`api_tts_preview_long` 签名声明 ``body: dict = None`` 但只挂了
``@api_endpoint`` 兜底装饰器，漏挂 ``@with_json``。本插件 Web API 约定
「JSON 体只由装饰器读一次并注入 handler 形参」（``_base.py`` 模块文档明示，
AstrBot 核心不传位置参数——已上线的 ``api_config(self)`` 若被传参会直接
TypeError）→ ``body`` 恒为 ``None`` → 试听永远走默认值：
文本「你好，今天天气真好」+ 音色 **Cherry**，与配置选择无关。

本文件按生产同形调用（handler 无参调用，body 由请求上下文提供）钉死：
选什么音色，试听就用什么音色。
"""

import asyncio
from unittest.mock import patch

import pytest
from test_admin_api import build_star, call, payload_of


@pytest.fixture()
def star():
    s = build_star()
    asyncio.run(s.initialize())
    return s


def _fake_tts_factory(captured):
    """替换 _direct_dashscope_tts：捕获 kwargs，返回极短 WAV 占位字节。"""

    async def fake_tts(**kwargs):
        captured.update(kwargs)
        return b"RIFF" + b"\x00" * 8820  # ≈0.1s，时长估算不会除零

    return fake_tts


def _run_preview(star, handler, body):
    """生产同形调用：路由层对 handler 无参调用，body 由请求上下文注入。"""
    from mingling.stars.apis import tts_voices

    captured = {}
    with patch.object(tts_voices, "_direct_dashscope_tts", _fake_tts_factory(captured)):
        resp = call(handler, method="POST", body=body)
    return captured, resp


def test_preview_short_uses_body_voice(star):
    """试听短文本：body 里选 Chelsie 就必须合成 Chelsie（旧代码恒 Cherry）。"""
    captured, resp = _run_preview(
        star,
        star.api_tts_preview,
        {"text": "今天真开心", "voice": "Chelsie", "provider": "dashscope", "api_key": "sk-test"},
    )
    payload = payload_of(resp)
    assert payload.get("ok") is True, f"试听应成功，实际：{payload}"
    assert captured.get("voice") == "Chelsie", (
        f"合成层必须收到 body 选的音色 Chelsie，实际收到：{captured.get('voice')!r}"
    )
    assert payload.get("voice") == "Chelsie", (
        f"响应回显音色必须为 Chelsie（前端结果卡直接渲染此字段），实际：{payload.get('voice')!r}"
    )
    assert captured.get("text") == "今天真开心"


def test_preview_long_uses_body_voice_and_instructions(star):
    """试听长文本：voice 与语气指令（instructions）都必须穿透到合成层。"""
    captured, resp = _run_preview(
        star,
        star.api_tts_preview_long,
        {
            "text": "这是一段长文本试听。",
            "voice": "Ethan",
            "provider": "dashscope",
            "api_key": "sk-test",
            "instructions": "语速稍快，带点微笑",
        },
    )
    payload = payload_of(resp)
    assert payload.get("ok") is True, f"长文本试听应成功，实际：{payload}"
    assert captured.get("voice") == "Ethan"
    assert captured.get("instructions") == "语速稍快，带点微笑"
    assert payload.get("voice") == "Ethan"


def test_voices_body_api_key_beats_empty_config(star):
    """音色列表：body.api_key 必须生效（旧代码 body=None → 误报缺 Key）。

    build_star 默认配置无 tts_api_key；若 body 注入失效，接口会返回
    「缺少 DashScope API Key」错误。打桩 /models 校验请求，返回合法结构。
    """
    import mingling.http_client as http_client

    async def fake_request_json(*args, **kwargs):
        return {"object": "list", "data": [{"id": "qwen3-tts-instruct-flash"}]}

    with patch.object(http_client, "request_json", fake_request_json):
        resp = call(
            star.api_tts_voices,
            method="POST",
            body={"provider": "dashscope", "api_key": "sk-body-key"},
        )
    payload = payload_of(resp)
    assert payload.get("ok") is True, f"body.api_key 应使校验通过，实际：{payload}"
    assert payload.get("provider") == "dashscope"
    assert isinstance(payload.get("voices"), list) and payload["voices"]


# ── v4.9.2：skip_verify 零成本音色表（「音色」下拉自动填充路径） ──────────


def test_voices_skip_verify_no_key_returns_builtin(star):
    """skip_verify=True 且无 Key：不校验、不调外部 API，直接返回内置音色表。

    前端「音色 (voice)」下拉页面加载即自动填充（skip_verify），此时用户
    可能还没配 DashScope Key——接口绝不能报「缺少 Key」。
    """
    import mingling.http_client as http_client

    def _fail(*args, **kwargs):
        raise AssertionError("skip_verify 模式不得发起任何外部 HTTP 调用")

    with patch.object(http_client, "request_json", _fail):
        resp = call(
            star.api_tts_voices,
            method="POST",
            body={"provider": "dashscope", "skip_verify": True},
        )
    payload = payload_of(resp)
    assert payload.get("ok") is True, f"skip_verify 无 Key 也应成功，实际：{payload}"
    voices = payload.get("voices")
    assert isinstance(voices, list) and len(voices) == 24, (
        f"应返回 24 个官方音色，实际：{len(voices or [])}"
    )
    assert {"Cherry", "Stella", "Maia"} <= {v["voice"] for v in voices}


def test_voices_skip_verify_with_key_skips_http(star):
    """skip_verify=True 且带 Key：同样不发起外部调用（下拉填充零成本）。"""
    import mingling.http_client as http_client

    def _fail(*args, **kwargs):
        raise AssertionError("skip_verify 模式不得发起任何外部 HTTP 调用")

    with patch.object(http_client, "request_json", _fail):
        resp = call(
            star.api_tts_voices,
            method="POST",
            body={"provider": "dashscope", "api_key": "sk-any", "skip_verify": True},
        )
    assert payload_of(resp).get("ok") is True


def test_voices_default_mode_still_requires_key(star):
    """默认（不传 skip_verify）行为不变：无 Key 仍报错——「拉取音色」按钮
    保留 Key 有效性验证语义，不被本次改动削弱。"""
    resp = call(
        star.api_tts_voices,
        method="POST",
        body={"provider": "dashscope"},
    )
    payload = payload_of(resp)
    assert payload.get("status") == "error", "默认模式无 Key 必须失败"
    assert "Key" in str(payload.get("message") or "")
