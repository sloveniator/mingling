"""stars/apis/identity.py -- 跨平台账号绑定
================================================
对应路由：``/identity/binding`` ``/identity/bind``

跨平台共享记忆的唯一入口（2.0 第三阶段）。默认各平台身份彼此隔离
（见 integration/identity_resolver.default_unified_id），只有把某平台的
platform_user_id 显式绑定到目标 unified_user_id 后，resolve_user 才会把它们
归并到同一身份、共享长期记忆。

安全边界：管理面板属站长信任区（本插件无 admin 鉴权层），故此处允许指定任意
platform_user_id 做绑定/解绑。聊天侧**只**提供只读查询命令（见 main.py 的
「我的绑定」），不允许普通用户在聊天里改绑定——否则任意 sender 可把自己绑到
他人 unified_user_id，劫持其长期记忆。跨账号归属验证需站外完成。
"""

from __future__ import annotations

from integration.identity_resolver import KV_BINDING_PREFIX, bind_identity
from ._base import (
    api_endpoint,
    error_response,
    json_response,
    query_str,
    require_json,
    require_query,
)


class IdentityApiMixin:
    """跨平台账号绑定域 Web API。"""

    @staticmethod
    def _binding_key(platform_user_id: str) -> str:
        return f"{KV_BINDING_PREFIX}:{platform_user_id}"

    # ── /identity/binding（GET，查绑定）───────────────────────
    @api_endpoint("查询账号绑定")
    @require_query("platform_user_id", "缺少 platform_user_id")
    async def api_identity_binding(self):
        platform_user_id = query_str("platform_user_id")
        try:
            bound = await self.get_kv_data(self._binding_key(platform_user_id), None)
        except Exception as e:
            return error_response(f"读取绑定失败: {str(e)[:120]}")
        bound_str = (bound or "").strip() if isinstance(bound, str) else ""
        return json_response({
            "ok": True,
            "platform_user_id": platform_user_id,
            "unified_user_id": bound_str,
            "bound": bool(bound_str),
            "note": ("已绑定到该统一身份，跨平台共享长期记忆"
                     if bound_str else "未绑定，按平台隔离"),
        })

    # ── /identity/bind（POST，写/解绑定）──────────────────────
    @api_endpoint("账号绑定")
    @require_json("platform_user_id", "缺少 platform_user_id")
    async def api_identity_bind(self, body):
        platform_user_id = str(body.get("platform_user_id", "")).strip()
        unified_user_id = str(body.get("unified_user_id", "")).strip()
        if not platform_user_id:
            return error_response("platform_user_id 不能为空")
        try:
            ok = await bind_identity(
                self.put_kv_data, platform_user_id, unified_user_id)
        except Exception as e:
            return error_response(f"绑定写入失败: {str(e)[:120]}")
        if not ok:
            return error_response("绑定写入失败（platform_user_id 为空或 KV 异常）")
        action = ("解绑成功，恢复按平台隔离" if not unified_user_id
                  else f"已绑定到 {unified_user_id}")
        return json_response({
            "ok": True,
            "platform_user_id": platform_user_id,
            "unified_user_id": unified_user_id,
            "action": action,
        })
