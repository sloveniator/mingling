"""stars/admin.py -- 后台管理面板 Web API 编排层（StarAdmin）
================================================================
AstrBot Plugin Pages Web API 的**路由表 + 装配**。32 个 handler 的实现
已按领域下沉到 ``apis/`` 子包（建议①）：

    apis/_base.py      公共样板：惰性 web 包装、参数/模块/异常装饰器、KV 护栏
    apis/_schema.py    配置校验复用 _conf_schema.json（建议④）
    apis/overview.py   stats / users / user / graph / recent_activity /
                       trigger / token_stats
    apis/system.py     kv / kv_set / config
    apis/collection.py 角色收集与角色管理
    apis/slang.py      群黑话
    apis/conversation.py 对话检视
    apis/worldview.py  世界观

本文件只保留：``initialize()`` → 内核 ensure → 注册路由。
新增面板端点 = 对应领域文件加一个方法 + 这里加一行 spec。

共享助手方法（``_active_uids`` / ``_get_relationship`` / ``get_kv_data`` …）
仍来自 ``MingbuStar`` 基类。
"""

from pathlib import Path

from astrbot.api import logger

from ..core import PLUGIN_NAME, MingbuStar
from .apis import (
    CollectionApiMixin,
    ConversationApiMixin,
    DiagnosticsApiMixin,
    IdentityApiMixin,
    OverviewApiMixin,
    SlangApiMixin,
    SystemApiMixin,
    TtsVoicesApiMixin,
    WorldviewApiMixin,
)

# 兼容旧导入路径：拆分前 MAX_LIST_USERS / _gather_limited 定义在本模块，
# 现有测试与 devtools 仍按 `from stars.admin import MAX_LIST_USERS` 取用。
from .apis._base import MAX_LIST_USERS as MAX_LIST_USERS


