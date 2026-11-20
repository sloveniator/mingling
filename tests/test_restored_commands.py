"""自然语言意图、解析器与内部处理器护栏。"""
from pathlib import Path

from command_support import clock_to_shichen, format_birth, parse_birth
from intent_router import (
    detect_task,
    normalize,
    should_exit_counseling,
    should_start_counseling,
)


def test_parse_birth_full():
    b = parse_birth("1990年5月20日早上8点 男")
    assert b == {"year": 1990, "month": 5, "day": 20, "hour": 8, "gender": "male"}


def test_parse_birth_pm_and_female():
    b = parse_birth("设置命盘：1995-12-01 晚上9点30分，女")
    assert b["year"] == 1995
    assert b["month"] == 12
    assert b["day"] == 1
    assert b["hour"] == 21
    assert b["gender"] == "female"


def test_parse_birth_rejects_missing_date():
    assert parse_birth("我忘了出生时间") is None


def test_clock_to_shichen_boundaries():
    assert clock_to_shichen(23) == 0
    assert clock_to_shichen(0) == 0
    assert clock_to_shichen(1) == 1
    assert clock_to_shichen(12) == 6


def test_format_birth_readable():
    assert "1990年5月20日" in format_birth(
        {"year": 1990, "month": 5, "day": 20, "hour": 8, "gender": "male"}
    )


def test_internal_handlers_exist_without_slash_registration():
    from main import MinglingPlugin

    for name in (
        "cmd_paipan", "cmd_bugua", "cmd_tarot", "cmd_my_chart", "cmd_set_profile",
        "cmd_method", "cmd_mood", "cmd_health", "cmd_growth", "cmd_status",
        "auto_route",
    ):
        assert hasattr(MinglingPlugin, name), f"缺少处理器 {name}"

    source = Path("main.py").read_text(encoding="utf-8")
    assert "filter.command" not in source, "不得再注册斜杠命令"


def test_natural_language_task_intents():
    assert detect_task("帮我看看八字和紫微") == "chart"
    assert detect_task("我最近睡不好，想改善睡眠") == "health"
    assert detect_task("最近老是失眠") == "health"
    assert detect_task("我们关系现在到哪一步了") == "status"
    assert detect_task("帮我抽三张塔罗") == "tarot"
    assert detect_task("回顾一下我们最近的相处") == "growth"


def test_slash_text_is_treated_as_normal_semantics():
    assert normalize("/排盘") == "排盘"
    assert detect_task("/排盘") == "chart"
    assert detect_task("/卜卦 我该不该换工作") == "divination"


def test_counseling_semantic_entry_and_exit():
    assert should_start_counseling("今天真的好难过，想找个人聊聊")
    assert should_start_counseling("压力好大，快撑不住了")
    assert should_start_counseling("心情不好，想找人说说话")
    assert should_exit_counseling("好多了，谢谢你")
    assert should_exit_counseling("不想聊了，换个话题吧")


