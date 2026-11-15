"""stars/apis/collection.py -- 角色收集 / 角色管理
================================================
对应路由：``/collection/progress`` ``/collection/unlock_hidden``
``/collection/all`` ``/collection/switch`` ``/collection/assign``
"""

from __future__ import annotations

from ._base import (
    api_endpoint,
    error_response,
    json_response,
    query_str,
    require_json,
    require_module,
    require_query,
)


async def _get_unlocked_set(plugin, uid: str | None) -> set[str]:
    """取指定用户的已解锁角色 ID 集合；无 uid 时回退到全局角色池。"""
    if uid and plugin.character_collection:
        progress = await plugin.character_collection.get_collection_progress(uid)
        return set(progress.get("unlocked_characters") or [])
    return set()


async def _is_hidden_unlocked(plugin, uid: str | None) -> bool:
    """取指定用户的隐藏角色解锁状态；无 uid 时回退到全局角色池。"""
    if uid and plugin.character_collection:
        progress = await plugin.character_collection.get_collection_progress(uid)
        return bool(progress.get("hidden_unlocked"))
    return plugin.character_pool.is_hidden_unlocked() if plugin.character_pool else False

# 角色池未初始化时 /collection/all 的空响应。7 = 6 个预设 + 1 个隐藏角色，
# 前端进度条用它当分母，返回 0 会出现除零。
_EMPTY_COLLECTION = {
    "characters": [],
    "total_with_hidden": 7,
    "unlocked_count": 0,
    "hidden_unlocked": False,
}


class CollectionApiMixin:
    """角色收集域 Web API。"""

    @staticmethod
    def _preset_affinity(preset: dict) -> int:
        """卡片进度条展示值。

        角色池里并不存在「亲和度」这个量，凭空造一个假百分比没有意义。
        这里改用角色真实的「陪伴」维度（0-100），前端标签同步改为陪伴度。
        """
        dims = preset.get("dimensions") or {}
        try:
            return max(0, min(100, int(float(dims.get("companionship", 0)))))
        except (TypeError, ValueError):
            return 0

    # ── /collection/progress ──────────────────────────────────
    @api_endpoint("角色收集进度")
    @require_query("uid", "缺少 uid 参数")
    @require_module("character_collection",
                    fallback=lambda: {"error": "角色收集模块未初始化"})
    async def api_collection_progress(self):
        uid = query_str("uid")
        progress = await self.character_collection.get_collection_progress(uid)
        return json_response({"uid": uid, **progress})

    # ── /collection/unlock_hidden ─────────────────────────────
    @api_endpoint("解锁隐藏角色")
    @require_query("uid", "缺少 uid 参数")
    @require_module("character_pool", "角色池未初始化")
    async def api_collection_unlock_hidden(self):
        result = await self.character_pool.unlock_hidden_character()
        if result["ok"]:
            return json_response(result)
        return error_response("；".join(result.get("errors", ["解锁失败"])))

    # ── /collection/all ───────────────────────────────────────
    @api_endpoint("角色收集全览")
    @require_module("character_pool", fallback=lambda: dict(_EMPTY_COLLECTION))
    async def api_collection_all(self):
        if not self.character_collection:
            return json_response(dict(_EMPTY_COLLECTION))

        uid = query_str("uid") or None
        # 有 uid 时以每用户的 collection 为解锁状态唯一权威；
        # 无 uid 时回退到全局角色池（兼容旧调用 / 测试）。
        unlocked = await _get_unlocked_set(self, uid)
        hidden_unlocked = await _is_hidden_unlocked(self, uid)
        # 无 uid 时仍按旧逻辑把全局池角色视为已拥有
        owned_fallback = {c.get("id") for c in await self.character_pool.list_characters()}

        presets = await self.character_pool.list_presets()
        hidden = self.character_pool.get_hidden_preset()

        characters = []
        unlocked_count = 0

        for p in presets:
            pid = p.get("id", "")
            is_unlocked = pid in unlocked if uid else pid in owned_fallback
            characters.append({
                "id": pid,
                "name": p.get("name", "???"),
                "description": p.get("description", ""),
                # 模板里的字段叫 avatar，不是 icon；此前取错导致图标全是默认值
                "icon": p.get("avatar") or "🎭",
                # 角色人设富字段（_preset_view 已附带）
                "background": p.get("background", ""),
                "catchphrases": p.get("catchphrases", []),
                "taboos": p.get("taboos", []),
                "pet_names": p.get("pet_names", []),
                "sample_dialogue": p.get("sample_dialogue", []),
                "emoji_usage": p.get("emoji_usage", ""),
                "verbosity": p.get("verbosity", ""),
                "speaking_style": p.get("speaking_style", ""),
                "counseling_style": p.get("counseling_style", ""),
                "unlocked": is_unlocked,
                "hidden": False,
                "affinity": self._preset_affinity(p) if is_unlocked else 0,
            })
            if is_unlocked:
                unlocked_count += 1

        if hidden:
            is_hidden_unlocked = hidden.get("id") in unlocked if uid else hidden_unlocked
            characters.append({
                "id": hidden.get("id", ""),
                "name": hidden.get("name", "???"),
                "description": hidden.get("description", ""),
                "icon": hidden.get("avatar") or "⭐",
                "background": hidden.get("background", ""),
                "catchphrases": hidden.get("catchphrases", []),
                "taboos": hidden.get("taboos", []),
                "pet_names": hidden.get("pet_names", []),
                "sample_dialogue": hidden.get("sample_dialogue", []),
                "emoji_usage": hidden.get("emoji_usage", ""),
                "verbosity": hidden.get("verbosity", ""),
                "speaking_style": hidden.get("speaking_style", ""),
                "counseling_style": hidden.get("counseling_style", ""),
                "unlocked": is_hidden_unlocked,
                "hidden": True,
                "affinity": self._preset_affinity(hidden) if is_hidden_unlocked else 0,
            })
            if is_hidden_unlocked:
                unlocked_count += 1

        return json_response({
            "characters": characters,
            "total_with_hidden": len(characters),
            "unlocked_count": unlocked_count,
            "hidden_unlocked": is_hidden_unlocked if hidden else hidden_unlocked,
        })

    # ── /collection/switch ────────────────────────────────────
    @api_endpoint("切换角色")
    @require_json("uid")
    async def api_collection_switch(self, body):
        uid = body.get("uid", "")
        char_id = body.get("char_id", "")
        if not char_id:
            return error_response("缺少 char_id")
        if not self.character_collection:
            return error_response("角色收集模块未初始化")
        ok = await self.character_collection.switch_to_character(uid, char_id)
        if not ok:
            return error_response("角色未解锁或不存在")
        return json_response({"ok": True, "uid": uid, "char_id": char_id})

    # ── /collection/assign ────────────────────────────────────
    @api_endpoint("分配初始角色")
    @require_json("uid")
    @require_module("character_collection", "角色收集模块未初始化")
    async def api_collection_assign(self, body):
        uid = body.get("uid", "")
        char_id = await self.character_collection.assign_initial_character(uid)
        return json_response({"ok": True, "uid": uid, "char_id": char_id})
