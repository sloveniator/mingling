"""stars/apis/slang.py -- 群黑话管理
==================================
对应路由：``/slang/groups`` ``/slang/list`` ``/slang/update`` ``/slang/delete``
"""

from __future__ import annotations

from ._base import (
    api_endpoint,
    error_response,
    json_response,
    query_str,
    require_module,
    require_query,
    with_json,
)


def _slang_key(body: dict):
    """从请求体解析条目键：显式 key 优先，否则由 gid+word 合成。

    update / delete 两个 handler 拆分前各写了一份同样的九行逻辑。
    """
    key = body.get("key", "")
    if key:
        return key, None
    gid = body.get("gid", "")
    word = body.get("word", "")
    if not gid or not word:
        return "", "缺少参数：需要 gid+word 或 key"
    return f"{gid}:{word}", None


class SlangApiMixin:
    """群黑话域 Web API。"""

    # ── /slang/groups ─────────────────────────────────────────
    @api_endpoint("群黑话群列表")
    @require_module("slang_manager", fallback=lambda: {"groups": []})
    async def api_slang_groups(self):
        items = await self.slang_manager.list_slang()
        groups: dict = {}
        for it in items:
            gid = it.get("gid", "")
            if not gid:
                continue
            if gid not in groups:
                node = await self.social.get_group(gid) if self.social else {}
                groups[gid] = {"gid": gid, "name": node.get("name", gid), "count": 0}
            groups[gid]["count"] += 1
        out = sorted(groups.values(), key=lambda x: x["count"], reverse=True)
        return json_response({"groups": out})

    # ── /slang/list ───────────────────────────────────────────
    @api_endpoint("群黑话列表")
    @require_query("gid", "缺少 gid 参数")
    @require_module("slang_manager",
                    fallback=lambda: {"gid": query_str("gid"), "items": []})
    async def api_slang_list(self):
        gid = query_str("gid")
        items = await self.slang_manager.list_slang(gid=gid)
        return json_response({"gid": gid, "items": items})

    # ── /slang/update ─────────────────────────────────────────
    @api_endpoint("群黑话更新")
    @with_json
    async def api_slang_update(self, body):
        key, err = _slang_key(body)
        if err:
            return error_response(err)
        if not self.slang_manager:
            return error_response("群黑话管理器未初始化")
        ok = await self.slang_manager.update(
            key,
            meaning=body.get("meaning", ""),
            injected=bool(body.get("injected", False)),
        )
        if not ok:
            return error_response("条目不存在")
        return json_response({"ok": True, "key": key})

    # ── /slang/delete ─────────────────────────────────────────
    @api_endpoint("群黑话删除")
    @with_json
    async def api_slang_delete(self, body):
        key, err = _slang_key(body)
        if err:
            return error_response(err)
        if not self.slang_manager:
            return error_response("群黑话管理器未初始化")
        ok = await self.slang_manager.delete(key)
        if not ok:
            return error_response("条目不存在")
        return json_response({"ok": True, "key": key})
