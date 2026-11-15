"""mention_policy（提及策略）单元测试。

借鉴 memory_companion 的 mention_policy，防"主动戳痛"：
- 记忆按策略分四档（direct/soft_echo/tone_only/avoid_unless_asked）；
- 默认按类型 + 情绪极性推导，负向情绪记忆降为 avoid_unless_asked；
- 持久化往返（to_dict/from_dict）保留该字段，老数据回退类型默认；
- persona_ctx 注入时只让 direct/soft_echo 进正文、tone_only 进情绪底色、
  avoid_unless_asked 默认不注入。
"""
import pytest

from memory_core import (
    EMOTIONAL,
    EPISODIC,
    GROWTH,
    MENTION_AVOID,
    MENTION_DIRECT,
    MENTION_SOFT_ECHO,
    MENTION_TONE_ONLY,
    SEMANTIC,
    Memory,
    MemoryCore,
)


def _m(mtype, emo=0.0, **kw):
    return Memory("u1", mtype, "测试内容", emotional_weight=emo, **kw)


# ---- 默认策略推导 ----
def test_semantic_defaults_direct():
    assert _m(SEMANTIC).mention_policy == MENTION_DIRECT


def test_growth_defaults_direct():
    assert _m(GROWTH).mention_policy == MENTION_DIRECT


def test_positive_emotion_defaults_tone_only():
    # 正向情绪记忆只影响语气，可被自然流露（tone_only）
    assert _m(EMOTIONAL, emo=0.6).mention_policy == MENTION_TONE_ONLY


def test_negative_emotion_downgrades_to_avoid():
    # 负向情绪记忆（伤疤）默认不主动提（防戳痛核心）
    assert _m(EMOTIONAL, emo=-0.7).mention_policy == MENTION_AVOID


def test_episodic_defaults_soft_echo():
    assert _m(EPISODIC).mention_policy == MENTION_SOFT_ECHO


def test_explicit_policy_overrides_default():
    # 写入侧显式指定可覆盖推导（如把伤疤标记为 avoid，或把情绪升为 direct）
    assert _m(SEMANTIC, mention_policy=MENTION_AVOID).mention_policy == MENTION_AVOID
    assert _m(EMOTIONAL, emo=-0.9, mention_policy=MENTION_DIRECT).mention_policy == MENTION_DIRECT


# ---- 持久化往返 ----
def test_mention_policy_roundtrip():
    m = _m(EMOTIONAL, emo=-0.5)  # 推导为 avoid
    d = m.to_dict()
    assert d["mention_policy"] == MENTION_AVOID
    m2 = Memory.from_dict(d)
    assert m2.mention_policy == MENTION_AVOID


def test_legacy_data_falls_back_to_type_default():
    # 老数据无 mention_policy 键：from_dict 按类型默认回退
    legacy = {
        "id": "abc", "user_id": "u1", "type": SEMANTIC,
        "content": "x", "importance": 0.5, "emotional_weight": 0.0,
        "tags": [], "created_at": "2026-01-01T00:00:00", "last_accessed": "2026-01-01T00:00:00",
        "access_count": 0, "decay_score": 1.0,
    }
    m = Memory.from_dict(legacy)
    assert m.mention_policy == MENTION_DIRECT  # semantic 默认 direct


# ---- add 透传 ----
@pytest.mark.asyncio
async def test_add_passes_mention_policy():
    core = MemoryCore.__new__(MemoryCore)  # 只测参数透传，跳过 plugin 依赖
    captured = {}

    async def fake_save(uid):
        captured["saved"] = uid

    async def fake_load(uid):
        return []

    core._load = fake_load
    core._save = fake_save
    core._max_memories = lambda: 1000
    core._compact = lambda uid: None

    mem = await core.add("u1", EMOTIONAL, "很难过", emotional_weight=-0.8,
                         mention_policy=MENTION_TONE_ONLY)
    assert mem.mention_policy == MENTION_TONE_ONLY


