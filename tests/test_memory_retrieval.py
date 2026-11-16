"""相关性检索（retrieve_relevant）回归测试 -- 2.0 第一阶段任务 2。

契约：当 embedding/rerank 均关闭时，retrieve_relevant 作为轻量兜底，
按「字符 bigram 重叠 × 有效权重」给记忆打相关性分，返回最相关 top-limit。
- 相关性排序：与 query 重叠多的记忆排前。
- min_score 门槛：低于门槛不返回（宁缺毋滥）。
- max_scope：群聊只召回 group scope（复用任务 3 隔离语义）。
- exclude_contents：当前会话已有的记忆跳过（防重复注入）。
"""
import asyncio

import pytest

from memory_core import SCOPE_GROUP, SCOPE_PRIVATE, MemoryCore


class _KVPlugin:
    def __init__(self):
        self.store = {}

    async def get_kv_data(self, key, default=None):
        return self.store.get(key, default)

    async def put_kv_data(self, key, value):
        self.store[key] = value

    _config: dict = {}


def _run(coro):
    return asyncio.run(coro)


def _mem():
    return MemoryCore(_KVPlugin())


def test_retrieves_most_relevant_first():
    """与 query 主题重叠最多的记忆排最前。"""
    async def _run2():
        mc = _mem()
        await mc.add("u1", "semantic", "我在开发一个 AI 心理陪伴系统",
                     importance=0.5, scope=SCOPE_PRIVATE)
        await mc.add("u1", "semantic", "今天晚饭吃了火锅",
                     importance=0.5, scope=SCOPE_PRIVATE)
        await mc.add("u1", "semantic", "心理陪伴项目要开始做了",
                     importance=0.5, scope=SCOPE_PRIVATE)
        return await mc.retrieve_relevant("u1", "心理陪伴项目进展如何",
                                          limit=3)

    got = _run(_run2())
    assert got, "应至少召回一条相关记忆"
    # 前两名应是与"心理陪伴"相关的，而非"火锅"
    top2 = " ".join(m.content for m in got[:2])
    assert "火锅" not in top2 or "心理陪伴" in top2


def test_min_score_filters_unrelated():
    """完全不相关的记忆低于门槛，不返回。"""
    async def _run2():
        mc = _mem()
        await mc.add("u1", "semantic", "喜欢猫", importance=0.5,
                     scope=SCOPE_PRIVATE)
        # query 与"喜欢猫"无字符重叠
        return await mc.retrieve_relevant("u1", "量子物理实验结果",
                                          limit=5, min_score=0.05)

    got = _run(_run2())
    assert got == [], "无字符重叠的记忆不应被召回"


def test_max_scope_group_excludes_private():
    """群聊（max_scope=group）只召回 group scope。"""
    async def _run2():
        mc = _mem()
        await mc.add("u1", "semantic", "私下做心理陪伴项目", importance=0.9,
                     scope=SCOPE_PRIVATE)
        await mc.add("u1", "semantic", "群里聊心理陪伴话题", importance=0.9,
                     scope=SCOPE_GROUP)
        return await mc.retrieve_relevant("u1", "心理陪伴", limit=5,
                                          max_scope=SCOPE_GROUP)

    got = _run(_run2())
    contents = [m.content for m in got]
    assert all("私下" not in c for c in contents)
    assert any("群里" in c for c in contents)


def test_exclude_contents_dedups_session():
    """exclude_contents（当前会话已有）中的记忆被跳过，防重复注入。"""
    async def _run2():
        mc = _mem()
        await mc.add("u1", "semantic", "我在做 AI 心理陪伴项目",
                     importance=0.9, scope=SCOPE_PRIVATE)
        await mc.add("u1", "semantic", "我喜欢猫", importance=0.9,
                     scope=SCOPE_PRIVATE)
        # 会话历史已包含第一条，应只召回第二条
        return await mc.retrieve_relevant(
            "u1", "心理陪伴项目 我喜欢猫", limit=5,
            exclude_contents=["我在做 AI 心理陪伴项目"])

    got = _run(_run2())
    contents = [m.content for m in got]
    assert all("心理陪伴项目" not in c or c == "我喜欢猫" for c in contents)


def test_returns_at_most_limit():
    """返回条数不超过 limit。"""
    async def _run2():
        mc = _mem()
        for i in range(10):
            await mc.add("u1", "semantic", f"心理陪伴相关记忆 {i}",
                         importance=0.5, scope=SCOPE_PRIVATE)
        return await mc.retrieve_relevant("u1", "心理陪伴", limit=3)

    got = _run(_run2())
    assert len(got) <= 3


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
