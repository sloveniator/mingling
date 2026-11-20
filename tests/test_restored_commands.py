"""恢复的命理/咨询命令：解析器与命令注册护栏。"""
from command_support import clock_to_shichen, format_birth, parse_birth


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


def test_restored_commands_are_registered():
    from main import MinglingPlugin

    for name in (
        "cmd_paipan", "cmd_bugua", "cmd_tarot", "cmd_my_chart", "cmd_set_profile",
        "cmd_method", "cmd_mood", "cmd_counsel", "cmd_end_counsel", "cmd_health",
        "cmd_growth", "cmd_status",
    ):
        assert hasattr(MinglingPlugin, name), f"缺少命令处理器 {name}"


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
