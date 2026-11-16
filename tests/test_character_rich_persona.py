"""
tests/test_character_rich_persona.py -- 角色人设富字段 + 终端切换通道测试
============================================================================
覆盖：
  - 预设人设富字段（background/catchphrases/taboos/pet_names/sample_dialogue/
    emoji_usage/verbosity）在 CharacterCard / _preset_view / to_persona_prompt 中的落地
  - 旧 KV 中的预设卡片通过 _enrich_preset_data 自动补回富字段（不破坏已编辑的自定义角色）
  - update_character 支持富字段
  - 终端用户切换通道：_list_unlocked_cards / _find_unlocked_character /
    _do_switch_character / _format_character_list（私聊专属，按 编号/名字/关键词 解析）
  - 隐藏角色解锁后可被切换到（修复：hidden 未进入 unlocked 列表导致切换失败）

运行：python -m pytest tests/test_character_rich_persona.py -v
"""
import asyncio
from typing import Tuple

from character_collection import THRESHOLDS, CharacterCollection
from character_pool import (
    HIDDEN_PRESET_ID,
    PRESET_PERSONA_RICH,
    CharacterCard,
    CharacterPool,
    _enrich_preset_data,
    _preset_view,
)
from main import MingbuPlugin


class _Harness:
    """带内存 KV 的 FakePlugin，同时充当 switch 解析方法的 self。"""
    # 以下方法在 _build_harness() 中由 MingbuPlugin 的实现 __get__ 绑定到实例，
    # 仅作类型声明供 mypy 识别（运行时由 __get__ 注入，非本类定义）。
    _list_unlocked_cards: object
    _find_unlocked_character: object
    _do_switch_character: object
    _character_codex_lines: object

    def __init__(self):
        self.kv = {}
        self.character_pool = None
        self.character_collection = None

    async def get_kv_data(self, key, default=None):
        return self.kv.get(key, default)

    async def put_kv_data(self, key, value):
        self.kv[key] = value


async def _build_harness() -> Tuple["_Harness", str]:
    h = _Harness()
    h.character_pool = CharacterPool(h)
    await h.character_pool.initialize()
    h.character_collection = CharacterCollection(h)
    uid = "switch-user"
    await h.character_collection.assign_initial_character(uid)
    # 解锁全部 6 个可见预设
    for thr in THRESHOLDS:
        await h.character_collection.check_recommend(uid, thr)
    # 把 switch 解析方法绑定到 harness 上（供测试以 self 方式调用）
    h._list_unlocked_cards = MingbuPlugin._list_unlocked_cards.__get__(h)
    h._find_unlocked_character = MingbuPlugin._find_unlocked_character.__get__(h)
    h._do_switch_character = MingbuPlugin._do_switch_character.__get__(h)
    # v4.8.120：/角色图鉴 命令与 character_codex_progress LLM 工具共用共享行构建器
    h._character_codex_lines = MingbuPlugin._character_codex_lines.__get__(h)
    return h, uid


# ──────────────────────────────────────────────────────────────
# 人设富字段：CharacterCard
# ──────────────────────────────────────────────────────────────

def test_preset_view_has_rich_fields():
    from character_pool import PRESET_TEMPLATES
    v = _preset_view(PRESET_TEMPLATES[0])
    assert v["background"]
    assert v["catchphrases"]
    assert v["taboos"]
    assert v["pet_names"]
    assert v["sample_dialogue"]
    assert v["emoji_usage"]
    assert v["verbosity"] in ("惜字如金", "适中", "适中偏多", "话痨")


def test_enrich_fills_missing_for_preset():
    data = {"id": "preset-mystery-friend", "is_preset": True, "name": "神秘朋友"}
    e = _enrich_preset_data(data)
    assert e["background"]   # 从 PRESET_PERSONA_RICH 补回
    assert e["catchphrases"]
    assert e["verbosity"]


def test_enrich_explicit_field_wins():
    data = {"id": "preset-mystery-friend", "is_preset": True,
            "background": "自定义背景", "catchphrases": ["自定义口头禅"]}
    e = _enrich_preset_data(data)
    assert e["background"] == "自定义背景"
    assert e["catchphrases"] == ["自定义口头禅"]


def test_enrich_skips_non_preset():
    data = {"id": "custom-1", "is_preset": False, "name": "我的角色"}
    e = _enrich_preset_data(data)
    assert "background" not in e
    assert "catchphrases" not in e


def test_character_card_reads_rich_fields():
    card = CharacterCard({"id": "preset-gentle-sister", "is_preset": True})
    assert card.background
    assert card.catchphrases
    assert card.taboos
    assert card.pet_names
    assert card.sample_dialogue
    assert card.emoji_usage
    assert card.verbosity
    # to_dict 应序列化富字段
    d = card.to_dict()
    assert d["background"] == card.background
    assert d["catchphrases"] == card.catchphrases
    assert "background" in d and "verbosity" in d


