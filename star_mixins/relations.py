"""
star_mixins/relations.py -- 用户关系读写与活跃用户索引维护
===================================================
用户关系读写与活跃用户索引维护。

``_remember_user`` 位于热路径（每条消息都会走到），命中内存集合时零 IO。

本模块由 ``MingbuStar`` 混入。方法体内的 ``self.<engine>`` 访问依赖
``MingbuStar.__getattr__`` 代理到 ``MingbuCore``，因此 mixin 本身不 import
``core``，也就不产生循环依赖。
"""
import json
import time
from typing import List

from astrbot.api import logger

from ..bond import Bond
from ..constants import KV_USERS
from ..persona import Relationship

# 活跃用户索引的内存缓存 TTL：超过该时长则重新从 KV 拉取一次，
# 以消解不同 Star 实例各自维护一份 _known_uids 导致的跨实例滞后
# （例如主 Star 新增用户、管理面板 Star 的缓存未刷新）。
_KNOWN_UIDS_TTL = 60


class RelationshipMixin:
    """用户关系读写与活跃用户索引维护。"""

    async def _get_relationship(self, uid: str) -> Relationship:
        if uid in self._rel_cache:
            return self._rel_cache[uid]
        raw = await self.get_kv_data(f"mingbu.rel:{uid}", None)
        rel = Relationship.from_dict(json.loads(raw)) if raw else Relationship(uid)
        # 简单边界：缓存过多时清空旧缓存（关系加载很便宜，无需复杂 LRU）
        if len(self._rel_cache) >= self._max_rel_cache:
            self._rel_cache.clear()
        self._rel_cache[uid] = rel
        return rel

    async def _save_relationship(self, rel: Relationship):
        self._rel_cache[rel.user_id] = rel
        try:
            await self.put_kv_data(f"mingbu.rel:{rel.user_id}",
                              json.dumps(rel.to_dict(), ensure_ascii=False))
        except Exception as e:
            logger.warning(f"[mingbu] 关系保存失败(已忽略): {e}")

    async def _get_bond(self, uid: str, char_id: str) -> Bond:
        """读取用户与某角色的羁绊（每用户 × 每角色一份）。"""
        cache_key = f"{uid}:{char_id}"
        if not hasattr(self, "_bond_cache"):
            self._bond_cache = {}
        if cache_key in self._bond_cache:
            return self._bond_cache[cache_key]
        raw = await self.get_kv_data(f"mingbu.bond:{cache_key}", None)
        bond = Bond.from_dict(json.loads(raw)) if raw else Bond(uid, char_id)
        if len(self._bond_cache) >= 500:
            self._bond_cache.clear()
        self._bond_cache[cache_key] = bond
        return bond

    async def _save_bond(self, bond: Bond):
        cache_key = f"{bond.user_id}:{bond.char_id}"
        if not hasattr(self, "_bond_cache"):
            self._bond_cache = {}
        self._bond_cache[cache_key] = bond
        try:
            await self.put_kv_data(f"mingbu.bond:{cache_key}",
                                   json.dumps(bond.to_dict(), ensure_ascii=False))
        except Exception as e:
            logger.warning(f"[mingbu] 羁绊保存失败(已忽略): {e}")

    async def _remember_user(self, uid: str):
        """把 uid 加入活跃用户索引（供后台任务遍历）。

        热路径：每条消息都会走到。命中内存集合时零 IO，只有真正出现新用户
        才回写 KV。此前每条消息都要读一次 KV 并对整个列表做 O(N) 成员判断。
        """
        if not uid:
            return
        try:
            if self._known_uids is None:
                raw = await self.get_kv_data(KV_USERS, None)
                self._known_uids = set(json.loads(raw) if raw else [])
                self._known_uids_ts = time.monotonic()
            if uid in self._known_uids:
                return
            self._known_uids.add(uid)
            await self.put_kv_data(
                KV_USERS, json.dumps(sorted(self._known_uids), ensure_ascii=False)
            )
            self._known_uids_ts = time.monotonic()
        except Exception as e:
            # 回写失败要撤销内存标记，否则该用户永远不会再被尝试写入
            if self._known_uids is not None:
                self._known_uids.discard(uid)
            logger.debug(f"[mingbu] 更新活跃用户索引失败(已忽略): {e}")

    def _filter_blocklist(self, uids: List[str]) -> List[str]:
        """批量任务用户黑名单过滤（batch_uid_blocklist，逗号分隔，支持 * 通配）。

        2026-09-22（P0）：测试/压测账号（flake_*/tester*/col_*/iso_* 等 16 个）
        会永久留在用户索引里，每晚各白耗一次 dream+growth LLM 调用（约占每夜
        内部调用一半）。这里按配置过滤参与批量任务的用户，不删原始数据。
        """
        try:
            cfg = self._config or {}
        except Exception:
            cfg = {}
        raw = str(cfg.get("batch_uid_blocklist", "") or "").replace("，", ",")
        patterns = [p.strip() for p in raw.split(",") if p.strip()]
        if not patterns:
            return uids
        import fnmatch

        kept = [
            u for u in uids
            if not any(fnmatch.fnmatchcase(u, p) for p in patterns)
        ]
        if len(kept) != len(uids):
            logger.info(
                f"[mingbu] 批量任务黑名单过滤：{len(uids)} → {len(kept)} 个用户"
            )
        return kept

    async def _active_uids(self) -> List[str]:
        try:
            if self._known_uids is not None and (
                time.monotonic() - getattr(self, "_known_uids_ts", 0.0)
            ) < _KNOWN_UIDS_TTL:
                return self._filter_blocklist(sorted(self._known_uids))
            raw = await self.get_kv_data(KV_USERS, None)
            uids = json.loads(raw) if raw else []
            uids = self._filter_blocklist(uids)
            self._known_uids = set(uids)
            self._known_uids_ts = time.monotonic()
            return uids
        except Exception as e:
            logger.debug(f"[mingbu] 读取活跃用户失败(已忽略): {e}")
            return []
