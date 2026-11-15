"""stars/apis/conversation.py -- 对话检视
=======================================
对应路由：``/conversation/list`` ``/learn`` ``/unlearn`` ``/delete``
``/stats`` ``/knowledge`` ``/weighted`` ``/extract`` ``/reweight``

九个 handler 里有五个只差一行引擎调用（learn/unlearn/delete/extract/
reweight），拆分前每个都要重抄「取 body → 校验 log_id → 判模块 → 调用 →
判 ok → 响应」。现在前四步交给装饰器。
"""

from __future__ import annotations

from ._base import (
    api_endpoint,
    error_response,
    json_response,
    query_float,
    query_int,
    query_str,
    require_json,
    require_module,
    require_query,
)

_MODULE = "conversation_inspector"
_NOT_READY = "对话检视模块未初始化"

_EMPTY_STATS = {
    "total_users": 0,
    "total_logs": 0,
    "total_learned": 0,
    "user_stats": [],
}


class ConversationApiMixin:
    """对话检视域 Web API。"""

    # ── /conversation/list ────────────────────────────────────
    @api_endpoint("对话列表")
    @require_module(_MODULE, fallback=lambda: {"total": 0, "items": []})
    async def api_conversation_list(self):
        result = await self.conversation_inspector.list_conversations(
            uid=query_str("uid") or None,
            gid=query_str("gid") or None,
            limit=query_int("limit", 50),
            offset=query_int("offset", 0),
            search=query_str("search"),
            msg_type=query_str("msg_type") or None,
            learned_only=query_str("learned_only", "false").lower() == "true",
        )
        return json_response(result)

    # ── /conversation/learn ───────────────────────────────────
    @api_endpoint("对话学习标注")
    @require_json("log_id")
    @require_module(_MODULE, _NOT_READY)
    async def api_conversation_learn(self, body):
        log_id = body.get("log_id", "")
        ok = await self.conversation_inspector.learn_from_conversation(
            log_id,
            notes=body.get("notes", ""),
            tags=body.get("tags", []),
            importance=float(body.get("importance", 0.5)),
        )
        if not ok:
            return error_response("对话记录不存在")
        return json_response({"ok": True, "log_id": log_id})

    # ── /conversation/unlearn ─────────────────────────────────
    @api_endpoint("取消对话学习")
    @require_json("log_id")
    @require_module(_MODULE, _NOT_READY)
    async def api_conversation_unlearn(self, body):
        log_id = body.get("log_id", "")
        ok = await self.conversation_inspector.unlearn(log_id)
        if not ok:
            return error_response("对话记录不存在")
        return json_response({"ok": True, "log_id": log_id})

    # ── /conversation/delete ──────────────────────────────────
    @api_endpoint("删除对话记录")
    @require_json("log_id")
    @require_module(_MODULE, _NOT_READY)
    async def api_conversation_delete(self, body):
        log_id = body.get("log_id", "")
        ok = await self.conversation_inspector.delete_conversation(log_id)
        if not ok:
            return error_response("对话记录不存在")
        return json_response({"ok": True, "log_id": log_id})

    # ── /conversation/stats ───────────────────────────────────
    @api_endpoint("对话统计")
    @require_module(_MODULE, fallback=lambda: dict(_EMPTY_STATS))
    async def api_conversation_stats(self):
        stats = await self.conversation_inspector.get_stats()
        return json_response(stats)

    # ── /conversation/knowledge ───────────────────────────────
    @api_endpoint("已学知识")
    @require_module(_MODULE, fallback=lambda: {"items": []})
    async def api_conversation_knowledge(self):
        items = await self.conversation_inspector.get_learned_knowledge(
            uid=query_str("uid") or None, limit=query_int("limit", 50)
        )
        return json_response({"items": items})

    # ── /conversation/weighted ────────────────────────────────
    @api_endpoint("高权重对话")
    @require_query("uid")
    @require_module(_MODULE, fallback=lambda: {"items": []})
    async def api_conversation_weighted(self):
        # 默认跟随引擎的实例阈值（受配置项 weight_threshold 控制），
        # 而不是写死 0.45——否则面板调高阈值后这里仍按旧值过滤。
        min_weight = query_float(
            "min_weight", self.conversation_inspector.weight_threshold
        )
        items = await self.conversation_inspector.get_weighted_conversations(
            uid=query_str("uid"),
            min_weight=min_weight,
            limit=query_int("limit", 50),
        )
        return json_response({"items": items, "min_weight": min_weight})

    # ── /conversation/extract ─────────────────────────────────
    @api_endpoint("批量入记忆")
    @require_json("uid")
    @require_module(_MODULE, _NOT_READY)
    async def api_conversation_extract(self, body):
        uid = body.get("uid", "")
        try:
            top_n = int(body.get("top_n", 5))
        except (TypeError, ValueError):
            top_n = 5
        count = await self.conversation_inspector.extract_high_weight_to_memory(
            uid=uid, top_n=top_n
        )
        return json_response({"ok": True, "extracted": count, "uid": uid})

    # ── /conversation/reweight ────────────────────────────────
    @api_endpoint("重算权重")
    @require_json("uid")
    @require_module(_MODULE, _NOT_READY)
    async def api_conversation_reweight(self, body):
        uid = body.get("uid", "")
        count = await self.conversation_inspector.reweight_all(uid=uid)
        return json_response({"ok": True, "reweighted": count, "uid": uid})
