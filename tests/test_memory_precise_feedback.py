"""记忆反馈精细化：隐式引用标记 + 单条精确反馈。

注入文本携带不透明短码（〔M1〕…），主 LLM 只见短码、见不到真实 id；
用户消息含短码时只对该条精确 feedback，无短码时回退全量（向后兼容）。
"""
import pytest

from memory_core import MENTION_AVOID, SEMANTIC, Memory, MemoryCore


class _KVPlugin:
    def __init__(self):
        self.kv = {}

    async def get_kv_data(self, key, default=None):
        return self.kv.get(key, default)

    async def put_kv_data(self, key, val):
        self.kv[key] = val


# ---- to_brief_with_refs：注入文本带不透明短码 ----
def test_brief_with_refs_assigns_codes():
    mems = [
        Memory("u1", SEMANTIC, "内容甲"),
        Memory("u1", SEMANTIC, "内容乙"),
    ]
    mapping = {}
    text = MemoryCore.to_brief_with_refs(mems, 50, mapping)
    assert "内容甲" in text and "内容乙" in text
    # 每条记忆分到一个短码，映射到真实 id
    assert set(mapping.values()) == {m.id for m in mems}
    # 短码出现在文本里
    for code in mapping:
        assert code in text


def test_brief_refs_codes_are_opaque():
    """短码不含真实 id，防泄漏。"""
    mem = Memory("u1", SEMANTIC, "秘密内容")
    mapping = {}
    text = MemoryCore.to_brief_with_refs([mem], 50, mapping)
    code = next(iter(mapping))
    assert mem.id not in code and mem.id not in text.replace(mem.content, "")


def test_brief_with_refs_empty():
    mapping = {}
    assert MemoryCore.to_brief_with_refs([], 50, mapping) == "（暂无记忆）"
    assert mapping == {}


# ---- parse_referenced_ids：从用户消息提取短码 → 真实 id ----
@pytest.mark.asyncio
async def test_parse_referenced_ids_extracts_and_resolves():
    core = MemoryCore(_KVPlugin())
    mapping = {"〔M1〕": "id-a", "〔M2〕": "id-b"}
    await core.remember_injected_refs("u1", mapping)
    got = await core.parse_referenced_ids("u1", "〔M1〕不是这样的")
    assert got == ["id-a"]


@pytest.mark.asyncio
async def test_parse_referenced_ids_multiple():
    core = MemoryCore(_KVPlugin())
    await core.remember_injected_refs(
        "u1", {"〔M1〕": "id-a", "〔M2〕": "id-b"})
    got = await core.parse_referenced_ids("u1", "〔M2〕记错了 〔M1〕")
    assert set(got) == {"id-a", "id-b"}


@pytest.mark.asyncio
async def test_parse_referenced_ids_none_when_no_code():
    """无短码 → 空列表（调用方据此回退全量）。"""
    core = MemoryCore(_KVPlugin())
    await core.remember_injected_refs("u1", {"〔M1〕": "id-a"})
    assert await core.parse_referenced_ids("u1", "不是这样的") == []


# ---- 精确反馈：只降权被引用那条 ----
@pytest.mark.asyncio
async def test_precise_feedback_only_referenced_demoted():
    plugin = _KVPlugin()
    core = MemoryCore(plugin)
    mem_a = await core.add("u1", SEMANTIC, "甲", importance=0.6)
    mem_b = await core.add("u1", SEMANTIC, "乙", importance=0.6)

    mapping = {"〔M1〕": mem_a.id, "〔M2〕": mem_b.id}
    await core.remember_injected_refs("u1", mapping)

    # 用户只否认 M1
    refs = await core.parse_referenced_ids("u1", "〔M1〕不是这样的")
    for mid in refs:
        await core.feedback("u1", mid, "reject")

    fresh = {m.id: m for m in await core._load("u1")}
    assert fresh[mem_a.id].mention_policy == MENTION_AVOID
    assert fresh[mem_a.id].importance == pytest.approx(0.45)
    # 未被引用的 M2 不受影响
    assert fresh[mem_b.id].mention_policy != MENTION_AVOID
    assert fresh[mem_b.id].importance == pytest.approx(0.6)


# ---- remember_injected_refs 与旧 remember_injected 并存 ----
@pytest.mark.asyncio
async def test_remember_refs_still_supports_ids_roundtrip():
    """旧 id 接口不破坏（向后兼容）。"""
    core = MemoryCore(_KVPlugin())
    await core.remember_injected("u1", ["x"])
    assert await core.get_last_injected("u1") == ["x"]