def test_to_persona_prompt_includes_rich():
    card = CharacterCard({"id": "preset-sarcastic-friend", "is_preset": True,
                          "name": "毒舌损友"})
    p = card.to_persona_prompt()
    assert "毒舌损友" in p
    assert card.background[:6] in p          # 角色底色落地
    assert "口头禅" in p                      # 口头禅区块
    assert "底线分寸" in p                    # 底线分寸（taboos）
    assert "风格示例" in p                    # 示范对话
    assert "终极约束" in p                    # 人设统一约束


def test_update_character_rich_fields():
    h = _Harness()
    pool = CharacterPool(h)
    asyncio.run(pool.initialize())
    cid = list(pool._characters.keys())[0]
    res = asyncio.run(pool.update_character(cid, {
        "background": "新背景",
        "catchphrases": ["新口头禅A", "新口头禅B"],
        "taboos": ["新底线"],
    }))
    assert res["ok"]
    assert res["character"]["background"] == "新背景"
    assert res["character"]["catchphrases"] == ["新口头禅A", "新口头禅B"]
    assert res["character"]["taboos"] == ["新底线"]
    assert res["character"]["is_preset"] is False


def test_preset_persona_rich_covers_all_7():
    ids = {
        "preset-mystery-friend", "preset-gentle-sister", "preset-sarcastic-friend",
        "preset-rational-advisor", "preset-energetic-sun", "preset-quiet-companion",
        "preset-hidden-master",
    }
    assert set(PRESET_PERSONA_RICH.keys()) == ids


# ──────────────────────────────────────────────────────────────
# 终端用户切换通道（解析逻辑）
# ──────────────────────────────────────────────────────────────

def test_list_unlocked_cards_all_six():
    h, uid = asyncio.run(_build_harness())
    cards, current, hidden_ok = asyncio.run(
        MingbuPlugin._list_unlocked_cards(h, uid))
    assert len(cards) == 6
    assert current is not None
    assert hidden_ok is False


def test_find_by_index():
    h, uid = asyncio.run(_build_harness())
    target = asyncio.run(MingbuPlugin._find_unlocked_character(h, uid, "2"))
    assert target is not None
    # 编号 2 应等于解锁列表第 2 个
    cards, _, _ = asyncio.run(MingbuPlugin._list_unlocked_cards(h, uid))
    assert target["id"] == cards[1]["id"]


def test_find_by_name():
    h, uid = asyncio.run(_build_harness())
    target = asyncio.run(MingbuPlugin._find_unlocked_character(h, uid, "毒舌损友"))
    assert target is not None
    assert target["id"] == "preset-sarcastic-friend"


def test_find_by_keyword():
    h, uid = asyncio.run(_build_harness())
    # 「吐槽」是毒舌损友的兴趣之一
    target = asyncio.run(MingbuPlugin._find_unlocked_character(h, uid, "吐槽"))
    assert target is not None
    assert target["id"] == "preset-sarcastic-friend"


def test_find_no_match_returns_none():
    h, uid = asyncio.run(_build_harness())
    target = asyncio.run(
        MingbuPlugin._find_unlocked_character(h, uid, "完全不存在的角色xyz"))
    assert target is None


def test_do_switch_character():
    h, uid = asyncio.run(_build_harness())
    res = asyncio.run(
        MingbuPlugin._do_switch_character(h, uid, "preset-rational-advisor"))
    assert res["ok"] is True
    assert res["card"]["name"] == "理性分析师"
    # collection 当前角色已更新
    coll = asyncio.run(h.character_collection.get_user_collection(uid))
    assert coll["current"] == "preset-rational-advisor"


def test_format_character_list_contains_names():
    h, uid = asyncio.run(_build_harness())
    cards, current, _ = asyncio.run(MingbuPlugin._list_unlocked_cards(h, uid))
    text = MingbuPlugin._format_character_list(h, cards, current)
    assert "已解锁" in text
    assert "神秘朋友" in text
    assert "/切换角色" in text


