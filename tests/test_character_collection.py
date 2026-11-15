"""
tests/test_character_collection.py -- 角色收集玩法测试
=========================================================
覆盖：
  - 用户首次添加时随机分配角色
  - 好感度阈值触发推荐机制（68/64/49/49/49）
  - 逐步解锁所有 6 个角色
  - 集齐 6 位可见角色后，自然语料/旧暗号触发隐藏角色（第 2 层玩法）
  - 收集进度查询
  - 隐藏角色默认不加载

运行：python -m pytest tests/test_character_collection.py -v
"""
import asyncio

from character_collection import (
    HIDDEN_TRIGGER_PHRASE,
    THRESHOLDS,
    VISIBLE_PRESETS,
    CharacterCollection,
)


class FakePlugin:
    """带内存 KV + 可配置 character_pool 的 FakePlugin。"""
    def __init__(self):
        self.kv = {}
        self.character_pool = None

    async def get_kv_data(self, key, default=None):
        return self.kv.get(key, default)

    async def put_kv_data(self, key, value):
        self.kv[key] = value


class FakeCharacterPool:
    """模拟 CharacterPool，支持解锁隐藏角色。"""
    def __init__(self):
        self._hidden_unlocked = False
        self._hidden_char = {"id": "preset-hidden-lord", "name": "隐藏主宰", "avatar": "👑"}

    async def unlock_hidden_character(self):
        if self._hidden_unlocked:
            return {"ok": False, "error": "already_unlocked"}
        self._hidden_unlocked = True
        return {"ok": True, "character": self._hidden_char}

    def is_hidden_unlocked(self):
        return self._hidden_unlocked

    async def get_character(self, char_id):
        from character_pool import HIDDEN_PRESET_ID as hp
        if char_id == hp and self._hidden_unlocked:
            return self._hidden_char
        raise KeyError(char_id)

    async def list_presets(self, include_hidden=False):
        from character_pool import VISIBLE_PRESETS as vp
        result = list(vp)
        if include_hidden and self._hidden_unlocked:
            result.append(self._hidden_char)
        return result

    async def current_character(self):
        return {"id": "preset-mystery-friend", "name": "灵犀"}

    async def switch_character(self, char_id):
        return True


def _make_collection():
    """创建一个带有真实内存存储的 CharacterCollection。"""
    plugin = FakePlugin()
    plugin.character_pool = FakeCharacterPool()
    return CharacterCollection(plugin)


# ------------------------------------------------------------
# 初始分配
# ------------------------------------------------------------

def test_assign_initial_character():
    coll = _make_collection()
    char_id = asyncio.run(coll.assign_initial_character("test-uid"))
    assert char_id is not None
    assert char_id in [t["id"] for t in VISIBLE_PRESETS]


def test_assign_initial_character_not_duplicate():
    coll = _make_collection()
    char1 = asyncio.run(coll.assign_initial_character("uid1"))
    char2 = asyncio.run(coll.assign_initial_character("uid2"))
    assert char1 is not None
    assert char2 is not None


def test_assign_initial_character_already_assigned():
    coll = _make_collection()
    asyncio.run(coll.assign_initial_character("uid"))
    result = asyncio.run(coll.assign_initial_character("uid"))
    assert result is not None


# ------------------------------------------------------------
# 好感度阈值推荐（68/64/49/49/49）
# ------------------------------------------------------------

def test_check_recommend_thresholds():
    coll = _make_collection()
    asyncio.run(coll.assign_initial_character("uid"))

    # 每个阈值都应该推荐一个新角色
    for i, threshold in enumerate(THRESHOLDS):
        result = asyncio.run(coll.check_recommend("uid", threshold))
        assert result is not None, f"阈值 {threshold} 应该触发推荐"
        assert result["ok"] is True
        assert result["threshold"] == threshold
        assert result["unlocked_count"] == i + 2


def test_check_recommend_not_triggered_below_threshold():
    coll = _make_collection()
    asyncio.run(coll.assign_initial_character("uid"))
    # 低于第一个阈值（68）不应推荐
    result = asyncio.run(coll.check_recommend("uid", 67))
    assert result is None


def test_check_recommend_same_score_unlocks_next():
    coll = _make_collection()
    asyncio.run(coll.assign_initial_character("uid"))
    asyncio.run(coll.check_recommend("uid", 68))
    # 递减阈值：68 同时满足 64，应继续解锁下一个
    result = asyncio.run(coll.check_recommend("uid", 68))
    assert result is not None
    assert result["threshold"] == 64


def test_check_recommend_all_unlocked():
    coll = _make_collection()
    asyncio.run(coll.assign_initial_character("uid"))
    # 解锁全部 6 个角色后不应再推荐
    for threshold in THRESHOLDS:
        asyncio.run(coll.check_recommend("uid", threshold))
    result = asyncio.run(coll.check_recommend("uid", 99))
    assert result is None


# ------------------------------------------------------------
# 隐藏角色触发（第 2 层：集齐 6 位可见角色后的收集终点）
# ------------------------------------------------------------

def _unlock_all_visible(coll, uid):
    """分配初始角色并解锁全部 6 位可见角色。"""
    asyncio.run(coll.assign_initial_character(uid))
    for t in THRESHOLDS:
        asyncio.run(coll.check_recommend(uid, t))


def test_check_hidden_unlock_requires_all_visible():
    # 只解锁 1 位可见角色时，打听隐藏角色只当闲聊 → 不解锁
    coll = _make_collection()
    asyncio.run(coll.assign_initial_character("uid"))
    result = asyncio.run(coll.check_hidden_unlock("uid", HIDDEN_TRIGGER_PHRASE))
    assert result is None


