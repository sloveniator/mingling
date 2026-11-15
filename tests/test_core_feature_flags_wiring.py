"""2.0 第三阶段：FeatureFlags + ContextGateway 接入 MingbuCore 的回归测试。

验证新增的「门控接入点」契约（core.ensure 中构建 flags/gateway）：
- __init__ 先声明 self.flags/self.gateway = None（ensure 完成前访问不 AttributeError）。
- FeatureFlags.from_config 从 config 读 integration_mode；缺省 enhance。
- observe 模式 should_modify_request()=False → gateway.build 返回空注入（零行为变化）。
- gateway 依赖 core.memory/persona 缺失时优雅降级（不抛异常，注入为空）。
"""
import asyncio

import pytest

from integration.feature_flags import (
    MODE_ENHANCE,
    MODE_OBSERVE,
    FeatureFlags,
)
from integration.context_gateway import ContextGateway


def _run(coro):
    return asyncio.run(coro)


# ---- __init__ 声明契约（防 ensure 前访问 AttributeError）----

def test_core_init_declares_flags_and_gateway_none():
    """MingbuCore.__init__ 必须先声明 flags/gateway 为 None（作用域安全）。"""
    from core import MingbuCore

    core = MingbuCore()
    assert hasattr(core, "flags") and core.flags is None
    assert hasattr(core, "gateway") and core.gateway is None


# ---- FeatureFlags.from_config（接入点读取 config 的契约）----

def test_flags_from_config_default_enhance():
    f = FeatureFlags.from_config({})
    assert f.mode == MODE_ENHANCE
    assert f.enabled is True
    assert f.should_modify_request() is True


def test_flags_from_config_reads_integration_mode():
    f = FeatureFlags.from_config({"integration_mode": MODE_OBSERVE})
    assert f.mode == MODE_OBSERVE
    assert f.is_observe is True  # @property，非方法
    assert f.should_modify_request() is False  # observe 零行为变化


def test_flags_from_config_invalid_mode_falls_back():
    f = FeatureFlags.from_config({"integration_mode": "bogus"})
    assert f.mode == MODE_ENHANCE  # 非法模式回退 enhance


# ---- gateway 组装契约（接入后 build 的行为）----

class _CoreStub:
    """最小 core 鸭子类型：memory/persona 可控。"""

    def __init__(self, memory=None, persona=None):
        self.memory = memory
        self.persona = persona


def test_gateway_observe_mode_returns_empty_injection():
    """observe 模式：build 不做任何检索，注入为空（零行为变化）。"""
    core = _CoreStub()
    flags = FeatureFlags.from_config({"integration_mode": MODE_OBSERVE})
    gw = ContextGateway(core, flags=flags)

    ctx = _run(gw.build("u1", message="hello"))
    assert ctx.memories == []
    assert ctx.hints == []
    assert ctx.injected is False


def test_gateway_missing_memory_persona_degrades_gracefully():
    """memory/persona 缺失（None）→ 不抛异常，优雅降级为空注入。"""
    core = _CoreStub(memory=None, persona=None)
    flags = FeatureFlags.from_config({})  # enhance 模式
    gw = ContextGateway(core, flags=flags)

    ctx = _run(gw.build("u1", message="hello"))  # 绝不抛
    assert ctx.memories == []
    assert ctx.injected is False


def test_gateway_wired_flags_consistency():
    """接进 core 后：gateway.flags 与 core.flags 应为同一实例（一致门控）。"""
    flags = FeatureFlags.from_config({"integration_mode": MODE_ENHANCE})
    core = _CoreStub()
    core.flags = flags
    core.gateway = ContextGateway(core, flags=flags)
    assert core.gateway.flags is core.flags
    assert core.gateway.core is core


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
