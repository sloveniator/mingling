"""stars/apis/worldview.py -- 世界观预设 / 自定义 / 预览
====================================================
对应路由：``/worldview/presets`` ``/worldview/custom`` ``/worldview/preview``
"""

from __future__ import annotations

import json

from ._base import (
    api_endpoint,
    error_response,
    json_response,
    request_json,
    request_method,
    with_json,
)

KV_CUSTOM_WORLDVIEW = "mingbu.custom_worldview"

# 自定义世界观必须覆盖的能力槽位——少一个，适配器在拼系统提示时会
# 撞上 KeyError，表现为「换了世界观后 bot 突然不说话」。
REQUIRED_WORLDVIEW_FIELDS = (
    "self_name", "world_name", "memory", "schedule", "dream",
    "search", "news", "image_vision", "merged_forward",
    "creation", "voice", "screen_peek",
)


class WorldviewApiMixin:
    """世界观域 Web API。"""

    # ── /worldview/presets ────────────────────────────────────
    @api_endpoint("世界观预设")
    async def api_worldview_presets(self):
        from ...worldview_adapter import WORLDVIEWS
        presets = [
            {
                "id": key,
                "name": wv.get("self_name", key),
                "world_name": wv.get("world_name", ""),
            }
            for key, wv in WORLDVIEWS.items()
        ]
        return json_response({"presets": presets})

    # ── /worldview/custom ─────────────────────────────────────
    @api_endpoint("自定义世界观")
    async def api_worldview_custom(self):
        if request_method() == "GET":
            try:
                raw = await self.get_kv_data(KV_CUSTOM_WORLDVIEW, None)
                if raw:
                    data = json.loads(raw) if isinstance(raw, str) else raw
                    return json_response({"custom": data})
            except Exception as e:
                return json_response({"custom": None, "error": str(e)})
            return json_response({"custom": None})
        return await self._save_custom_worldview(await request_json())

    async def _save_custom_worldview(self, body: dict):
        custom_wv = body.get("custom_worldview")
        if custom_wv is None:
            await self.put_kv_data(KV_CUSTOM_WORLDVIEW, "")
            return json_response({"ok": True, "message": "已清除自定义世界观"})
        try:
            parsed = json.loads(custom_wv) if isinstance(custom_wv, str) else custom_wv
            if not isinstance(parsed, dict):
                return error_response("自定义世界观必须是 JSON 对象")
            missing = [k for k in REQUIRED_WORLDVIEW_FIELDS if k not in parsed]
            if missing:
                return error_response(f"缺少必要字段: {', '.join(missing)}")
            await self.put_kv_data(
                KV_CUSTOM_WORLDVIEW, json.dumps(parsed, ensure_ascii=False)
            )
            return json_response({"ok": True, "message": "自定义世界观已保存"})
        except json.JSONDecodeError as e:
            return error_response(f"JSON 格式错误: {e}")
        except Exception as e:
            return error_response(f"保存失败: {e}")

    # ── /worldview/preview ────────────────────────────────────
    @api_endpoint("世界观预览")
    @with_json
    async def api_worldview_preview(self, body):
        from ...worldview_adapter import adapt_system_prompt
        try:
            preview = adapt_system_prompt(
                body.get("base_prompt", "你是一个温柔体贴的 AI 朋友。"),
                body.get("style", "default"),
                body.get("char_name", "灵犀"),
                custom_worldview=body.get("custom_worldview", ""),
            )
            return json_response({"preview": preview})
        except Exception as e:
            return error_response(f"预览生成失败: {e}")
