"""2.0 第二阶段：记忆元数据（confidence/verified/expires_at）+ 去重强化回归测试。

元数据契约：
- Memory 加 confidence（可信度）/verified（是否用户确认）/expires_at（过期）。
- 老数据 from_dict 回退：confidence=INFERRED、verified=False、expires_at=None。
- is_expired() 按期判过期，解析失败保守视为未过期。
去重强化契约：
- 标点/空格/大小写差异 → 归一化后判重（不漏判）。
- 轻微改写（bigram 重叠 >=0.85）→ 判重（不重复写入膨胀）。
- 不同内容 → 不误判，正常新增。
"""
import asyncio

import pytest

from memory_core import (
    CONFIDENCE_INFERRED,
    CONFIDENCE_USER_STATED,
    SCOPE_PRIVATE,
    Memory,
    MemoryCore,
)


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


# ---- 元数据字段 ----

def test_memory_metadata_defaults():
    """未指定元数据时默认为推测档 + 未确认 + 不过期。"""
    m = Memory("u1", "semantic", "喜欢猫")
    assert m.confidence == CONFIDENCE_INFERRED
    assert m.verified is False
    assert m.expires_at is None


def test_memory_metadata_roundtrip():
    """to_dict → from_dict 保住元数据。"""
    m = Memory("u1", "semantic", "我是工程师", confidence=0.95,
               verified=True, expires_at="2099-01-01T00:00:00")
    m2 = Memory.from_dict(m.to_dict())
    assert m2.confidence == 0.95
    assert m2.verified is True
    assert m2.expires_at == "2099-01-01T00:00:00"


def test_old_data_metadata_fallback():
    """老 KV 无元数据键 → 安全回退（不崩、不假装用户确认过）。"""
    legacy = {
        "id": "x", "user_id": "u1", "type": "semantic", "content": "老记忆",
        "source": "chat", "importance": 0.5, "emotional_weight": 0.0,
        "tags": [], "created_at": "2025-01-01T00:00:00",
        "last_accessed": "2025-01-01T00:00:00", "access_count": 0,
        "decay_score": 1.0, "mention_policy": "direct", "scope": "private",
        # 无 confidence/verified/expires_at
    }
    m = Memory.from_dict(legacy)
    assert m.confidence == CONFIDENCE_INFERRED
    assert m.verified is False
    assert m.expires_at is None


def test_confidence_clamped():
    """confidence 越界被钳制到 [0,1]。"""
    assert Memory("u1", "semantic", "x", confidence=2.0).confidence == 1.0
    assert Memory("u1", "semantic", "x", confidence=-1.0).confidence == 0.0


def test_is_expired():
    """expires_at 早于当前 → 过期；None/解析失败 → 未过期。"""
    past = Memory("u1", "semantic", "x", expires_at="2000-01-01T00:00:00")
    future = Memory("u1", "semantic", "x", expires_at="2099-01-01T00:00:00")
    none_exp = Memory("u1", "semantic", "x")
    bad = Memory("u1", "semantic", "x", expires_at="not-a-date")

    assert past.is_expired() is True
    assert future.is_expired() is False
    assert none_exp.is_expired() is False
    assert bad.is_expired() is False  # 解析失败保守保留


def test_add_persists_metadata():
    """add(confidence=...) 写入并落盘后能读回。"""
    async def _run2():
        mc = _mem()
        await mc.add("u1", "semantic", "我是医生", confidence=0.95,
                     verified=True)
        await mc.flush_all()
        return await mc.recall_recent("u1", n=5)

    got = _run(_run2())
    assert got[0].confidence == 0.95
    assert got[0].verified is True


# ---- 去重强化 ----

def test_dedup_punctuation_insensitive():
    """标点差异 → 归一化后判重，不新增条目。"""
    async def _run2():
        mc = _mem()
        m1 = await mc.add("u1", "semantic", "我喜欢猫")
        m2 = await mc.add("u1", "semantic", "我喜欢猫！")  # 仅尾标点差异
        return m1, m2

    m1, m2 = _run(_run2())
    assert m1.id == m2.id, "标点差异应判重，复用同一条"


def test_dedup_whitespace_case_insensitive():
    """空格/大小写差异 → 判重。"""
    async def _run2():
        mc = _mem()
        m1 = await mc.add("u1", "semantic", "Hello World")
        m2 = await mc.add("u1", "semantic", "hello  world")
        return m1, m2

    m1, m2 = _run(_run2())
    assert m1.id == m2.id


def test_dedup_light_rewrite():
    """轻微改写（高 bigram 重叠）→ 判重，避免重复膨胀。
    注意：完全相同串只命中第一层精确指纹，这里补一个真正的「多几字」改写，
    命中第二层 bigram 相似度（>=0.85）路径。
    """
    async def _run2():
        mc = _mem()
        m1 = await mc.add("u1", "semantic", "我在开发一个AI心理陪伴系统")
        # 轻微改写：加了个"个"
        m2 = await mc.add("u1", "semantic", "我在开发一个AI心理陪伴系统")
        return m1, m2

    m1, m2 = _run(_run2())
    assert m1.id == m2.id

    # 真正的改写：长句末尾多个语气词（bigram 交集仍 >=0.85）
    async def _run3():
        mc = _mem()
        m1 = await mc.add("u1", "semantic", "我喜欢在周末去公园散步放松心情")
        m2 = await mc.add("u1", "semantic", "我喜欢在周末去公园散步放松心情啊")  # 末尾多"啊"
        return m1, m2

    m1b, m2b = _run(_run3())
    assert m1b.id == m2b.id, "轻微改写（多语气词）应命中 bigram 相似度判重"


def test_no_false_positive_dedup():
    """不同内容 → 不误判，正常新增两条。"""
    async def _run2():
        mc = _mem()
        await mc.add("u1", "semantic", "我喜欢猫")
        await mc.add("u1", "semantic", "我讨厌下雨天")
        return await mc.recall_recent("u1", n=10)

    got = _run(_run2())
    assert len(got) == 2, "内容差异大的记忆不应被误判为重复"


def test_dedup_respects_type():
    """同内容不同 type → 不跨类型判重。"""
    async def _run2():
        mc = _mem()
        await mc.add("u1", "semantic", "喜欢猫")
        await mc.add("u1", "episodic", "喜欢猫")  # 不同类型
        return await mc.recall_recent("u1", n=10)

    got = _run(_run2())
    assert len(got) == 2, "不同 type 不应互相判重"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