def test_chat_message_updates_relationship_emotion_and_memory():
    """私聊消息应自动更新关系/情绪/关系记忆，而不是只在 /命灵状态 时才算。"""
    import asyncio

    from config import CompanionConfig
    from emotion_trace import EmotionTrace
    from main import MinglingPlugin
    from open_loops import OpenLoopManager
    from proactive_v2 import ProactiveEngine
    from relation_memory import RelationMemory
    from relationship import RelationshipManager
    from topic_stack import TopicStack

    class FakeStore:
        def __init__(self):
            self.data = {}

        async def load(self, umo, suffix, default):
            return self.data.get((umo, suffix), default)

        async def save(self, umo, suffix, value):
            self.data[(umo, suffix)] = value

        async def remember_umo(self, umo):
            return None

        async def append_timeline(self, umo, kind, payload):
            return None

    store = FakeStore()
    cfg = CompanionConfig(enabled=True)
    emotion = EmotionTrace(store)
    relationship = RelationshipManager(store)
    topics = TopicStack(store)
    loops = OpenLoopManager(store, window_days=7)
    memory = RelationMemory(store, cfg)

    plugin = object.__new__(MinglingPlugin)
    plugin._cfg = cfg
    plugin._store = store
    plugin._emotion = emotion
    plugin._relationship = relationship
    plugin._topics = topics
    plugin._loops = loops
    plugin._memory = memory
    plugin._proactive = ProactiveEngine(
        None, store, cfg, emotion, relationship, loops, topics, memory
    )

    asyncio.run(plugin._observe_user_message("u1", "今天面试很紧张"))

    rel = asyncio.run(relationship.get("u1"))
    state = asyncio.run(emotion.load("u1"))
    memories = asyncio.run(memory._load("u1"))
    opens = asyncio.run(loops.list_open("u1"))

    assert rel.intimacy > 0, "关系亲密度应随聊天增长"
    assert state.tension > 0, "紧张情绪应写入情绪轨迹"
    assert memories, "有情绪峰值的对话应写入关系记忆"
    assert opens, "新话题应进入未闭环列表"


def test_ask_default_uses_astrbot_default_provider():
    """命理/咨询的 AI 生成必须走 AstrBot 默认 provider，而不是插件自有 Key。"""
    import asyncio
    from types import SimpleNamespace

    from llm_default import ask_default

    seen = {}

    class Provider:
        async def text_chat(self, **kwargs):
            seen.update(kwargs)
            return SimpleNamespace(completion_text="默认模型回复")

    class Ctx:
        def get_using_provider(self):
            return Provider()

    assert asyncio.run(ask_default(Ctx(), "解读这段命盘")) == "默认模型回复"
    assert seen["prompt"] == "解读这段命盘"


def test_auto_route_enters_and_exits_counseling_without_commands():
    import asyncio

    from config import CompanionConfig
    from main import MinglingPlugin

    class FakeStore:
        def __init__(self):
            self.data = {}

        async def load(self, umo, suffix, default):
            return self.data.get((umo, suffix), default)

        async def save(self, umo, suffix, value):
            self.data[(umo, suffix)] = value

    class FakeEvent:
        def __init__(self, text):
            self.message_str = text
            self.unified_msg_origin = "u1"
            self.stopped = False

        def is_private_chat(self):
            return True

        def plain_result(self, text):
            return ("plain", text)

        def stop_event(self):
            self.stopped = True

    async def collect(gen):
        return [item async for item in gen]

    plugin = object.__new__(MinglingPlugin)
    plugin._cfg = CompanionConfig(enabled=True)
    plugin._store = FakeStore()
    plugin._last_observe_key = None

    async def noop(*args, **kwargs):
        return None

    plugin._observe_once = noop

    async def fake_task(task, event, text):
        yield ("task", task)

    plugin._run_task = fake_task

    # 自然语言进入具体功能
    ev = FakeEvent("帮我看看八字")
    out = asyncio.run(collect(plugin.auto_route(ev)))
    assert out == [("task", "chart")]
    assert ev.stopped is True

    # 情绪倾诉自动进入开解
    ev = FakeEvent("今天真的好难过，想找个人聊聊")
    out = asyncio.run(collect(plugin.auto_route(ev)))
    assert out and out[0][0] == "plain"
    assert ev.stopped is True
    assert asyncio.run(plugin._store.load("u1", "counseling_active", False)) is True

    # 开解中继续倾诉：不打断，交给默认模型
    ev = FakeEvent("我还是很难受，不知道怎么办")
    out = asyncio.run(collect(plugin.auto_route(ev)))
    assert out == []
    assert ev.stopped is False

    # 语义自然退出
    ev = FakeEvent("好多了，谢谢你")
    out = asyncio.run(collect(plugin.auto_route(ev)))
    assert out and out[0][0] == "plain"
    assert asyncio.run(plugin._store.load("u1", "counseling_active", False)) is False
    assert "停" in out[0][1] or "不客气" in out[0][1]