def test_hidden_character_switchable_after_unlock():
    """回归：隐藏角色解锁后，switch_to_character 此前因不在 unlocked 列表而失败。"""
    h, uid = asyncio.run(_build_harness())
    # 触发隐藏角色解锁
    from character_collection import HIDDEN_TRIGGER_PHRASE
    res = asyncio.run(h.character_collection.check_hidden_unlock(uid, HIDDEN_TRIGGER_PHRASE))
    assert res is not None and res["ok"] is True

    # 现在应通过解析器切换到隐藏角色
    target = asyncio.run(MingbuPlugin._find_unlocked_character(h, uid, "隐藏主宰"))
    assert target is not None
    assert target["id"] == HIDDEN_PRESET_ID

    do_res = asyncio.run(MingbuPlugin._do_switch_character(h, uid, HIDDEN_PRESET_ID))
    assert do_res["ok"] is True
    coll = asyncio.run(h.character_collection.get_user_collection(uid))
    assert coll["current"] == HIDDEN_PRESET_ID
    assert HIDDEN_PRESET_ID in coll["unlocked"]


# ──────────────────────────────────────────────────────────────
# 角色图鉴（发现）：/角色图鉴 指令
# ──────────────────────────────────────────────────────────────

class _FakeEvent:
    def __init__(self, uid, group=False):
        self._uid = uid
        self.message_obj = type("M", (), {"type": "GroupMessage" if group else "PrivateMessage"})()

    def get_sender_id(self):
        return self._uid

    def plain_result(self, text):
        return text


def _run_codex(h, uid, group=False) -> str:
    # v4.8.120：cmd 依赖共享行构建器，裸 _Harness 也需绑定（否则 AttributeError）
    h._character_codex_lines = MingbuPlugin._character_codex_lines.__get__(h)
    h.cmd_character_codex = MingbuPlugin.cmd_character_codex.__get__(h)
    ev = _FakeEvent(uid, group=group)
    out = []

    async def _go():
        async for r in h.cmd_character_codex(ev):
            out.append(r)

    asyncio.run(_go())
    return "".join(out)


def test_codex_lists_all_seven_and_hidden_locked():
    h, uid = asyncio.run(_build_harness())  # 6 可见全解锁，隐藏未解锁
    text = _run_codex(h, uid)
    for name in ["神秘朋友", "温柔大姐姐", "毒舌损友", "理性分析师",
                 "元气小太阳", "安静陪伴者", "隐藏主宰"]:
        assert name in text, f"图鉴应含角色 {name}"
    assert "隐藏款" in text            # 隐藏角色未解锁时显示「隐藏款」
    assert "已解锁 6/7" in text
    # v4.8.120（①B 纯 AI 引导）：图鉴不再打广告 /切换角色 命令，改口引导自然说名字/「切换」
    assert "或说「切换」" in text


def test_codex_shows_seven_when_hidden_unlocked():
    h, uid = asyncio.run(_build_harness())
    from character_collection import HIDDEN_TRIGGER_PHRASE
    res = asyncio.run(h.character_collection.check_hidden_unlock(uid, HIDDEN_TRIGGER_PHRASE))
    assert res is not None and res["ok"] is True
    text = _run_codex(h, uid)
    assert "已解锁 7/7" in text
    assert "隐藏款" not in text        # 解锁后不再是「隐藏款」


def test_codex_group_refuses():
    h, uid = asyncio.run(_build_harness())
    text = _run_codex(h, uid, group=True)
    assert "私聊" in text


def test_next_unlock_threshold_logic():
    """解锁进度提示应基于已解锁可见角色数推算下一阈值。"""
    # 1 个已解锁 → 下一个阈值 = THRESHOLDS[0]
    coll = {"unlocked": ["preset-mystery-friend"], "hidden_unlocked": False}
    assert CharacterCollection.next_unlock_threshold(coll) == THRESHOLDS[0]
    # 5 个已解锁 → 下一个阈值 = THRESHOLDS[4]
    five = ["preset-mystery-friend", "preset-gentle-sister", "preset-sarcastic-friend",
            "preset-rational-advisor", "preset-energetic-sun"]
    coll5 = {"unlocked": five, "hidden_unlocked": False}
    assert CharacterCollection.next_unlock_threshold(coll5) == THRESHOLDS[4]
    # 全部 6 可见解锁 → None
    six = five + ["preset-quiet-companion"]
    coll6 = {"unlocked": six, "hidden_unlocked": False}
    assert CharacterCollection.next_unlock_threshold(coll6) is None


def test_codex_shows_next_threshold_when_not_all_unlocked():
    h, uid = asyncio.run(_build_harness())  # 6 可见全解锁 → 不应出现阈值提示
    text = _run_codex(h, uid)
    assert "好感度达到" not in text   # 全部解锁时无进度提示

    # 只解锁 1 个时，应出现下一阈值提示
    h2 = _Harness()
    h2.character_pool = CharacterPool(h2)
    asyncio.run(h2.character_pool.initialize())
    h2.character_collection = CharacterCollection(h2)
    uid2 = "codex-progress-user"
    asyncio.run(h2.character_collection.assign_initial_character(uid2))
    text2 = _run_codex(h2, uid2)
    assert "好感度达到" in text2
