from __future__ import annotations

try:
    from quart import jsonify
except ImportError:  # 测试桩/无 quart 环境降级
    def jsonify(data, status_code: int = 200):
        return data, status_code

from store import Store


class AdminTimelineAPI:
    """
    暴露 AstrBot Plugin Pages 所需的时间线数据。
    路由注册在 context.register_web_api()，由 AstrBot 面板鉴权保护。
    """

    def __init__(self, store: Store, cfg):
        self._store = store
        self._cfg = cfg

    def register(self, context) -> None:
        try:
            context.register_web_api(
                "/mingling/users",
                self._users_handler,
                ["GET"],
                "命灵已知私聊用户",
            )
            context.register_web_api(
                "/mingling/timeline",
                self._timeline_handler,
                ["GET"],
                "命灵用户时间线",
            )
        except Exception:
            # Web API 在部分版本/环境不可用，不影响主链路
            pass

    async def _users_handler(self, request):
        umos = await self._store.list_known_umos()
        return jsonify({"ok": True, "count": len(umos), "umos": umos})

    async def _timeline_handler(self, request):
        umo = request.args.get("umo")
        if not umo:
            return jsonify({"ok": False, "error": "umo required"}), 400
        records = await self._store.read_timeline(umo, limit=self._cfg.timeline_max_lines)
        return jsonify({"ok": True, "count": len(records), "records": records})
