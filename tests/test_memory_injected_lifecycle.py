"""跨轮注入 id 生命周期测试。

缺陷回归：某轮无记忆注入时，应清空 KV 残留，避免下一轮用户
「记错了」误降权已反馈过的旧记忆。clear_injected 用于显式清空。
"""
import asyncio

import pytest

from memory_core import MemoryCore


class _KVPlugin:
    def __init__(self):
        self.kv = {}

    async def get_kv_data(self, key, default=None):
        return self.kv.get(key, default)

    async def put_kv_data(self, key, val):
        self.kv[key] = val


class _DummyMem:
    def __init__(self, mid):
        self.id = mid


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def test_clear_injected_empties_kv():
    """清空后 KV 应返回空列表。"""
    core = MemoryCore(_KVPlugin())
    _run(core.remember_injected("u1", ["m1", "m2"]))
    assert _run(core.get_last_injected("u1")) == ["m1", "m2"]
    _run(core.clear_injected("u1"))
    assert _run(core.get_last_injected("u1")) == []


def test_no_inject_round_clears_stale():
    """无注入轮次调用 clear_injected（而非依赖 remember_injected 短路）。"""
    core = MemoryCore(_KVPlugin())
    # 第一轮注入
    _run(core.remember_injected("u1", ["old"]))
    # 第二轮无注入 → 显式清空
    _run(core.clear_injected("u1"))
    # 第三轮用户反馈 → 应读到空，不误降权
    assert _run(core.get_last_injected("u1")) == []


def test_remember_injected_empty_short_circuits():
    """remember_injected 传空列表时短路（不覆盖 KV），故必须用 clear_injected 显式清。"""
    core = MemoryCore(_KVPlugin())
    _run(core.remember_injected("u1", ["m1"]))
    _run(core.remember_injected("u1", []))  # 短路，KV 不变
    assert _run(core.get_last_injected("u1")) == ["m1"]
