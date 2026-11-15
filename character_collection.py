"""
character_collection.py -- 角色收集玩法
======================================
用户首次添加时随机分配 6 个 AI 中的一个。
当好感度达到阈值后，AI 推荐下一个角色，逐步解锁全部 6 个。
当用户触发特定台词时，解锁隐藏款第七个角色。
"""
import random
from typing import Optional

from .character_pool import HIDDEN_PRESET_ID, VISIBLE_PRESETS

KV_COLLECTION = "mingbu.collection:{uid}"

THRESHOLDS = [68, 64, 49, 49, 49]

# ---------------------------------------------------------------------------
# 隐藏角色「第七人」触发（角色收集 UX 第 2 层，2026-09-07）
# ---------------------------------------------------------------------------
# 之前要求用户背出开发者私设台词「六大魔王有 7 个人是很合理的一件事。」，
# 用户根本说不出 → 等于开发者自嗨。第 2 层把隐藏款定位成「集齐 6 位可见
# 角色之后的收集终点」：
#   A 通道（自然语料）：集齐 6 位后，用自然语言打听第七个/隐藏款/秘密，
#       即可触发解锁（子串匹配，先归一化去空白与标点再比较）；
#   B 通道（彩蛋线索）：第 6 位解锁时直发一条彩蛋线索，引导用户问出触发句。
# 旧整句暗号保留在词组表首位，兼容历史玩法与测试。
HIDDEN_TRIGGER_PHRASES = [
    "六大魔王有7个人是很合理的一件事",          # 旧彩蛋暗号（历史保留）
    "第七个人", "第七个角色", "第七个", "第七人", "第七位",   # 猜第七个
    "隐藏角色", "隐藏款", "隐藏的那一位",        # 找隐藏
    "彩蛋", "还有隐藏", "是不是还藏",
    "藏着一个朋友", "藏了个朋友", "藏着一个", "藏着一个人",
    "藏着谁", "还藏着谁", "藏了谁",
    "还有别的身份", "是不是还有别的身份",
    "有事瞒着我", "瞒着我", "瞒了我", "瞒了我什么",
    "还有什么秘密", "还有秘密", "你的秘密", "秘密是什么",
]

# 旧常量保留为历史整句暗号（带空格写法），避免外部按名字引用时断裂。
HIDDEN_TRIGGER_PHRASE = "六大魔王有 7 个人是很合理的一件事。"

# 归一化时跳过的常见中英文标点（空格已由 str.isspace 覆盖）。
_HIDDEN_PHRASE_SKIP = set(
    "，。！？、；：（）()【】《》,.?!;:<>…—~-·"
    "“”‘’\"'"
)


def _norm_text(s: str) -> str:
    """去除空白与常见标点并转小写，供隐藏触发词组做子串匹配。"""
    return "".join(
        ch for ch in (s or "") if not ch.isspace() and ch not in _HIDDEN_PHRASE_SKIP
    ).lower()


def visible_unlocked_count(coll: dict) -> int:
    """coll 中已解锁的可见角色数量（unlocked 可能含隐藏 id，须过滤）。"""
    return sum(1 for t in VISIBLE_PRESETS if t["id"] in coll.get("unlocked", []))