class StarAdmin(
    DiagnosticsApiMixin,
    OverviewApiMixin,
    SystemApiMixin,
    CollectionApiMixin,
    SlangApiMixin,
    ConversationApiMixin,
    WorldviewApiMixin,
    TtsVoicesApiMixin,
    IdentityApiMixin,
    MingbuStar,
):
    """管理面板 Web API 路由（数量以 _register_web_apis 中的 specs 为准）。"""

    async def initialize(self):
        # 内核初始化（幂等；首个初始化的 Star 完成真实创建）
        await self.core.ensure(self)
        # 注册后台管理面板 Web API（AstrBot Plugin Pages）
        self._register_web_apis()

    def _route_specs(self):
        """路由表：(路径, handler, 方法, 描述)。"""
        p = PLUGIN_NAME
        return [
            # ── 概览 / 用户 ──
            (f"/{p}/stats", self.api_stats, ["GET"], "概览统计"),
            (f"/{p}/users", self.api_users, ["GET"], "用户羁绊列表"),
            (f"/{p}/user", self.api_user_detail, ["GET"], "单用户详情"),
            (f"/{p}/trigger", self.api_trigger, ["POST"], "手动触发夜梦/预感/反思"),
            (f"/{p}/graph", self.api_graph, ["GET"], "关系网"),
            (f"/{p}/recent_activity", self.api_recent_activity, ["GET"], "近期互动"),
            (f"/{p}/token_stats", self.api_token_stats, ["GET"], "Token 用量统计"),
            # ── 系统 ──
            (f"/{p}/kv", self.api_kv_get, ["GET"], "KV 查看"),
            (f"/{p}/kv_set", self.api_kv_set, ["POST"], "KV 写入"),
            (f"/{p}/config", self.api_config, ["GET", "POST"], "配置读取/保存"),
            (f"/{p}/model_catalog", self.api_model_catalog, ["POST"], "拉取模型目录"),
            (f"/{p}/model_catalog_save", self.api_model_catalog_save, ["POST"], "保存模型目录"),
            (f"/{p}/model_catalog_get", self.api_model_catalog_get, ["GET"], "读取模型目录"),
            (f"/{p}/list_providers", self.api_list_providers, ["GET"], "列出服务商"),
            (f"/{p}/diagnostics", self.api_diagnostics, ["GET"], "系统诊断"),
            (f"/{p}/connectivity", self.api_connectivity, ["POST"], "服务连通性测试"),
            (f"/{p}/mingbu_test", self.api_mingbu_test, ["POST"], "命理后端连通性测试"),
            # ── 音色库 ──
            (f"/{p}/tts/voices", self.api_tts_voices, ["GET", "POST"], "拉取 TTS 音色列表"),
            (f"/{p}/tts/preview", self.api_tts_preview, ["GET", "POST"], "TTS 试听（短文本）"),
            (f"/{p}/tts/preview_long", self.api_tts_preview_long, ["GET", "POST"], "TTS 试听（长文本）"),
            # ── 角色收集 ──
            (f"/{p}/collection/progress", self.api_collection_progress, ["GET"], "角色收集进度"),
            (f"/{p}/collection/unlock_hidden", self.api_collection_unlock_hidden, ["POST"], "手动解锁隐藏角色"),
            (f"/{p}/collection/all", self.api_collection_all, ["GET"], "角色收集全览"),
            (f"/{p}/collection/switch", self.api_collection_switch, ["POST"], "切换当前角色"),
            (f"/{p}/collection/assign", self.api_collection_assign, ["POST"], "为用户分配初始角色"),
            # ── 世界观 ──
            (f"/{p}/worldview/presets", self.api_worldview_presets, ["GET"], "世界观预设列表"),
            (f"/{p}/worldview/custom", self.api_worldview_custom, ["GET", "POST"], "自定义世界观读写"),
            (f"/{p}/worldview/preview", self.api_worldview_preview, ["POST"], "预览世界观效果"),
            # ── 群黑话 ──
            (f"/{p}/slang/groups", self.api_slang_groups, ["GET"], "群黑话·有数据的群"),
            (f"/{p}/slang/list", self.api_slang_list, ["GET"], "群黑话·列表"),
            (f"/{p}/slang/update", self.api_slang_update, ["POST"], "群黑话·更新释义/注入"),
            (f"/{p}/slang/delete", self.api_slang_delete, ["POST"], "群黑话·删除"),
            # ── 对话检视 ──
            (f"/{p}/conversation/list", self.api_conversation_list, ["GET"], "对话检视·列表"),
            (f"/{p}/conversation/learn", self.api_conversation_learn, ["POST"], "对话检视·学习标注"),
            (f"/{p}/conversation/unlearn", self.api_conversation_unlearn, ["POST"], "对话检视·取消学习"),
            (f"/{p}/conversation/delete", self.api_conversation_delete, ["POST"], "对话检视·删除"),
            (f"/{p}/conversation/stats", self.api_conversation_stats, ["GET"], "对话检视·统计"),
            (f"/{p}/conversation/knowledge", self.api_conversation_knowledge, ["GET"], "对话检视·已学知识"),
            (f"/{p}/conversation/weighted", self.api_conversation_weighted, ["GET"], "对话检视·高权重"),
            (f"/{p}/conversation/extract", self.api_conversation_extract, ["POST"], "对话检视·批量入记忆"),
            (f"/{p}/conversation/reweight", self.api_conversation_reweight, ["POST"], "对话检视·重算权重"),
            # ── 跨平台身份绑定 ──
            (f"/{p}/identity/binding", self.api_identity_binding, ["GET"], "查询账号绑定"),
            (f"/{p}/identity/bind", self.api_identity_bind, ["POST"], "写入/解绑账号绑定"),
        ]

    def _version_guard_key(self) -> str:
        """版本化幂等守卫键：从 metadata.yaml 读版本号。

        AstrBot reload 插件时 star_context 存活、已注册路由不会自动清理，
        旧路由仍绑着旧代码的 handler（reload 形同虚设）。把守卫键绑到
        metadata.yaml 的 version 上：每次升版后的首次注册必然执行，
        register_web_api 按「路由+方法」替换旧 handler，新代码即接管。
        """
        key = "_mingbu_admin_registered"
        try:
            meta = Path(__file__).resolve().parents[1] / "metadata.yaml"
            if meta.exists():
                for line in meta.read_text(encoding="utf-8").splitlines():
                    if line.strip().startswith("version:"):
                        v = line.split(":", 1)[1].strip().strip("'\"")
                        if v:
                            key = f"{key}_v{v}"
                        break
        except Exception:  # noqa: BLE001 — 守卫键读取失败时退回旧行为
            pass
        return key

    def _register_web_apis(self):
        ctx = getattr(self, "context", None)
        if ctx is None or not hasattr(ctx, "register_web_api"):
            logger.warning("[mingbu] 当前 AstrBot 无 register_web_api，管理面板不可用")
            return
        # 幂等守卫（版本化）：AstrBot 在部分版本下会把 stars/ 子目录里的
        # StarAdmin 与主 Star 都初始化，二者共享同一个 context；同版本内
        # 已注册则跳过，避免重复。版本变更后强制重新注册以接管旧路由。
        guard_key = self._version_guard_key()
        if getattr(ctx, guard_key, False):
            return
        try:
            specs = self._route_specs()
            for route, handler, methods, desc in specs:
                ctx.register_web_api(route, handler, methods, desc)
            setattr(ctx, guard_key, True)
            logger.info(f"[mingbu] 已注册管理面板 Web API（{len(specs)} 个路由，guard={guard_key}）")
        except Exception as e:
            logger.warning(f"[mingbu] 注册 Web API 失败(管理面板不可用): {e}")
