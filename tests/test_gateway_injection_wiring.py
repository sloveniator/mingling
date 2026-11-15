"""2.0 第四阶段：ContextGateway 接入 llm_hook 注入链的回归测试。

关键关系（取证复核）：记忆回注**主链路**是 persona_ctx（经 hooks/persona 接
llm_hook），其内部自实现 recall/retrieve_relevant 兜底。``_inject_gateway_extras``
走的是 gateway **补充**路径，二者并行。本测试锁定新函数四契约：

1. observe 模式 / 总开关关闭 / gateway 未初始化 → 零注入（行为零变化）。
2. enhance 模式 → gateway 额外召回的记忆作为补充 hint 注入 req。
3. 与 persona_ctx 去重：已登记注入（get_last_injected）的 id 不重复注入。
4. fail-open：gateway.build / get_last_injected 抛异常 → 返回 0、不注入、不抛出。
"""
import asyncio

import pytest

from star_mixins.llm_hook import _inject_gateway_extras


def _run(coro):
    return asyncio.run(coro)


class _Mem:
    def __init__(self, content, mid):
        self.content = content
        self.id = mid


class _Ctx:
    def __init__(self, memories):
        self.memories = memories


class _Gateway:
    def __init__(self, memories):
        self._memories = memories
        self.called = False

    async def build(self, uid, session_id="", message="", is_group=False, limit=3):
        self.called = True
        return _Ctx(self._memories)


class _Flags:
    def __init__(self, can_modify):
        self._can_modify = can_modify

    def should_modify_request(self):
        return self._can_modify


class _Memory:
    def __init__(self, injected_ids):
        self._injected = injected_ids

    async def get_last_injected(self, uid):
        return self._injected


class _Req:
    def __init__(self):
        self.extra_user_content_parts = []


def _joined(req):
    return " ".join(str(p) for p in req.extra_user_content_parts)


def test_observe_mode_zero_injection():
    """observe（不可改请求）→ 零行为变化，不注入、gateway 甚至不被调用。"""
    gw = _Gateway([_Mem("相关记忆", "m1")])
    core = type("C", (), {"flags": _Flags(False), "gateway": gw,
                          "memory": _Memory([])})()
    req = _Req()
    n = _run(_inject_gateway_extras(core, "u1", req, "你好", "s1", False))
    assert n == 0
    assert gw.called is False, "observe 模式不应触发 gateway.build"
    assert req.extra_user_content_parts == []


def test_gateway_not_initialized_zero_injection():
    """flags/gateway 未初始化（None）→ 优雅返回 0，不抛异常。"""
    core = type("C", (), {"flags": None, "gateway": None})()
    req = _Req()
    assert _run(_inject_gateway_extras(core, "u1", req, "你好", "s1", False)) == 0
    assert req.extra_user_content_parts == []


def test_empty_message_zero_injection():
    """消息为空 → 不触发 gateway，零注入。"""
    gw = _Gateway([_Mem("x", "m1")])
    core = type("C", (), {"flags": _Flags(True), "gateway": gw})()
    req = _Req()
    assert _run(_inject_gateway_extras(core, "u1", req, "", "s1", False)) == 0
    assert gw.called is False


def test_enhance_mode_injects_extra_memory():
    """enhance 模式 → gateway 召回的记忆作为补充 hint 注入。"""
    gw = _Gateway([_Mem("TA 喜欢猫", "m1")])
    core = type("C", (), {"flags": _Flags(True), "gateway": gw,
                          "memory": _Memory([])})()
    req = _Req()
    n = _run(_inject_gateway_extras(core, "u1", req, "养猫好累", "s1", False))
    assert n == 1
    assert "TA 喜欢猫" in _joined(req)


def test_dedup_excludes_already_injected():
    """与 persona_ctx 去重：已登记注入的 id 不重复注入。"""
    gw = _Gateway([
        _Mem("已注入过的记忆", "m1"),
        _Mem("全新补充的记忆", "m2"),
    ])
    core = type("C", (), {"flags": _Flags(True), "gateway": gw,
                          "memory": _Memory(["m1"])})()
    req = _Req()
    n = _run(_inject_gateway_extras(core, "u1", req, "继续聊", "s1", False))
    assert n == 1, "只应注入未注入过的 m2"
    joined = _joined(req)
    assert "全新补充的记忆" in joined
    assert "已注入过的记忆" not in joined


def test_fail_open_on_gateway_exception():
    """gateway.build 抛异常 → 返回 0、不注入、不向上抛。"""
    class _BadGateway:
        async def build(self, *a, **k):
            raise RuntimeError("gateway down")

    core = type("C", (), {"flags": _Flags(True), "gateway": _BadGateway(),
                          "memory": _Memory([])})()
    req = _Req()
    assert _run(_inject_gateway_extras(core, "u1", req, "你好", "s1", False)) == 0
    assert req.extra_user_content_parts == []


def test_fail_open_on_injected_read_exception():
    """get_last_injected 抛异常 → 去重降级（仍注入），不阻断。"""
    class _BadMemory:
        async def get_last_injected(self, uid):
            raise RuntimeError("kv down")

    gw = _Gateway([_Mem("降级仍可注入", "m1")])
    core = type("C", (), {"flags": _Flags(True), "gateway": gw,
                          "memory": _BadMemory()})()
    req = _Req()
    n = _run(_inject_gateway_extras(core, "u1", req, "你好", "s1", False))
    assert n == 1, "去重数据源失败应降级为仍注入，不阻断补充"
    assert "降级仍可注入" in _joined(req)


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
