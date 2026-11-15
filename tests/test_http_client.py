"""统一 HTTP 客户端状态码与响应解析边界测试。"""
import asyncio

import pytest

from http_client import (
    ServiceHttpError,
    _CircuitBreaker,
    _headers,
    _resilient,
    request_bytes,
    request_json,
)


class _Response:
    def __init__(self, status, payload=None, text="", raw=b""):
        self.status = status
        self._payload = payload
        self._text = text
        self._raw = raw

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload

    async def text(self):
        return self._text

    async def read(self):
        return self._raw


class _Session:
    def __init__(self, response):
        self.response = response
        self.closed = False

    def request(self, *args, **kwargs):
        return self.response

    def post(self, *args, **kwargs):
        return self.response

    async def close(self):
        self.closed = True


def test_request_json_accepts_201():
    result = asyncio.run(request_json(_Session(_Response(201, {"ok": True})), "POST", "https://example.test"))
    assert result == {"ok": True}


def test_request_json_rejects_success_non_json():
    with pytest.raises(ServiceHttpError, match="不是合法 JSON"):
        asyncio.run(request_json(_Session(_Response(204, ValueError("bad"), text="done")), "GET", "https://example.test"))


def test_request_bytes_accepts_202():
    result = asyncio.run(request_bytes(_Session(_Response(202, raw=b"audio")), "https://example.test"))
    assert result == b"audio"


# ------------------------------------------------------------
# 熔断 / 重试：仅「可重试」错误（连接/超时/429/5xx）计入熔断；
# 4xx 业务错误不得误触发熔断，也不重试（回归：反复 4xx 会把存活服务拖入冷却）。
# ------------------------------------------------------------

def test_resilient_nonretryable_not_counted_not_retried():
    breaker = _CircuitBreaker(failure_threshold=2, cooldown_sec=60)
    calls = []

    async def corofn():
        calls.append(1)
        raise ServiceHttpError("HTTP 400: bad", status=400, retryable=False)

    with pytest.raises(ServiceHttpError, match="400"):
        asyncio.run(_resilient(corofn, "https://x.test", breaker=breaker, retry_max=3))

    assert len(calls) == 1                      # 4xx 不重试
    assert breaker.allow("https://x.test") is True  # 4xx 不计入熔断计数


def test_resilient_retryable_counted_and_retried():
    breaker = _CircuitBreaker(failure_threshold=2, cooldown_sec=60)
    calls = []

    async def corofn():
        calls.append(1)
        raise ServiceHttpError("连接失败", retryable=True)

    with pytest.raises(ServiceHttpError):
        asyncio.run(_resilient(
            corofn, "https://x.test", breaker=breaker,
            retry_max=1, retry_base=0.0, retry_max_delay=0.0,
        ))

    assert len(calls) == 2                       # 首试 + 1 次重试
    # 两次连续失败已达阈值，熔断打开，后续请求应被拒绝
    assert breaker.allow("https://x.test") is False


# ------------------------------------------------------------
# Content-Type：不得硬编码 application/json
# （历史：_headers 无条件写 application/json，会把 multipart 表单的 boundary 覆盖，
#   STT 上传音频时后端按 JSON 解析 multipart 字节 → HTTP 400 / code 20015）
# ------------------------------------------------------------

def test_headers_does_not_force_content_type():
    h = _headers("sk-test")
    assert "Content-Type" not in h
    assert h["Authorization"] == "Bearer sk-test"


def test_request_json_form_does_not_force_json_content_type():
    import aiohttp

    captured = {}

    class _CapturingSession:
        def request(self, *a, **kw):
            captured.update(kw)
            return _Response(200, {"text": "hi"})

        async def close(self):
            pass

    form = aiohttp.FormData()
    form.add_field("file", b"RIFFxxxx", filename="audio.wav", content_type="audio/wav")
    asyncio.run(request_json(_CapturingSession(), "POST", "https://example.test", form=form))
    # form 请求不应带 application/json，交由 aiohttp 自动生成 multipart boundary
    assert "Content-Type" not in captured.get("headers", {})
