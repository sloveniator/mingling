"""全局成长反思：打分、筛选、去重、预算与回退。"""
import asyncio

from config import CompanionConfig
from growth_global import build_growth_pack
from growth_signal import score_growth_signal
from main import MinglingPlugin


def _sig(text, score, umo="u1", ts=1.0):
    return {"text": text, "score": score, "tags": ["test"], "ts": ts, "umo": umo}


def test_score_high_value_above_threshold():
    s = score_growth_signal("我决定下个月辞职，去杭州找工作，虽然有点害怕，但我想试试")
    assert s["score"] >= 0.55
    assert "decision" in s["tags"]


def test_score_low_value_below_threshold():
    assert score_growth_signal("哈哈")["score"] < 0.55
    assert score_growth_signal("谢谢")["score"] < 0.55


def test_pack_filters_low_value_and_dedupes():
    pack = build_growth_pack([
        _sig("我决定下个月辞职，去杭州找工作，虽然有点害怕，但我想试试", 0.9),
        _sig("我决定下个月辞职，去杭州找工作，虽然有点害怕但想试试", 0.8),
        _sig("今天天气不错", 0.2),
    ], min_score=0.55)
    assert pack["stats"]["before_count"] == 3
    assert pack["stats"]["filtered_count"] == 1
    assert pack["stats"]["dedupe_dropped"] >= 1
    assert pack["stats"]["after_count"] == 1
    assert "天气" not in pack["items"][0]["text"]


def test_pack_count_and_token_limits():
    texts = [
        "我决定辞职去杭州找工作，虽然害怕但想试试。",
        "这次考试没通过，我明白需要调整复习计划。",
        "妈妈住院了，我意识到家人比工作更重要。",
        "终于完成搬家，我总结出提前列清单很有用。",
        "我和朋友吵架后道歉，发现沟通方式需要改变。",
    ]
    signals = [_sig(t, 0.9, ts=float(i)) for i, t in enumerate(texts)]
    pack = build_growth_pack(signals, max_items=2, token_budget=1000)
    assert pack["stats"]["count_truncated"] == 3
    assert pack["stats"]["after_count"] == 2

    tight = build_growth_pack(signals, max_items=5, token_budget=5)
    assert tight["stats"]["token_truncated"] >= 1
    assert tight["stats"]["after_count"] <= 1
    assert tight["stats"]["token_method"].startswith("estimate")


class FakeStore:
    def __init__(self, per_user):
        self.per_user = per_user
        self.kv = {}
        self.timelines = []

    async def list_known_umos(self):
        return list(self.per_user)

    async def load(self, umo, suffix, default):
        if suffix == "growth_signals":
            return self.per_user.get(umo, [])
        if suffix == "relation_memory":
            return []
        return default

    async def kv_get(self, key, default=None):
        return self.kv.get(key, default)

    async def kv_set(self, key, value):
        self.kv[key] = value

    async def append_timeline(self, umo, kind, payload):
        self.timelines.append((umo, kind, payload))


def _plugin(per_user, *, global_mode=True):
    p = object.__new__(MinglingPlugin)
    p._cfg = CompanionConfig.from_raw({
        "growth_global_mode": global_mode,
        "growth_signal_min_score": 0.55,
        "growth_max_items": 60,
        "growth_token_budget": 3000,
    })
    p._store = FakeStore(per_user)
    p._asked = []
    p._per_user_calls = []

    async def ask(prompt, **kwargs):
        p._asked.append(prompt)
        return "这是一份整体反思。"

    async def per_user(umo):
        p._per_user_calls.append(umo)
        return f"用户 {umo} 的反思"

    p._ask = ask
    p._build_growth_note = per_user
    return p


