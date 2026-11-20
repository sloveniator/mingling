"""混合意图路由：正则快速路径 + 默认模型语义兜底。"""
import asyncio

import semantic_router
from semantic_router import (
    SemanticDecision,
    parse_decision,
    should_classify,
)


def test_parse_decision_valid_and_invalid():
    d = parse_decision('{"intent":"health","confidence":0.82,"counsel_action":"none"}')
    assert d == SemanticDecision("health", 0.82, "none")
    assert parse_decision("not json") is None
    assert parse_decision('{"intent":"unknown","confidence":0.9}') is None
    assert parse_decision('{"intent":"none","confidence":2,"counsel_action":"none"}').confidence == 1.0


def test_should_classify_only_candidates():
    assert should_classify("最近总是提不起劲", False)
    assert should_classify("帮我看看这个怎么办", False)
    assert not should_classify("今天吃什么", False)
    assert not should_classify("嗯", False)
    # 开解中短应声不调用模型；可能收束/转向的长消息才调用
    assert not should_classify("嗯嗯", True)
    assert should_classify("我觉得好多了，可以结束了", True)


def test_classify_uses_default_model(monkeypatch):
    async def fake_ask(*args, **kwargs):
        return '{"intent":"counsel","confidence":0.91,"counsel_action":"enter"}'

    monkeypatch.setattr(semantic_router, "ask_default", fake_ask)
    d = asyncio.run(semantic_router.classify(None, "最近心里空落落的", False))
    assert d.intent == "counsel"
    assert d.counsel_action == "enter"


def _plugin():
    from config import CompanionConfig
    from main import MinglingPlugin

    class FakeStore:
        def __init__(self):
            self.data = {}

        async def load(self, umo, suffix, default):
            return self.data.get((umo, suffix), default)

        async def save(self, umo, suffix, value):
            self.data[(umo, suffix)] = value

    class FakeEvent:
        def __init__(self, text):
            self.message_str = text
            self.unified_msg_origin = "u1"
            self.stopped = False

        def is_private_chat(self):
            return True

        def plain_result(self, text):
            return ("plain", text)

        def stop_event(self):
            self.stopped = True

    p = object.__new__(MinglingPlugin)
    p._cfg = CompanionConfig(enabled=True, semantic_router_enabled=True)
    p._store = FakeStore()
    p._last_observe_key = None
    p.context = object()

    async def noop(*args, **kwargs):
        return None

    p._observe_once = noop
    return p, FakeEvent


def test_auto_route_uses_semantic_fallback_for_counsel(monkeypatch):
    p, FakeEvent = _plugin()

    async def fake_classify(*args, **kwargs):
        return SemanticDecision("counsel", 0.93, "enter")

    monkeypatch.setattr("main.classify_intent", fake_classify)

    async def fake_task(task, event, text):
        yield ("task", task)

    p._run_task = fake_task
    ev = FakeEvent("最近心里空落落的")
    out = asyncio.run(_collect(p.auto_route(ev)))
    assert out and out[0][0] == "plain"
    assert ev.stopped is True
    assert asyncio.run(p._store.load("u1", "counseling_active", False)) is True


def test_auto_route_ignores_low_confidence(monkeypatch):
    p, FakeEvent = _plugin()

    async def fake_classify(*args, **kwargs):
        return SemanticDecision("health", 0.2, "none")

    monkeypatch.setattr("main.classify_intent", fake_classify)
    ev = FakeEvent("最近有点提不起劲")
    out = asyncio.run(_collect(p.auto_route(ev)))
    assert out == []
    assert ev.stopped is False


def test_regex_fast_path_skips_model(monkeypatch):
    p, FakeEvent = _plugin()

    async def boom(*args, **kwargs):
        raise AssertionError("正则已命中时不应调用语义分类")

    monkeypatch.setattr("main.classify_intent", boom)

    async def fake_task(task, event, text):
        yield ("task", task)

    p._run_task = fake_task
    ev = FakeEvent("帮我看看八字")
    out = asyncio.run(_collect(p.auto_route(ev)))
    assert out == [("task", "chart")]
    assert ev.stopped is True


async def _collect(gen):
    return [item async for item in gen]
