"""
tests/test_character_bond_integration.py -- 角色人设注入 + 羁绊注入的集成验证
=============================================================================
验证「角色取代人格」与「羁绊按角色分开」两条主链路：
1. _build_persona_block 私聊时注入当前角色人设（角色名）+ 当前角色羁绊（称号）。
2. CharacterHook 从消息检测羁绊事件并触发当前角色羁绊 nudge。
"""
import asyncio

from hooks.character import CharacterHook
from persona import PersonaState, Relationship
from tests.test_admin_api import build_star


def _fake_memory():
    class M:
        async def recall_important(self, uid, n=4, min_weight=0.35,
                                   max_scope=None):
            return []
        async def recall_emotional(self, uid, n=3, max_scope=None):
            return []
    return M()


def _fake_social():
    class S:
        async def relation_hint(self, uid):
            return ""
    return S()


def _setup_star():
    from character_collection import CharacterCollection
    from character_pool import CharacterPool

    s = build_star()
    s.persona = PersonaState(name="灵犀")
    s.character_pool = CharacterPool(s)
    asyncio.run(s.character_pool.initialize())
    s.character_collection = CharacterCollection(s)
    s.memory = _fake_memory()
    s.social = _fake_social()
    s._config = {
        "worldview_enabled": False,
        "embedding_enabled": False,
        "rerank_enabled": False,
    }
    s._bond_cache = {}
    return s


def test_build_persona_block_injects_character_and_bond():
    s = _setup_star()

    # 分配初始角色（随机 6 选 1），拿到角色名
    char_id = asyncio.run(s.character_collection.assign_initial_character("u1"))
    char = asyncio.run(s.character_pool.get_character(char_id))
    char_name = char["name"]

    rel = Relationship("u1")
    out = asyncio.run(s._build_persona_block("u1", rel, in_group=False))

    assert char_name in out, f"应注入当前角色名「{char_name}」"
    assert "羁绊" in out, "应注入羁绊块"
    assert "初识" in out, "初始羁绊称号应为「初识」"


def test_build_persona_block_group_falls_back_to_fixed_persona():
    """群聊不做 per-user 角色注入，保持固定人格。"""
    s = _setup_star()
    rel = Relationship("u1")
    out = asyncio.run(s._build_persona_block("u1", rel, in_group=True))
    assert "灵犀" in out, "群聊应回退到固定「灵犀」人格"
    assert "羁绊" not in out, "群聊不应注入羁绊块"


def test_character_hook_detects_bond_event_and_nudges():
    s = _setup_star()
    char_id = asyncio.run(s.character_collection.assign_initial_character("u1"))

    class _Req:
        def __init__(self):
            self.extra_user_content_parts = []

    class _Event:
        def get_sender_id(self):
            return "u1"

    hook = CharacterHook()
    req = _Req()

    # 亲密事件：应触发当前角色羁绊 nudge（score 从 10 → 15）
    asyncio.run(hook.process(s, _Event(), req, is_private=True, message_text="爱你，有你真好"))
    bond = asyncio.run(s._get_bond("u1", char_id))
    assert bond.score >= 15.0
    assert bond.intimacy_moments == 1

    # 想静静事件：应进入拒绝态
    asyncio.run(hook.process(s, _Event(), _Req(), is_private=True, message_text="别理我，我想静静"))
    bond = asyncio.run(s._get_bond("u1", char_id))
    assert bond.mode == "refusing"


def test_persona_switch_off_returns_empty_block_private():
    """角色收集总开关关闭：私聊不注入任何人格/羁绊/记忆块 → AstrBot 配置人格接管。"""
    s = _setup_star()
    s._config["character_collection_enabled"] = False
    rel = Relationship("u1")
    out = asyncio.run(s._build_persona_block("u1", rel, in_group=False))
    assert out == "", "关闭角色收集后人格块应整体不注入"


def test_persona_switch_off_returns_empty_block_group():
    """角色收集总开关关闭：群聊同样不注入固定「灵犀」人格块。"""
    s = _setup_star()
    s._config["character_collection_enabled"] = False
    rel = Relationship("u1")
    out = asyncio.run(s._build_persona_block("u1", rel, in_group=True))
    assert out == "", "关闭角色收集后群聊人格块也应整体不注入"
