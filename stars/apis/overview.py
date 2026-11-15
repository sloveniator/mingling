"""stars/apis/overview.py -- 概览 / 用户 / 关系网 / 手动触发
==========================================================
对应路由：``/stats`` ``/users`` ``/user`` ``/graph``
``/recent_activity`` ``/trigger`` ``/token_stats``
"""

from __future__ import annotations

import asyncio

from astrbot.api import logger

from ...constants import PLUGIN_NAME
from ...metrics import get_failure_counts
from ._base import (
    MAX_LIST_USERS,
    api_endpoint,
    error_response,
    gather_limited,
    json_response,
    query_str,
    require_json,
    require_module,
    safe_row,
)


class OverviewApiMixin:
    """概览域 Web API。"""

    # ── /stats ────────────────────────────────────────────────
    @api_endpoint("概览统计")
    @require_module("memory", message="记忆模块未初始化")
    @require_module("social", message="社交关系模块未初始化")
    async def api_stats(self):
        all_uids = await self._active_uids()
        uids = all_uids[:MAX_LIST_USERS]
        rels = await gather_limited(
            [lambda u=u: self._get_relationship(u) for u in uids]
        )
        # 记忆总量：受并发上限保护，避免一次性打爆 KV（与关系拉取同量级）
        mem_stats = await gather_limited(
            [lambda u=u: self.memory.stats(u) for u in uids]
        )
        total_memories = sum((s or {}).get("total", 0) for s in mem_stats)
        total_interactions = sum(getattr(r, "interaction_count", 0) for r in rels)
        avg_score = (sum(r.score for r in rels) / len(rels)) if rels else 0.0
        groups = await self.social.list_groups()
        from ..._version import VERSION
        return json_response({
            "plugin": PLUGIN_NAME,
            "version": VERSION,
            "bot_name": self.persona.name if self.persona else "",
            "active_users": len(all_uids),
            "groups": len(groups),
            "relationships_truncated": len(all_uids) > len(uids),
            "total_interactions": total_interactions,
            "total_memories": total_memories,
            "bot_status": self._bot_status(avg_score),
            "failures": get_failure_counts(),
            "relationships": [
                {"uid": r.user_id, "mode": r.mode, "score": round(r.score, 1),
                 "interactions": r.interaction_count}
                for r in rels
            ],
        })

    def _bot_status(self, avg_score: float = 0.0):
        """聚合 Bot 当前状态，供管理面板「今日运势」四个环形进度条使用。"""
        persona = self.persona
        if persona is None:
            return None
        traits = getattr(persona, "traits", []) or []
        return {
            "name": getattr(persona, "name", ""),
            "traits": "、".join(list(traits)[:5]),
            "mood": round(float(getattr(persona, "mood", 62)), 0),
            "energy": round(float(getattr(persona, "energy", 66)), 0),
            "social": round(float(getattr(persona, "social_need", 50)), 0),
            "intimacy": round(float(avg_score), 0),
        }

    # ── /users ────────────────────────────────────────────────
    @api_endpoint("用户列表")
    async def api_users(self):
        all_uids = await self._active_uids()
        uids = all_uids[:MAX_LIST_USERS]

        async def _one(u):
            rel, profile = await asyncio.gather(
                self._get_relationship(u), self._get_profile(u)
            )
            bname = (profile.get("birth", {}) or {}).get("name", "")
            return {
                "uid": u,
                "name": profile.get("name", "") or bname,
                "mode": rel.mode,
                "score": round(rel.score, 1),
                "interactions": rel.interaction_count,
                "last_interaction": rel.last_interaction,
            }

        # 单用户 KV 读失败不该让整页 500：跳过该行、其余照常返回（建议⑤）
        rows = await gather_limited(
            [lambda u=u: safe_row(lambda: _one(u), label="users") for u in uids]
        )
        out = [r for r in rows if r]
        out.sort(key=lambda x: x["score"], reverse=True)
        return json_response({
            "users": out,
            "count": len(out),
            "total": len(all_uids),
            "truncated": len(all_uids) > len(uids),
        })

    # ── /user ─────────────────────────────────────────────────
    @api_endpoint("用户详情")
    @require_module("memory", message="记忆模块未初始化")
    @require_module("social", message="社交关系模块未初始化")
    async def api_user_detail(self):
        uid = query_str("uid")
        if not uid:
            return error_response("缺少 uid 参数")
        rel = await self._get_relationship(uid)
        profile = await self._get_profile(uid)
        recent = await self.memory.recall_recent(uid, n=10)
        dreams = await self.dream.get_dreams(uid, n=5) if self.dream else []
        co = await self.social.co_members(uid)
        return json_response({
            "uid": uid,
            "relationship": rel.to_dict(),
            "profile": profile,
            "recent_memories": [
                {"type": m.type, "content": m.content[:120], "ts": m.created_at}
                for m in recent
            ],
            "recent_dreams": dreams,
            "co_members": co,
        })

    # ── /trigger ──────────────────────────────────────────────
    @require_json("uid")
    async def api_trigger(self, body):
        uid = body.get("uid", "")
        action = body.get("action", "")
        try:
            if action == "dream":
                if not self.dream:
                    return error_response("梦境能力未启用")
                text = await self.dream.nightly_consolidate(uid)
            elif action == "premonition":
                # P3 策划通道：优先暗盘信号（含强度）；无信号时强制生成
                # 弱预感（纯氛围），保证手动触发始终可用
                strength = 1
                cue = None
                try:
                    profile = await self._get_profile(uid)
                    birth = self._birth_from(profile)
                    if birth:
                        sig = await self.diviner.premonition_signal(uid, birth, "")
                        if (sig or {}).get("strength", 0) >= 1:
                            strength = sig["strength"]
                            cue = sig.get("cue")
                except Exception as e:
                    logger.debug(f"[mingbu] 预感信号取暗盘失败(退回纯氛围): {e}")
                text = await self.dream.premonition(
                    uid, cue=cue, strength=strength)
            elif action == "growth":
                if not self.growth:
                    return error_response("成长能力未启用")
                text = await self.growth.reflect(uid)
            else:
                return error_response(f"未知 action: {action}")
            return json_response({"ok": True, "action": action,
                                  "result": (text or "")[:500]})
        except Exception as e:
            logger.warning(f"[mingbu] 手动触发失败: {e}")
            return error_response(f"触发失败: {str(e)[:120]}")

    # ── /graph ────────────────────────────────────────────────
    @api_endpoint("关系网")
    @require_module("social", message="社交关系模块未初始化")
    async def api_graph(self):
        groups = await self.social.list_groups()
        uids = (await self._active_uids())[:MAX_LIST_USERS]
        nodes, seen = [], set()
        for u in uids:
            if u in seen:
                continue
            seen.add(u)
            nodes.append({"id": u, "type": "user"})
        edges = []
        for g in groups:
            gid = "group:" + g["gid"]
            nodes.append({"id": gid, "type": "group", "name": g["name"]})
            for m in g.get("members", []):
                edges.append({"source": gid, "target": m})
        return json_response({"nodes": nodes, "edges": edges, "groups": groups})

    # ── /recent_activity ──────────────────────────────────────
    @api_endpoint("近期互动")
    async def api_recent_activity(self):
        """最近活动 Top 10。

        原实现先取 uids[:10] 再排序——索引顺序与活跃时间无关，
        于是永远只展示固定的那几个用户。这里改为先在受限范围内
        全量取 last_interaction，再排序截断。
        """
        uids = (await self._active_uids())[:MAX_LIST_USERS]

        async def _one(u):
            rel, profile = await asyncio.gather(
                self._get_relationship(u), self._get_profile(u)
            )
            if not rel.last_interaction:
                return None
            return {
                "user_uid": u,
                # 与 api_users 取名口径保持一致：平台昵称 → 命盘用名 → uid。
                # 原实现只读顶层 name 且兜底 u[:4]，"wxid_zhang_wei" 被截成 "wxid"。
                "user_name": (profile.get("name", "")
                              or (profile.get("birth", {}) or {}).get("name", "")
                              or u),
                "type": "chat",
                "timestamp": rel.last_interaction,
            }

        results = await gather_limited(
            [lambda u=u: safe_row(lambda: _one(u), label="recent_activity")
             for u in uids]
        )
        items = [x for x in results if x]
        # timestamp 是 float，默认值必须同类型，否则混排时 TypeError
        items.sort(key=lambda x: x.get("timestamp") or 0, reverse=True)
        return json_response({"items": items[:10]})

    # ── /token_stats ──────────────────────────────────────────
    @api_endpoint("Token 统计")
    @require_module("token_monitor", fallback=lambda: {"error": "Token 监控未初始化"})
    async def api_token_stats(self):
        stats = await self.token_monitor.get_stats()
        return json_response(stats)
