"""自我进化（self_evolution）run_nightly 写入路径回归测试。

回归背景（2026-11-05 复核发现）：_process_single_user 原调用
``self.memory.add_memory(...)``，但 MemoryCore 只暴露 add()（grep 全仓：
定义 0 处、此调用 1 处）。该功能默认关闭（features.py default=False）且
run_nightly 无测试覆盖，故长期未暴露；一旦开启，每晚提炼必抛
AttributeError，导致「显性事实」断言从未真正落库。
本测试补上覆盖，确保 facts 走 MemoryCore.add() 落库而非抛异常。
"""
import asyncio
from types import SimpleNamespace

import pytest

from self_evolution import SelfEvolution, parse_facts


class _FakeMemory:
    """记录 add() 调用的记忆桩。"""

    def __init__(self):
        self.added = []

    async def recall_recent(self, uid, n=20):
        return [SimpleNamespace(content="用户说：我最近在备考")]

    async def add(self, uid, mtype, content, **kw):
        self.added.append((uid, mtype, content, kw))


class _FakePlugin:
    def __init__(self, llm_out):
        self._llm_out = llm_out
        self.kv = {}

    async def get_kv_data(self, key, default=None):
        return self.kv.get(key, default)

    async def put_kv_data(self, key, val):
        self.kv[key] = val

    async def _llm_text(self, prompt, task_type=None):
        return self._llm_out


@pytest.mark.asyncio
async def test_run_nightly_writes_facts_via_add():
    # LLM 返回 2 条事实（JSON 数组）
    plugin = _FakePlugin('["我最近在备考", "我不喜欢被催"]')
    mem = _FakeMemory()
    plugin.kv["mingbu.sessions"] = '{"sess1": {"uid": "u1"}}'

    evo = SelfEvolution(plugin, mem)
    await evo.run_nightly()

    # facts 应经 parse_facts 解析后逐条写入，且类型为 SEMANTIC
    assert len(mem.added) == 2
    for uid, mtype, content, kw in mem.added:
        assert uid == "u1"
        assert mtype == "semantic"
        assert content in {"我最近在备考", "我不喜欢被催"}
        assert kw.get("source") == "self_evolution"


@pytest.mark.asyncio
async def test_run_nightly_no_facts_no_write():
    plugin = _FakePlugin("[]")  # 无事实
    mem = _FakeMemory()
    plugin.kv["mingbu.sessions"] = '{"sess1": {"uid": "u1"}}'

    evo = SelfEvolution(plugin, mem)
    await evo.run_nightly()

    assert mem.added == []


@pytest.mark.asyncio
async def test_run_nightly_no_recent_skips_llm():
    class _EmptyMem(_FakeMemory):
        async def recall_recent(self, uid, n=20):
            return []

    plugin = _FakePlugin('["x"]')
    mem = _EmptyMem()
    plugin.kv["mingbu.sessions"] = '{"sess1": {"uid": "u1"}}'

    evo = SelfEvolution(plugin, mem)
    await evo.run_nightly()

    # 无近期记忆 → 提前返回，不调 LLM、不写入
    assert mem.added == []