# ---- split_by_mention 分组 ----
def test_split_by_mention_groups():
    mems = [
        _m(SEMANTIC),                 # direct
        _m(EPISODIC),                 # soft_echo
        _m(EMOTIONAL, emo=0.6),       # tone_only
        _m(EMOTIONAL, emo=-0.7),      # avoid
    ]
    groups = MemoryCore.split_by_mention(mems)
    assert set(groups.keys()) == {MENTION_DIRECT, MENTION_SOFT_ECHO, MENTION_TONE_ONLY, MENTION_AVOID}
    assert len(groups[MENTION_DIRECT]) == 1
    assert len(groups[MENTION_AVOID]) == 1


def test_split_by_mention_skips_none_group():
    assert MemoryCore.split_by_mention([]) == {}


# ---- persona_ctx 注入过滤（纯逻辑，复现分槽规则）----
def test_injection_filter_sayable_vs_tone():
    important = [_m(SEMANTIC), _m(EPISODIC), _m(GROWTH)]
    emotional = [_m(EMOTIONAL, emo=0.6), _m(EMOTIONAL, emo=-0.7)]

    sayable = [m for m in important if m.mention_policy in (MENTION_DIRECT, MENTION_SOFT_ECHO)]
    tone_emo = [m for m in emotional if m.mention_policy == MENTION_TONE_ONLY]
    injected_avoid = [m for m in emotional if m.mention_policy == MENTION_AVOID]

    # 正文含 direct/soft_echo（semantic/growth/episodic 全部可说）
    assert len(sayable) == 3
    # 情绪底色只含 tone_only（正向情绪）
    assert len(tone_emo) == 1
    # avoid_unless_asked 不进任何槽
    assert len(injected_avoid) == 1  # 被过滤出，不注入
    assert all(m.mention_policy != MENTION_AVOID for m in sayable + tone_emo)


# ---- feedback() 回写反馈 ----
@pytest.mark.asyncio
async def test_feedback_accept_raises_importance():
    core = MemoryCore.__new__(MemoryCore)
    mems = [_m(SEMANTIC)]  # importance 默认 0.5
    core._load = lambda uid: _async_return(mems)
    core._save = lambda uid: _async_return(None)

    ok = await core.feedback("u1", mems[0].id, "accept")
    assert ok is True
    assert mems[0].importance == pytest.approx(0.55)
    assert mems[0].mention_policy == MENTION_DIRECT  # accept 不改策略


@pytest.mark.asyncio
async def test_feedback_reject_demotes_to_avoid():
    core = MemoryCore.__new__(MemoryCore)
    mems = [_m(SEMANTIC)]
    core._load = lambda uid: _async_return(mems)
    core._save = lambda uid: _async_return(None)

    ok = await core.feedback("u1", mems[0].id, "reject")
    assert ok is True
    assert mems[0].importance == pytest.approx(0.35)
    assert mems[0].mention_policy == MENTION_AVOID


@pytest.mark.asyncio
async def test_feedback_correct_adds_tag_and_avoid():
    core = MemoryCore.__new__(MemoryCore)
    mems = [_m(EMOTIONAL, emo=0.6)]  # 原 tone_only
    core._load = lambda uid: _async_return(mems)
    core._save = lambda uid: _async_return(None)

    ok = await core.feedback("u1", mems[0].id, "correct")
    assert ok is True
    assert "纠正" in mems[0].tags
    assert mems[0].mention_policy == MENTION_AVOID


@pytest.mark.asyncio
async def test_feedback_unknown_id_returns_false():
    core = MemoryCore.__new__(MemoryCore)
    core._load = lambda uid: _async_return([])
    core._save = lambda uid: _async_return(None)

    ok = await core.feedback("u1", "nonexistent", "reject")
    assert ok is False


def _async_return(val):
    import asyncio
    fut = asyncio.get_event_loop().create_future()
    fut.set_result(val)
    return fut