class CharacterCollection:
    def __init__(self, plugin):
        self.plugin = plugin

    async def get_user_collection(self, uid: str) -> dict:
        key = KV_COLLECTION.format(uid=uid)
        raw = await self.plugin.get_kv_data(key, None)
        if raw:
            import json
            data = json.loads(raw) if isinstance(raw, str) else raw
        else:
            data = {}
        return {
            "unlocked": data.get("unlocked", []),
            "current": data.get("current", None),
            "last_recommend_score": data.get("last_recommend_score", 0.0),
            "last_recommend_index": data.get("last_recommend_index", 0),
            "recommend_count": data.get("recommend_count", 0),
            "total_interactions": data.get("total_interactions", 0),
            "hidden_unlocked": data.get("hidden_unlocked", False),
            "hidden_triggered": data.get("hidden_triggered", False),
            "hidden_clue_sent": data.get("hidden_clue_sent", False),
        }

    async def _save_collection(self, uid: str, coll: dict):
        key = KV_COLLECTION.format(uid=uid)
        import json
        await self.plugin.put_kv_data(key, json.dumps(coll, ensure_ascii=False))

    async def assign_initial_character(self, uid: str) -> str:
        coll = await self.get_user_collection(uid)
        if coll["unlocked"]:
            return coll["current"]
        initial = random.choice(VISIBLE_PRESETS)
        coll["unlocked"] = [initial["id"]]
        coll["current"] = initial["id"]
        await self._save_collection(uid, coll)
        return initial["id"]

    async def check_recommend(self, uid: str, current_score: float) -> Optional[dict]:
        coll = await self.get_user_collection(uid)
        if len(coll["unlocked"]) >= len(VISIBLE_PRESETS):
            return None
        recommend_idx = len(coll["unlocked"])
        if recommend_idx > len(THRESHOLDS):
            return None
        threshold = THRESHOLDS[recommend_idx - 1]
        # 使用 last_recommend_index 避免递减阈值导致的重复/跳过问题
        if current_score >= threshold and recommend_idx > coll["last_recommend_index"]:
            available = [t["id"] for t in VISIBLE_PRESETS if t["id"] not in coll["unlocked"]]
            if available:
                new_char_id = random.choice(available)
                coll["unlocked"].append(new_char_id)
                coll["last_recommend_score"] = current_score
                coll["last_recommend_index"] = recommend_idx
                coll["recommend_count"] += 1
                await self._save_collection(uid, coll)
                char_info = next((t for t in VISIBLE_PRESETS if t["id"] == new_char_id), {})
                return {
                    "ok": True,
                    "character_id": new_char_id,
                    "character_name": char_info.get("name", ""),
                    "character_avatar": char_info.get("avatar", "👤"),
                    "threshold": threshold,
                    "unlocked_count": len(coll["unlocked"]),
                    "total_available": len(VISIBLE_PRESETS),
                }
        return None

    async def check_hidden_unlock(self, uid: str, message: str) -> Optional[dict]:
        """隐藏角色解锁判定（第 2 层玩法）。

        隐藏款 = 集齐 6 位可见角色后的收集终点：
        1) 未集齐 6 位可见角色前，任何「打听隐藏款/第七个」都只当普通闲聊；
        2) 集齐后，消息命中 HIDDEN_TRIGGER_PHRASES 中任一词（归一化子串）
           即触发——用户可说人话，不用背开发者私设台词；
        3) 多用户/并发场景下角色池可能已被他人解锁：只要角色已在池中
           （is_hidden_unlocked），对本用户同样计为解锁成功（幂等授予）。
        """
        coll = await self.get_user_collection(uid)
        if coll["hidden_unlocked"] or coll["hidden_triggered"]:
            return None
        if visible_unlocked_count(coll) < len(VISIBLE_PRESETS):
            return None
        norm = _norm_text(message)
        if not any(p in norm for p in HIDDEN_TRIGGER_PHRASES):
            return None
        coll["hidden_triggered"] = True
        await self._save_collection(uid, coll)
        if hasattr(self.plugin, "character_pool") and self.plugin.character_pool:
            pool = self.plugin.character_pool
            character = None
            try:
                result = await pool.unlock_hidden_character()
                if result.get("ok"):
                    character = result.get("character")
            except Exception:
                character = None
            # 角色已被其它用户先解锁 → 对本用户幂等授予（角色卡已在池中可取）。
            if character is None:
                try:
                    already = bool(pool.is_hidden_unlocked())
                except Exception:
                    already = False
                if already:
                    try:
                        character = await pool.get_character(HIDDEN_PRESET_ID)
                    except Exception:
                        character = None
            if character is not None:
                coll["hidden_unlocked"] = True
                await self._save_collection(uid, coll)
                name = character.get("name", "") if isinstance(character, dict) else ""
                return {
                    "ok": True,
                    "character": character,
                    "message": f"你发现了一个隐藏角色「{name}」！传说中的第七人出现了……",
                }
        return None

    async def mark_hidden_clue_sent(self, uid: str):
        """记录彩蛋线索已直发（B 通道只提示一次，避免每轮重试刷屏）。"""
        coll = await self.get_user_collection(uid)
        coll["hidden_clue_sent"] = True
        await self._save_collection(uid, coll)

    async def record_interaction(self, uid: str):
        coll = await self.get_user_collection(uid)
        coll["total_interactions"] += 1
        await self._save_collection(uid, coll)

    async def switch_to_next_character(self, uid: str) -> Optional[str]:
        coll = await self.get_user_collection(uid)
        unlocked = coll["unlocked"]
        if not unlocked:
            return None
        current = coll["current"]
        if current in unlocked:
            idx = unlocked.index(current)
            next_idx = (idx + 1) % len(unlocked)
        else:
            next_idx = 0
        coll["current"] = unlocked[next_idx]
        await self._save_collection(uid, coll)
        return unlocked[next_idx]

    async def switch_to_character(self, uid: str, char_id: str) -> bool:
        coll = await self.get_user_collection(uid)
        if char_id not in coll["unlocked"]:
            # 隐藏角色未显式进入 unlocked 列表，仅在 hidden_unlocked 时允许切换
            if char_id == HIDDEN_PRESET_ID and coll.get("hidden_unlocked"):
                coll["unlocked"] = list(coll["unlocked"]) + [char_id]
            else:
                return False
        coll["current"] = char_id
        await self._save_collection(uid, coll)
        return True

    @staticmethod
    def next_unlock_threshold(coll: dict) -> Optional[int]:
        """解锁下一个可见角色所需的羁绊阈值；已全部解锁则返回 None。

        阈值取自模块级 THRESHOLDS，按「已解锁可见角色数」推算下一个。
        驱动解锁的 score 是当前角色羁绊亲密度（见 hooks/character.py）。
        """
        visible_unlocked = sum(
            1 for t in VISIBLE_PRESETS if t["id"] in coll.get("unlocked", [])
        )
        if visible_unlocked >= len(VISIBLE_PRESETS):
            return None
        idx = visible_unlocked - 1
        if 0 <= idx < len(THRESHOLDS):
            return THRESHOLDS[idx]
        return None

    async def get_collection_progress(self, uid: str) -> dict:
        coll = await self.get_user_collection(uid)
        next_threshold = self.next_unlock_threshold(coll)
        return {
            "unlocked_count": visible_unlocked_count(coll)
            + (1 if coll["hidden_unlocked"] else 0),
            "total_visible": len(VISIBLE_PRESETS),
            "total_with_hidden": len(VISIBLE_PRESETS) + 1,
            "hidden_unlocked": coll["hidden_unlocked"],
            "recommend_count": coll["recommend_count"],
            "total_interactions": coll["total_interactions"],
            "next_threshold": next_threshold,
            "unlocked_characters": coll["unlocked"],
            "current_character": coll["current"],
        }
