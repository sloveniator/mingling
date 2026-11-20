from __future__ import annotations

import json
from dataclasses import fields
from pathlib import Path

try:
    from quart import jsonify
except ImportError:  # 测试桩/无 quart 环境降级
    def jsonify(data, status_code: int = 200):
        return data, status_code

from _version import VERSION
from config import CompanionConfig
from store import Store


def _json(data, status: int = 200):
    """统一响应；兼容 quart Response 与测试桩 tuple。"""
    resp = jsonify(data)
    if status == 200:
        return resp
    if isinstance(resp, tuple):
        return resp[0], status
    return resp, status


# 面板可编辑的活动配置键（relation_memory_half_life_days 为嵌套 dict，暂不做表单）
CONFIG_KEYS = tuple(
    f.name for f in fields(CompanionConfig)
    if f.name != "relation_memory_half_life_days"
)
EXTRA_KEYS = ("mingbu_api_url",)
ALLOWED_KEYS = set(CONFIG_KEYS) | set(EXTRA_KEYS)


class AdminTimelineAPI:
    """命灵 v5 管理面板 API：总览、用户、时间线、配置读写。"""

    def __init__(self, store: Store, cfg: CompanionConfig, plugin=None):
        self._store = store
        self._cfg = cfg
        self._plugin = plugin

    def register(self, context) -> None:
        specs = [
            ("/mingling/overview", self._overview_handler, ["GET"], "命灵总览"),
            ("/mingling/config", self._config_handler, ["GET", "POST"], "命灵配置读写"),
            ("/mingling/users", self._users_handler, ["GET"], "命灵已知私聊用户"),
            ("/mingling/timeline", self._timeline_handler, ["GET"], "命灵用户时间线"),
        ]
        for route, handler, methods, desc in specs:
            try:
                context.register_web_api(route, handler, methods, desc)
            except Exception:
                # Web API 在部分版本/环境不可用，不影响主链路
                pass

    # ---------- 工具 ----------
    def _active_config(self) -> dict:
        data = {k: getattr(self._cfg, k) for k in CONFIG_KEYS}
        raw = getattr(self._plugin, "_raw_config", {}) or {}
        data["mingbu_api_url"] = raw.get("mingbu_api_url", "")
        return data

    async def _request_json(self, request) -> dict:
        try:
            body = await request.get_json()
        except AttributeError:
            body = await request.json(default={})
        return body if isinstance(body, dict) else {}

    @staticmethod
    def _args(request):
        return getattr(request, "args", None) or getattr(request, "query", {})

    @classmethod
    def _query_int(cls, request, name: str, default: int, lo: int, hi: int) -> int:
        try:
            value = int(cls._args(request).get(name, default))
        except (TypeError, ValueError):
            return default
        return max(lo, min(hi, value))

    def _persist_config(self, data: dict) -> tuple[bool, str]:
        """写回 AstrBot 配置对象；没有 save_config 时落盘到 data/config。"""
        plugin = self._plugin
        raw = getattr(plugin, "_raw_config", None)
        if isinstance(raw, dict):
            raw.update(data)
        cfg_obj = getattr(plugin, "config", None)
        try:
            if isinstance(cfg_obj, dict):
                cfg_obj.update(data)
                saver = getattr(cfg_obj, "save_config", None)
                if callable(saver):
                    saver()
                    return True, "已写入 AstrBot 配置"
        except Exception as e:  # noqa: BLE001 - 面板保存失败需回显
            return False, f"写入 AstrBot 配置失败: {e}"

        # 兜底：直接写插件配置文件（适用于 config 为普通 dict 的环境）
        try:
            from astrbot.core.utils.astrbot_path import get_astrbot_data_path

            path = Path(get_astrbot_data_path()) / "config" / "mingling_config.json"
            current = {}
            if path.exists():
                current = json.loads(path.read_text(encoding="utf-8-sig"))
            if not isinstance(current, dict):
                current = {}
            current.update(data)
            path.write_text(
                json.dumps(current, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            return True, "已写入 mingling_config.json"
        except Exception as e:  # noqa: BLE001
            return False, f"配置持久化失败: {e}"

    # ---------- handlers ----------
    async def _overview_handler(self, request):
        try:
            umos = await self._store.list_known_umos()
            stats = self._store.timeline_stats()
            cron = []
            ctx = getattr(self._plugin, "context", None)
            manager = getattr(ctx, "cron_manager", None) if ctx else None
            if manager is not None:
                jobs = manager.list_jobs()
                if hasattr(jobs, "__await__"):
                    jobs = await jobs
                for job in jobs or []:
                    name = getattr(job, "name", "")
                    if not name.startswith("mingling_"):
                        continue
                    nxt = getattr(job, "next_run_time", None)
                    cron.append({
                        "name": name,
                        "cron": getattr(job, "cron_expression", ""),
                        "timezone": getattr(job, "timezone", ""),
                        "enabled": bool(getattr(job, "enabled", True)),
                        "next_run": nxt.isoformat() if hasattr(nxt, "isoformat") else str(nxt or ""),
                    })
            return _json({
                "ok": True,
                "name": "mingling",
                "version": VERSION,
                "enabled": bool(self._cfg.enabled),
                "users": len(umos),
                "timeline": stats,
                "config": self._active_config(),
                "cron": cron,
            })
        except Exception as e:  # noqa: BLE001
            return _json({"ok": False, "error": str(e)[:300]}, 500)

    async def _config_handler(self, request):
        if getattr(request, "method", "GET").upper() == "GET":
            return _json({"ok": True, "config": self._active_config(),
                            "editable": sorted(ALLOWED_KEYS)})
        try:
            body = await self._request_json(request)
            unknown = sorted(set(body) - ALLOWED_KEYS)
            if unknown:
                return _json({"ok": False, "error": f"不支持的配置键: {', '.join(unknown)}"}, 400)
            candidate = dict(getattr(self._plugin, "_raw_config", {}) or {})
            candidate.update(body)
            new_cfg = CompanionConfig.from_raw(candidate)
            for f in fields(CompanionConfig):
                setattr(self._cfg, f.name, getattr(new_cfg, f.name))
            loops = getattr(self._plugin, "_loops", None)
            if loops is not None:
                loops._window_sec = self._cfg.open_loop_window_days * 86400
            if "mingbu_api_url" in body:
                client = getattr(self._plugin, "_client", None)
                if client is not None and hasattr(client, "close"):
                    await client.close()
                self._plugin._client = None
            ok, message = self._persist_config(dict(body))
            return _json({
                "ok": ok,
                "message": message,
                "config": self._active_config(),
            }, 200 if ok else 500)
        except Exception as e:  # noqa: BLE001
            return _json({"ok": False, "error": str(e)[:300]}, 500)

    async def _users_handler(self, request):
        try:
            umos = await self._store.list_known_umos()
            items = []
            for umo in umos[:200]:
                rel = await self._store.load(umo, "relationship", {})
                emo = await self._store.load(umo, "emotion", {})
                topic = await self._store.load(umo, "topic_stack", {})
                loops = await self._store.load(umo, "open_loops", [])
                items.append({
                    "umo": umo,
                    "last_message": await self._store.load(umo, "last_user_msg_at", 0),
                    "stage": rel.get("stage", "normal") if isinstance(rel, dict) else "normal",
                    "intimacy": rel.get("intimacy", 0.0) if isinstance(rel, dict) else 0.0,
                    "mood": await self._store.load(umo, "last_mood", ""),
                    "topic": topic.get("current") if isinstance(topic, dict) else None,
                    "open_loops": len(loops) if isinstance(loops, list) else 0,
                    "pleasantness": emo.get("pleasantness", 0.0) if isinstance(emo, dict) else 0.0,
                    "tension": emo.get("tension", 0.0) if isinstance(emo, dict) else 0.0,
                })
            items.sort(key=lambda x: float(x.get("last_message") or 0), reverse=True)
            return _json({"ok": True, "count": len(umos), "users": items})
        except Exception as e:  # noqa: BLE001
            return _json({"ok": False, "error": str(e)[:300]}, 500)

    async def _timeline_handler(self, request):
        args = self._args(request)
        umo = args.get("umo")
        if not umo:
            return _json({"ok": False, "error": "umo required"}, 400)
        limit = self._query_int(request, "limit", self._cfg.timeline_max_lines, 1, 1000)
        kind = args.get("kind", "")
        try:
            records = await self._store.read_timeline(umo, limit=1000)
            if kind:
                records = [r for r in records if r.get("kind") == kind]
            records = records[-limit:]
            return _json({"ok": True, "count": len(records), "records": records})
        except Exception as e:  # noqa: BLE001
            return _json({"ok": False, "error": str(e)[:300]}, 500)
