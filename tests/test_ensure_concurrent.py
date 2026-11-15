"""P2 并发安全：core.ensure 在多个 Star 并行初始化时只构建一次引擎。

见代码审查报告 P2：原 ensure 用 `if self._ensured: return` 做幂等，但无锁；若
AstrBot 并行初始化多个 Star，ensure 在「检查通过→设置 _ensured=True」之间可能被
重入，导致引擎双建（KV/HTTP 连接池泄漏）。现用 asyncio.Lock 包裹构建段
（双检锁），首个协程构建、其余快速返回。
"""
import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

import features
from core import MingbuCore


class _FakeFeature:
    name = "demo"
    config_key = "x_demo"

    def __init__(self):
        self.builds = 0

    def enabled_if(self, cfg):
        return True

    async def build(self, core_inst, owner, cfg):
        self.builds += 1
        core_inst._fake_builds = getattr(core_inst, "_fake_builds", 0) + 1


class _FakeProviderRouter:
    def __init__(self, owner):
        self.owner = owner


class _FakeMemory:
    def __init__(self, owner):
        self.owner = owner


class _FakeCounsel:
    def __init__(self, owner):
        self.owner = owner


class _FakePersona:
    @classmethod
    def from_dict(cls, d):
        return cls()

    def __init__(self, name="灵犀"):
        self.name = name
        self.mood = 0


def _fake_owner():
    owner = MagicMock()
    owner.get_kv_data = AsyncMock(return_value=None)
    return owner


@pytest.fixture
def patched(monkeypatch):
    feat = _FakeFeature()
    monkeypatch.setattr("core.MemoryCore", _FakeMemory)
    monkeypatch.setattr("core.ProviderRouter", _FakeProviderRouter)
    monkeypatch.setattr("core.CounselingSessionManager", _FakeCounsel)
    monkeypatch.setattr("core.PersonaState", _FakePersona)
    monkeypatch.setattr(features, "get_features", lambda: {"demo": feat})
    return feat


def test_ensure_concurrent_no_double_build(patched):
    """三个协程并发 ensure，引擎只构建一次。"""
    feat = patched
    core = MingbuCore()
    owner = _fake_owner()

    async def concurrent():
        await asyncio.gather(
            core.ensure(owner),
            core.ensure(owner),
            core.ensure(owner),
        )

    asyncio.run(concurrent())
    assert core._ensured is True
    assert feat.builds == 1, f"build 被调用 {feat.builds} 次，应为 1（并发不应双建引擎）"
