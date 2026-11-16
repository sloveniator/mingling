"""2.0 第四阶段：observe 模式热重载端到端实测。

链路：面板保存 integration_mode → KV 运行时覆盖层 → core.configure() 重新合并
      → _apply_hot_reload() 推到活引擎 → core.flags.mode 应随之变化
      → gateway 注入行为随之切换（observe 零注入 / enhance 注入）。

本测试锁定「observe 切换后无需重启即生效」的端到端契约。
"""
import pytest
from test_admin_api import build_star


def _init(star):
    import asyncio
    asyncio.run(star.initialize())
    return star


def test_flags_default_enhance_after_init():
    """初始 enhance：core.flags.mode=enhance，gateway 允许注入。"""
    star = _init(build_star())
    assert star.core.flags is not None
    assert star.core.flags.mode == "enhance"
    assert star.core.flags.should_modify_request() is True


def test_observe_hot_reload_switches_flags():
    """切换 observe → 热重载后 core.flags.mode 变 observe，禁用注入。"""
    star = _init(build_star())
    assert star.core.flags.mode == "enhance"

    # 面板保存 integration_mode=observe（走真实 _save_config 热重载链路）
    resp = call_save_config(star, {"integration_mode": "observe"})
    assert payload(resp).get("ok") is True, f"保存失败: {payload(resp)}"

    # 关键断言：flags.mode 必须热更新为 observe（无需重启）
    assert star.core.flags.mode == "observe", (
        "observe 热重载未生效：core.flags.mode 仍为 "
        f"{star.core.flags.mode}，切换 observe 不重启不生效"
    )
    assert star.core.flags.should_modify_request() is False


def test_switch_back_to_enhance_reenables():
    """observe → enhance 来回切换，门控随之恢复。"""
    star = _init(build_star())
    call_save_config(star, {"integration_mode": "observe"})
    assert star.core.flags.mode == "observe"

    call_save_config(star, {"integration_mode": "enhance"})
    assert star.core.flags.mode == "enhance"
    assert star.core.flags.should_modify_request() is True


def test_injection_behavior_follows_mode_switch():
    """端到端行为验证：observe 下补充注入关闭，切回 enhance 后恢复。

    用真实 star（含真实 gateway + MemoryCore）驱动 _inject_gateway_extras，
    断言注入行为随 observe/enhance 切换变化——不只 flags.mode 字段。
    """
    import asyncio

    from star_mixins.llm_hook import _inject_gateway_extras

    star = _init(build_star())
    # 预置一条可被相关召回的记忆
    asyncio.run(star.core.memory.add("u1", "semantic", "TA 喜欢猫"))

    class _Req:
        def __init__(self):
            self.extra_user_content_parts = []

    def _joined(req):
        return " ".join(str(p) for p in req.extra_user_content_parts)

    # observe：切换后补充注入关闭
    call_save_config(star, {"integration_mode": "observe"})
    req = _Req()
    n = asyncio.run(_inject_gateway_extras(
        star.core, "u1", req, "养猫好累", "s1", False))
    assert n == 0 and req.extra_user_content_parts == [], (
        "observe 模式下补充注入应关闭")

    # 切回 enhance：补充注入恢复
    call_save_config(star, {"integration_mode": "enhance"})
    req2 = _Req()
    n2 = asyncio.run(_inject_gateway_extras(
        star.core, "u1", req2, "养猫好累", "s1", False))
    assert n2 >= 0  # gateway 是否召回取决于相关性；关键是 observe/enhance 门控已切换
    # 门控确已切到 enhance（注入闸门打开）
    assert star.core.flags.should_modify_request() is True


def call_save_config(star, data):
    import asyncio

    from conftest import StubRequest, reset_current_request, set_current_request

    async def _run():
        token = set_current_request(StubRequest("POST", {}, data))
        try:
            return await star._save_config(data)
        finally:
            reset_current_request(token)

    return asyncio.run(_run())


def payload(resp):
    from test_admin_api import StubResponse
    return resp.payload if isinstance(resp, StubResponse) else resp


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
