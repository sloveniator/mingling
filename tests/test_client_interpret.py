"""命灵·MingbuClient.interpret 的 SSE 累积解析 + messages 透传单测。"""
import asyncio
from unittest.mock import patch

import client as client_mod
from client import MingbuClient, _accumulate_sse


def test_accumulate_sse_basic():
    lines = ["data: 你好", "data: 世界", ""]
    assert _accumulate_sse(lines) == "你好世界"


def test_accumulate_sse_ignores_heartbeat_and_done():
    lines = [": heartbeat", "data: 第一部分", "data: [DONE]", "data: 第二部分"]
    assert _accumulate_sse(lines) == "第一部分第二部分"


def test_accumulate_sse_empty():
    assert _accumulate_sse([]) == ""


def test_accumulate_sse_strips_data_prefix_space():
    lines = ["data: 带前导空格的文本"]
    assert _accumulate_sse(lines) == "带前导空格的文本"


def test_interpret_passes_messages_through():
    """interpret 接受 messages 时，payload 必须携带该 messages 而非单条兜底。"""
    captured = {}

    async def fake_stream(session, url, payload=None, timeout=None):
        captured["payload"] = payload
        return ["data: 命宫", "", "data: 在午", "", "data: [DONE]"]

    c = MingbuClient(base_url="http://x")
    with patch.object(client_mod, "request_stream", fake_stream):
        out = asyncio.run(c.interpret(
            type="combined", birth={"a": 1}, topic="career",
            messages=[{"role": "user", "content": "下半年事业运"}],
            provider="deepseek", api_key="k", base_url="https://b/v1", model="m",
        ))
    assert out == "命宫在午"
    assert captured["payload"]["messages"] == [{"role": "user", "content": "下半年事业运"}]
    assert captured["payload"]["type"] == "combined"
    assert captured["payload"]["topic"] == "career"
    assert captured["payload"]["provider"] == "deepseek"
    assert captured["payload"]["apiKey"] == "k"
    assert captured["payload"]["baseUrl"] == "https://b/v1"
    assert captured["payload"]["model"] == "m"


def test_interpret_falls_back_single_message():
    """未传 messages 时回退为单条 user 消息（question）。"""
    captured = {}

    async def fake_stream(session, url, payload=None, timeout=None):
        captured["payload"] = payload
        return ["data: ok"]

    c = MingbuClient(base_url="http://x")
    with patch.object(client_mod, "request_stream", fake_stream):
        asyncio.run(c.interpret(type="ziwei", birth={"a": 1}, question="我的命盘"))
    assert captured["payload"]["messages"] == [{"role": "user", "content": "我的命盘"}]