def test_check_hidden_unlock_legacy_phrase_after_all_visible():
    # 旧整句暗号仍可触发（历史玩法兼容）
    coll = _make_collection()
    _unlock_all_visible(coll, "uid")
    result = asyncio.run(coll.check_hidden_unlock("uid", HIDDEN_TRIGGER_PHRASE))
    assert result is not None
    assert result["ok"] is True


def test_check_hidden_unlock_natural_phrase_after_all_visible():
    # 自然语料（A 通道）：集齐 6 位后用大白话打听秘密 → 触发
    for phrase in ("你是不是还瞒着我什么", "第七个人是谁", "你是不是藏着谁"):
        coll = _make_collection()
        _unlock_all_visible(coll, "uid")
        result = asyncio.run(coll.check_hidden_unlock("uid", phrase))
        assert result is not None, f"自然语料 {phrase!r} 应触发隐藏解锁"
        assert result["ok"] is True


def test_check_hidden_unlock_not_triggered():
    coll = _make_collection()
    _unlock_all_visible(coll, "uid")
    result = asyncio.run(coll.check_hidden_unlock("uid", "今天天气不错"))
    assert result is None


def test_check_hidden_unlock_already_unlocked():
    coll = _make_collection()
    _unlock_all_visible(coll, "uid")
    asyncio.run(coll.check_hidden_unlock("uid", HIDDEN_TRIGGER_PHRASE))
    # 第二次相同消息不应重复触发
    result = asyncio.run(coll.check_hidden_unlock("uid", HIDDEN_TRIGGER_PHRASE))
    assert result is None


def test_check_hidden_unlock_idempotent_when_pool_already_has_hidden():
    # 多用户：角色池已被其它用户解锁时，本用户集齐 6 位后仍应解锁成功
    coll = _make_collection()
    _unlock_all_visible(coll, "uid")
    coll.plugin.character_pool._hidden_unlocked = True  # 模拟其它用户已解锁
    result = asyncio.run(coll.check_hidden_unlock("uid", "你是不是还瞒着我什么"))
    assert result is not None
    assert result["ok"] is True
    progress = asyncio.run(coll.get_collection_progress("uid"))
    assert progress["hidden_unlocked"] is True


def test_mark_hidden_clue_sent_once():
    # 彩蛋线索一次性标记（B 通道只提示一次）
    coll = _make_collection()
    asyncio.run(coll.assign_initial_character("uid"))
    asyncio.run(coll.mark_hidden_clue_sent("uid"))
    raw = asyncio.run(coll.get_user_collection("uid"))
    assert raw["hidden_clue_sent"] is True


# ------------------------------------------------------------
# 收集进度
# ------------------------------------------------------------

def test_get_collection_progress():
    coll = _make_collection()
    asyncio.run(coll.assign_initial_character("uid"))
    progress = asyncio.run(coll.get_collection_progress("uid"))
    assert progress["unlocked_count"] == 1
    assert progress["total_visible"] == len(VISIBLE_PRESETS)
    assert progress["total_with_hidden"] == len(VISIBLE_PRESETS) + 1
    assert progress["hidden_unlocked"] is False
    assert progress["next_threshold"] == 68


def test_get_collection_progress_after_unlocks():
    coll = _make_collection()
    asyncio.run(coll.assign_initial_character("uid"))
    asyncio.run(coll.check_recommend("uid", 68))
    asyncio.run(coll.check_recommend("uid", 64))
    progress = asyncio.run(coll.get_collection_progress("uid"))
    assert progress["unlocked_count"] == 3
    assert progress["recommend_count"] == 2
    assert progress["next_threshold"] == 49


def test_get_collection_progress_with_hidden():
    coll = _make_collection()
    _unlock_all_visible(coll, "uid")
    asyncio.run(coll.check_hidden_unlock("uid", "你是不是还瞒着我什么"))
    progress = asyncio.run(coll.get_collection_progress("uid"))
    assert progress["hidden_unlocked"] is True
    assert progress["unlocked_count"] == len(VISIBLE_PRESETS) + 1


# ------------------------------------------------------------
# 互动记录
# ------------------------------------------------------------

def test_record_interaction():
    coll = _make_collection()
    asyncio.run(coll.assign_initial_character("uid"))
    progress = asyncio.run(coll.get_collection_progress("uid"))
    assert progress["total_interactions"] == 0

    asyncio.run(coll.record_interaction("uid"))
    asyncio.run(coll.record_interaction("uid"))
    progress = asyncio.run(coll.get_collection_progress("uid"))
    assert progress["total_interactions"] == 2


# ------------------------------------------------------------
# 角色切换
# ------------------------------------------------------------

def test_switch_to_next_character():
    coll = _make_collection()
    asyncio.run(coll.assign_initial_character("uid"))
    asyncio.run(coll.check_recommend("uid", 68))

    # 切换到下一个角色
    next_char = asyncio.run(coll.switch_to_next_character("uid"))
    assert next_char is not None


def test_switch_to_character():
    coll = _make_collection()
    asyncio.run(coll.assign_initial_character("uid"))
    asyncio.run(coll.check_recommend("uid", 68))

    progress = asyncio.run(coll.get_collection_progress("uid"))
    target_char = progress["unlocked_characters"][1]

    result = asyncio.run(coll.switch_to_character("uid", target_char))
    assert result is True


def test_switch_to_character_not_unlocked():
    coll = _make_collection()
    asyncio.run(coll.assign_initial_character("uid"))
    # 使用一个不可能存在的角色 ID
    result = asyncio.run(coll.switch_to_character("uid", "preset-nonexistent"))
    assert result is False


# ------------------------------------------------------------
# 边界情况
# ------------------------------------------------------------

def test_no_initial_assignment():
    coll = _make_collection()
    result = asyncio.run(coll.switch_to_next_character("unknown-uid"))
    assert result is None
