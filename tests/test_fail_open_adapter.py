"""fail-open 适配器回归测试 -- 2.0 第一阶段任务 1。

核心契约：``_on_llm_request_body``（顶层包装）在任何增强逻辑抛异常时，
都**绝不能**阻断 AstrBot 原生对话。增强失效的代价是"这一轮没有个性化
上下文"，而非"用户收不到回复"。

覆盖三种情形：
1. 增强主体 ``_impl`` 抛异常 → 包装层吞掉异常 + record_failure，正常返回；
2. 正常控制流（impl 内部已 ``event.stop_event()``）→ 包装层不拦截、不重放；
3. 包装层自身在取 uid 兜底时也不抛（uid 获取失败仍安全降级）。
"""
import asyncio
from unittest.mock import patch

import pytest

from tests.test_admin_api import build_star


def _make_event(msg_type="FriendMessage", text="在吗"):
    from astrbot.api.event import AstrMessageEvent

    return AstrMessageEvent(
        message_str=text,
        unified_msg_origin="test:friend:u_failopen",
        session_id="s_failopen",
        msg_type=msg_type,
    )


def _make_req():
    from astrbot.api.provider import ProviderRequest

    return ProviderRequest(prompt="在吗", session_id="s_failopen")


def _run(coro):
    return asyncio.run(coro)


def test_impl_exception_is_swallowed_and_recorded():
    """impl 抛异常 → 包装层吞掉、record_failure 被调用、方法正常返回。"""
    star = build_star()
    _run(star.initialize())

    async def _boom(event, req):
        raise RuntimeError("模拟增强逻辑崩溃")

    star._on_llm_request_body_impl = _boom

    # 不应向外抛异常
    _run(star._on_llm_request_body(_make_event(), _make_req()))


def test_impl_exception_records_failure_metric():
    """impl 抛异常时必须 record_failure("request_enhancement")。"""
    star = build_star()
    _run(star.initialize())

    async def _boom(event, req):
        raise ValueError("boom")

    star._on_llm_request_body_impl = _boom

    with patch("star_mixins.llm_hook.record_failure") as rf:
        _run(star._on_llm_request_body(_make_event(), _make_req()))
    rf.assert_any_call("request_enhancement")


def test_normal_flow_not_intercepted():
    """正常路径下包装层只是透传：impl 正常执行、stop_event 控制流不被拦截。"""
    star = build_star()
    _run(star.initialize())

    calls = {"impl": 0, "stopped": False}

    class _Ev:
        message_str = "在吗"
        unified_msg_origin = "test:friend:u_failopen"
        session_id = "s_failopen"

        def get_sender_id(self):
            return "u_failopen"

        def stop_event(self):
            calls["stopped"] = True

    async def _ok(event, req):
        calls["impl"] += 1
        # 模拟去重/没听清短路：stop_event 后正常 return
        event.stop_event()

    star._on_llm_request_body_impl = _ok

    _run(star._on_llm_request_body(_Ev(), _make_req()))
    assert calls["impl"] == 1
    # 包装层不拦截正常控制流：impl 内的 stop_event 已生效
    assert calls["stopped"] is True


def test_wrapper_survives_uid_failure():
    """impl 抛异常且 get_sender_id 也抛 → 包装层仍安全降级，不抛。"""
    star = build_star()
    _run(star.initialize())

    async def _boom(event, req):
        raise RuntimeError("crash")

    star._on_llm_request_body_impl = _boom

    class _BadUidEvent:
        def get_sender_id(self):
            raise RuntimeError("no uid")

    with patch("star_mixins.llm_hook.record_failure"):
        # 事件对象连 uid 都取不到，包装层兜底仍要安全
        ev = _BadUidEvent()
        _run(star._on_llm_request_body(ev, _make_req()))


def test_real_impl_still_reachable():
    """冒烟：真实 impl（未 mock）在干净内核上可正常跑通一次，不抛。"""
    star = build_star()
    _run(star.initialize())
    # 真实实现跑一遍，确认拆分（包装 + _impl）后方法仍连通
    _run(star._on_llm_request_body(_make_event(), _make_req()))


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
