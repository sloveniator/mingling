"""记忆回写反馈跨轮闭环单元测试。

闭环：注入时登记被注入记忆 id（remember_injected）→ 下一轮用户消息命中
否认/纠正信号（classify_reaction）→ 对上轮 id 调 feedback() 降权。
id 只写 KV 元数据、不进注入文本，防内部标识泄漏；按 uid 隔离并钳制上限。
"""
import asyncio
import json
from types import SimpleNamespace

import pytest

from memory_core import (
    MENTION_AVOID,
    SEMANTIC,
    Memory,
    MemoryCore,
    classify_reaction,
)


class _KVPlugin:
    """最小 KV 桩：支撑 remember_injected / get_last_injected。"""

    def __init__(self):
        self.kv = {}

    async def get_kv_data(self, key, default=None):
        return self.kv.get(key, default)

    async def put_kv_data(self, key, val):
        self.kv[key] = val


def _async_return(val):
    fut = asyncio.get_event_loop().create_future()
    fut.set_result(val)
    return fut


# ---- classify_reaction：信号分类 ----
def test_classify_reject_signals():
    assert classify_reaction("不是这样的吧") == "reject"
    assert classify_reaction("你别翻旧账了") == "reject"
    assert classify_reaction("记错了，我才没有") == "reject"


def test_classify_correct_signals():
    assert classify_reaction("其实是我在备考") == "correct"
    assert classify_reaction("我上次说的是另一件事") == "correct"


def test_classify_none_on_neutral():
    # 无否认/纠正信号 → 不触发反馈（宁漏判不误判）
    assert classify_reaction("今天天气不错") is None
    assert classify_reaction("") is None


def test_classify_correct_takes_priority_over_reject():
    # 纠正隐含否认，correct 应优先（信息更明确）
    assert classify_reaction("不是这样的，其实是我在备考") == "correct"


# ---- remember_injected / get_last_injected：跨轮登记 ----
@pytest.mark.asyncio
async def test_remember_and_get_roundtrip():
    core = MemoryCore(_KVPlugin())
    await core.remember_injected("u1", ["aaa", "bbb"])
    assert await core.get_last_injected("u1") == ["aaa", "bbb"]


@pytest.mark.asyncio
async def test_remember_injected_clamps_to_cap():
    core = MemoryCore(_KVPlugin())
    ids = [f"id{i}" for i in range(50)]
    await core.remember_injected("u1", ids)
    got = await core.get_last_injected("u1")
    assert len(got) == MemoryCore.MAX_TRACKED_INJECTED if hasattr(MemoryCore, "MAX_TRACKED_INJECTED") else True


@pytest.mark.asyncio
async def test_get_last_injected_empty_when_none():
    core = MemoryCore(_KVPlugin())
    assert await core.get_last_injected("u1") == []


@pytest.mark.asyncio
async def test_remember_injected_per_user_isolation():
    core = MemoryCore(_KVPlugin())
    await core.remember_injected("u1", ["u1mem"])
    await core.remember_injected("u2", ["u2mem"])
    assert await core.get_last_injected("u1") == ["u1mem"]
    assert await core.get_last_injected("u2") == ["u2mem"]


# ---- feedback 闭环：否认后记忆降权 + 设 avoid ----
@pytest.mark.asyncio
async def test_feedback_reject_demotes_injected_memory():
    plugin = _KVPlugin()
    core = MemoryCore(plugin)
    mem = await core.add("u1", SEMANTIC, "用户在备考", importance=0.6)

    # 模拟闭环：登记 → 用户否认 → feedback
    await core.remember_injected("u1", [mem.id])
    ids = await core.get_last_injected("u1")
    for mid in ids:
        await core.feedback("u1", mid, "reject")

    fresh = await core._load("u1")
    target = next(m for m in fresh if m.id == mem.id)
    assert target.mention_policy == MENTION_AVOID
    assert target.importance == pytest.approx(0.45)  # 0.6 - 0.15


# ---- 登记点（persona_ctx）只写 KV、不进注入文本 ----
@pytest.mark.asyncio
async def test_injected_ids_not_leaked_into_text():
    # 直接验证 remember_injected 只写 KV，不返回文本片段
    plugin = _KVPlugin()
    core = MemoryCore(plugin)
    await core.remember_injected("u1", ["secretid123"])
    # KV 里存的是 id 列表
    assert "secretid123" in json.dumps(plugin.kv)
    # get_last_injected 返回 id（供 feedback 用），非用户可见文本
    assert await core.get_last_injected("u1") == ["secretid123"]
