"""群聊/私聊记忆隔离（scope）回归测试 -- 2.0 第一阶段任务 3（方案 B）。

契约：
- Memory 增加 scope 字段（private/group），写入按会话标记。
- 群聊召回（max_scope=group）只返回 group scope，私聊秘密绝不泄漏到群。
- 私聊召回（max_scope=PRIVATE/None）保留全部记忆连贯性。
- 老数据（KV 无 scope 键）保守回退 private。

不依赖 astrbot 运行时，用内存 KV 桩直接测 MemoryCore 语义。
"""
import asyncio

import pytest

from memory_core import (
    DEFAULT_SCOPE,
    SCOPE_GROUP,
    SCOPE_PRIVATE,
    Memory,
    MemoryCore,
)


class _KVPlugin:
    """最小内存 KV 桩，供 MemoryCore 读写。"""

    def __init__(self):
        self.store = {}

    async def get_kv_data(self, key, default=None):
        return self.store.get(key, default)

    async def put_kv_data(self, key, value):
        self.store[key] = value

    _config = {}


def _run(coro):
    return asyncio.run(coro)


def _mem():
    return MemoryCore(_KVPlugin())


def test_memory_scope_defaults_private():
    """未指定 scope 时默认 private（保守，防泄漏）。"""
    m = Memory("u1", "episodic", "我在做 AI 心理陪伴项目")
    assert m.scope == SCOPE_PRIVATE
    assert DEFAULT_SCOPE == SCOPE_PRIVATE


def test_add_persists_scope():
    """add(scope=...) 写入并落盘后能读回正确 scope。"""
    async def _run2():
        mc = _mem()
        await mc.add("u1", "semantic", "喜欢猫", scope=SCOPE_GROUP)
        await mc.flush_all()
        got = await mc.recall_recent("u1", n=10)
        return got

    got = _run(_run2())
    assert len(got) == 1
    assert got[0].scope == SCOPE_GROUP


def test_group_recall_excludes_private():
    """群聊召回（max_scope=group）只返回 group scope，private 秘密被过滤。"""
    async def _run2():
        mc = _mem()
        await mc.add("u1", "semantic", "我在做 AI 心理陪伴项目",
                     importance=0.9, scope=SCOPE_PRIVATE)
        await mc.add("u1", "semantic", "我喜欢猫", importance=0.9,
                     scope=SCOPE_GROUP)
        return await mc.recall_important("u1", n=10, max_scope=SCOPE_GROUP)

    got = _run(_run2())
    contents = [m.content for m in got]
    assert "我喜欢猫" in contents
    assert all("AI 心理陪伴" not in c for c in contents), \
        "群聊召回不得包含 private 秘密"


def test_private_recall_keeps_all():
    """私聊召回（不过滤）保留 private + group 全部记忆连贯性。"""
    async def _run2():
        mc = _mem()
        await mc.add("u1", "semantic", "我在做 AI 心理陪伴项目",
                     importance=0.9, scope=SCOPE_PRIVATE)
        await mc.add("u1", "semantic", "我喜欢猫", importance=0.9,
                     scope=SCOPE_GROUP)
        return await mc.recall_important("u1", n=10, max_scope=None)

    got = _run(_run2())
    assert len(got) == 2


def test_old_data_without_scope_falls_back_private():
    """老 KV 记录无 scope 键 → from_dict 回退 private（不泄漏）。"""
    legacy = {
        "id": "abc", "user_id": "u1", "type": "semantic",
        "content": "老记忆", "source": "chat", "importance": 0.8,
        "emotional_weight": 0.0, "tags": [],
        "created_at": "2025-01-01T00:00:00", "last_accessed": "2025-01-01T00:00:00",
        "access_count": 0, "decay_score": 1.0, "mention_policy": "direct",
        # 故意无 scope 键
    }
    m = Memory.from_dict(legacy)
    assert m.scope == SCOPE_PRIVATE


def test_scope_roundtrip_dict():
    """to_dict → from_dict 保住 scope。"""
    m = Memory("u1", "episodic", "群聊说的", scope=SCOPE_GROUP)
    m2 = Memory.from_dict(m.to_dict())
    assert m2.scope == SCOPE_GROUP


def test_illegal_scope_falls_back_private():
    """非法 scope 值 → from_dict 回退 private。"""
    d = Memory("u1", "semantic", "x", scope=SCOPE_GROUP).to_dict()
    d["scope"] = "public"  # 非法值
    m = Memory.from_dict(d)
    assert m.scope == SCOPE_PRIVATE


def test_emotional_group_recall_excludes_private():
    """情绪记忆召回同样受 max_scope 约束。"""
    async def _run2():
        mc = _mem()
        await mc.add("u1", "emotional", "私下很难过",
                     emotional_weight=-0.8, scope=SCOPE_PRIVATE)
        await mc.add("u1", "emotional", "群里很开心",
                     emotional_weight=0.7, scope=SCOPE_GROUP)
        return await mc.recall_emotional("u1", n=10, max_scope=SCOPE_GROUP)

    got = _run(_run2())
    contents = [m.content for m in got]
    assert all("私下很难过" not in c for c in contents)
    assert any("群里很开心" in c for c in contents)


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