def test_global_note_generates_one_reflection_for_multiple_users():
    p = _plugin({
        "u1": [
            _sig("我决定下个月辞职，去杭州找工作，虽然有点害怕，但我想试试", 0.9, "u1"),
            _sig("哈哈", 0.05, "u1"),
        ],
        "u2": [
            _sig("这次考试没通过，我有点失望，但明白需要调整计划", 0.85, "u2"),
        ],
    })
    text = asyncio.run(p._build_global_growth_note())
    assert text == "这是一份整体反思。"
    assert len(p._asked) == 1
    note = p._store.kv["mingling.growth.global"]
    assert note["mode"] == "global"
    assert note["stats"]["users"] == 2
    assert note["stats"]["before_count"] == 3
    assert note["stats"]["filtered_count"] == 1
    assert len(note["sources"]) == 2
    assert "u1" not in p._asked[0] and "u2" not in p._asked[0]


def test_growth_batch_switch_global_vs_per_user():
    p = _plugin({"u1": [], "u2": []}, global_mode=True)
    global_calls = []

    async def global_note():
        global_calls.append(1)
        return "g"

    p._build_global_growth_note = global_note
    asyncio.run(p._run_growth_batch())
    assert global_calls == [1]
    assert p._per_user_calls == []

    p2 = _plugin({"u1": [], "u2": []}, global_mode=False)
    asyncio.run(p2._run_growth_batch())
    assert p2._per_user_calls == ["u1", "u2"]

def test_observe_message_writes_growth_signal_end_to_end():
    """有输入 → 打分 → 落库 → 全局打包可读到。"""
    from emotion_trace import EmotionTrace
    from open_loops import OpenLoopManager
    from proactive_v2 import ProactiveEngine
    from relation_memory import RelationMemory
    from relationship import RelationshipManager
    from topic_stack import TopicStack

    class Store(FakeStore):
        def __init__(self):
            super().__init__({"u1": []})
            self.data = {}

        async def remember_umo(self, umo):
            return None

        async def save(self, umo, suffix, value):
            self.data[(umo, suffix)] = value

        async def load(self, umo, suffix, default):
            return self.data.get((umo, suffix), default)

        async def append_timeline(self, umo, kind, payload):
            return None

        async def append_growth_signal(self, umo, signal, max_items=200):
            self.data.setdefault((umo, "growth_signals"), []).append(signal)

    store = Store()
    cfg = CompanionConfig.from_raw({"growth_global_mode": True})
    emotion = EmotionTrace(store)
    relationship = RelationshipManager(store)
    topics = TopicStack(store)
    loops = OpenLoopManager(store, window_days=7)
    memory = RelationMemory(store, cfg)

    p = object.__new__(MinglingPlugin)
    p._cfg = cfg
    p._store = store
    p._emotion = emotion
    p._relationship = relationship
    p._topics = topics
    p._loops = loops
    p._memory = memory
    p._proactive = ProactiveEngine(None, store, cfg, emotion, relationship, loops, topics, memory)

    asyncio.run(p._observe_user_message("u1", "我决定下个月辞职，去杭州找工作，虽然有点害怕，但我想试试"))
    signals = store.data.get(("u1", "growth_signals"), [])
    assert signals, "对话中打分未落库"
    assert signals[-1]["score"] >= 0.55

    async def ask(prompt, **kwargs):
        return "整体反思"

    p._ask = ask
    text = asyncio.run(p._build_global_growth_note())
    assert text == "整体反思"
    assert p._store.kv["mingling.growth.global"]["stats"]["before_count"] == 1


def test_global_falls_back_to_relation_memory_when_no_signals():
    class Store(FakeStore):
        async def load(self, umo, suffix, default):
            if suffix == "relation_memory":
                return [{
                    "topic": "面试",
                    "content": "面试失败后决定复盘并调整计划",
                    "strength": 1.0,
                    "anchor": True,
                    "last_access": 100.0,
                    "created_at": 100.0,
                }]
            return await super().load(umo, suffix, default)

    p = _plugin({"u1": []})
    p._store = Store({"u1": []})
    p._store.kv = p._store.kv if hasattr(p._store, "kv") else {}

    async def ask(prompt, **kwargs):
        return "降级后的整体反思"

    p._ask = ask
    text = asyncio.run(p._build_global_growth_note())
    assert text == "降级后的整体反思"
    stats = p._store.kv["mingling.growth.global"]["stats"]
    assert stats["users_fallback"] == 1
    assert stats["fallback_items"] == 1
    assert stats["before_count"] == 1
