"""
social.py -- 群组节点 & 朋友关系网（多平台兼容）
==============================================
仅依赖 AstrBot 统一抽象（group_id / sender_id），不写任何平台特化逻辑，
因此 QQ / Telegram / 微信 / Discord 等皆可工作。

数据模型：
  - 群节点  mingbu.group:{gid}      -> {name, members:[uid], last_active, created}
  - 用户-群索引 mingbu.user_groups:{uid} -> [gid]

关系网语义（v2 · 神秘朋友）：
  bot 不直接"知道"谁是谁的现实身份，但通过共同群聊能感知
  "你和某些人在同一个圈子里"，并以朦胧方式自然流露（绝不点名）。
"""
from __future__ import annotations

from typing import Dict, List

from astrbot.api import logger

from .bj_time import now_bj
from .kv_store import KVStoreBase

KV_GROUP = "mingbu.group"
KV_USER_GROUPS = "mingbu.user_groups"


class SocialGraph(KVStoreBase):
    """群组节点 + 共同成员关系网。所有存储走 plugin 的 KV。"""

    def __init__(self, plugin):
        super().__init__(plugin, "")

    # ---- 底层 KV 助手 ----
    async def _get_group(self, gid: str) -> dict:
        return await self._load_json(f"{KV_GROUP}:{gid}", default={})

    async def _save_group(self, gid: str, node: dict):
        await self._save_json(f"{KV_GROUP}:{gid}", node)

    async def _user_groups(self, uid: str) -> List[str]:
        return await self._load_json(f"{KV_USER_GROUPS}:{uid}", default=[])

    async def _save_user_groups(self, uid: str, gids: List[str]):
        await self._save_json(f"{KV_USER_GROUPS}:{uid}", gids)

    # ---- 写入：群消息时调用 ----
    async def touch_group(self, gid: str, uid: str, name: str = ""):
        """记录一条群活动：确保群节点存在、把 uid 加入成员、更新用户-群索引。"""
        if not gid or not uid:
            return
        node = await self._get_group(gid)
        node.setdefault("name", name or gid)
        if name:
            node["name"] = name
        members = node.get("members", [])
        if uid not in members:
            members.append(uid)
        node["members"] = members
        node["last_active"] = now_bj().isoformat()
        node.setdefault("created", now_bj().isoformat())
        await self._save_group(gid, node)

        ugs = await self._user_groups(uid)
        if gid not in ugs:
            ugs.append(gid)
            await self._save_user_groups(uid, ugs)

    # ---- 查询 ----
    async def get_group(self, gid: str) -> dict:
        return await self._get_group(gid)

    async def list_groups(self) -> List[dict]:
        out = []
        try:
            uids = await self.plugin._active_uids()
        except Exception:
            uids = []
        seen = set()
        for uid in uids:
            for gid in await self._user_groups(uid):
                if gid in seen:
                    continue
                seen.add(gid)
                node = await self._get_group(gid)
                if node:
                    out.append({
                        "gid": gid,
                        "name": node.get("name", gid),
                        "members": node.get("members", []),
                        "member_count": len(node.get("members", [])),
                        "last_active": node.get("last_active", ""),
                    })
        return out

    async def groups_of(self, uid: str) -> List[str]:
        return await self._user_groups(uid)

    async def co_members(self, uid: str) -> List[str]:
        """与 uid 同处至少一个群的其他用户（去重）。"""
        mine = set(await self._user_groups(uid))
        others: Dict[str, int] = {}
        for gid in mine:
            node = await self._get_group(gid)
            for m in node.get("members", []):
                if m != uid:
                    others[m] = others.get(m, 0) + 1
        return list(others.keys())

    async def shared_groups(self, uid: str, other: str) -> List[str]:
        """uid 与 other 共同所在的群。"""
        a = set(await self._user_groups(uid))
        b = set(await self._user_groups(other))
        return list(a & b)

    # ---- 朦胧关系网提示（注入人格块，不点名） ----
    async def relation_hint(self, uid: str) -> str:
        """为私聊人格块产出一句朦胧的'关系网'提示，绝不点出具体身份。"""
        try:
            groups = await self.groups_of(uid)
            co = await self.co_members(uid)
            if not groups and not co:
                return ""
            if groups and co:
                return (
                    "你和一些人处在同一个圈子里，有些事我是从那儿隐隐听来的——"
                    "但我不会点破是谁，你心里有数就好。"
                )
            if co:
                return "你身边似乎也有别的、和我有点牵连的人，这个我就不多嘴了。"
        except Exception as e:
            logger.debug(f"[mingbu.social] 关系网提示生成失败(忽略): {e}")
        return ""
