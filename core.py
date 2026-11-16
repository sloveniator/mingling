"""
core.py -- 共享内核 + 基类（mingling v4 多 Star 拆分）
=================================================================
将原本 ~1180 行的 main.py 拆为：
  - core.py             : MingbuCore 单例（持有所有引擎）+ MingbuStar 基类（极薄）
  - constants.py        : 全插件共享常量（KV 键名 / 命理方法集 / 预编译正则）
  - star_mixins/        : MingbuStar 的 30 个共享助手方法（按职责分 8 个 mixin）
  - hooks/              : LLM 请求钩子链
  - main.py             : MingbuPlugin（核心 Star：生命周期 + 全部 @filter handler）
  - stars/admin.py      : StarAdmin（后台管理面板 Web API，唯一保留的子 Star）

所有 Star 共享同一个 MingbuCore 单例（避免重复初始化引擎 / HTTP 连接池）。
每个 Star 通过 MingbuStar 基类获得引擎属性代理与共享助手方法，
从而 dream/growth/proactive/social/memory_core 等引擎模块经
`self.plugin.<attr>` 访问时仍能命中同一套实现。

依赖方向严格单向、无环::

    constants ──> star_mixins ──> core ──> main / stars/*
        └───────────────────────────┘

本模块保留常量与钩子类的 ``X as X`` re-export（PEP 484 显式重导出），
故 ``from core import KV_USERS / CrisisHook`` 等既有写法继续可用。
"""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING, Dict, Optional, Set

from astrbot.api import logger
from astrbot.api.star import Context, Star

from .client import MingbuClient

if TYPE_CHECKING:
    # 引擎类仅用于类型标注：真实构造已下沉到 features.py 的能力注册表，
    # 运行时不再在 core 顶层导入这些模块（避免无谓的启动开销与潜在循环）。
    from .dream import DreamEngine
    from .growth import GrowthEngine
    from .proactive import ProactiveEngine
    from .social import SocialGraph

# ---- constants 模块 ----
# 常量已下沉到 constants.py 以打破「core 导入 mixin、mixin 又要用常量」的
# 循环。这里用 PEP 484 显式 re-export（``X as X``）保留旧导入路径：
# stars/collectors.py、stars/admin.py、conversation_inspector.py 与现有
# 测试均从 core 取这些名字。新代码请直接 ``from .constants import ...``。
from .constants import BIRTH_YEAR_RE as BIRTH_YEAR_RE
from .constants import BUGUA_BRIDGE_TO_LIUYAO as BUGUA_BRIDGE_TO_LIUYAO
from .constants import BUGUA_METHODS as BUGUA_METHODS
from .constants import EMOTION_KEYWORD_PATTERN as EMOTION_KEYWORD_PATTERN
from .constants import EMOTION_KEYWORD_REGEX as EMOTION_KEYWORD_REGEX
from .constants import KV_CONTEXT as KV_CONTEXT
from .constants import KV_PROFILE as KV_PROFILE
from .constants import KV_USERS as KV_USERS
from .constants import LLM_TEXT_TIMEOUT as LLM_TEXT_TIMEOUT
from .constants import MAX_CONTEXT_MSGS as MAX_CONTEXT_MSGS
from .constants import MINGBU_API_URL
from .constants import MINGBU_METHODS_ALL as MINGBU_METHODS_ALL
from .constants import MINGPAN_METHODS as MINGPAN_METHODS
from .constants import PLUGIN_NAME as PLUGIN_NAME
from .constants import ZODIAC_RE as ZODIAC_RE
from .counseling import CounselingSessionManager, EmotionalTracker

# ---- hooks 子包 ----
# 钩子实现已从 core.py 尾部（原 921-1092 行）迁出到 hooks/ 子包。
# 全部 ``X as X`` 均为 PEP 484 显式 re-export，用于保留旧导入路径，使
# ``from core import CrisisHook, HookChain`` 等既有写法（含现有测试）
# 继续可用。新代码请直接 ``from .hooks import ...``。
from .hooks import BlessingHook as BlessingHook
from .hooks import CharacterHook as CharacterHook
from .hooks import CounselingHook as CounselingHook
from .hooks import CrisisHook as CrisisHook
from .hooks import HookChain as HookChain
from .hooks import HookProcessor as HookProcessor
from .hooks import PersonaHook as PersonaHook
from .hooks import get_default_chain as get_default_chain
from .memory_core import MemoryCore
from .persona import PersonaState, Relationship
from .provider_router import ProviderRouter
from .silent_divination import SilentDiviner

# ---- star_mixins 子包 ----
# MingbuStar 的 30 个共享助手方法（原 core.py 内 557 行）已按职责迁出。
# mixin 一律不 import core，只通过 self.<engine> 鸭子类型访问运行时依赖，
# 由 MingbuStar.__getattr__ 代理到内核，因此不构成循环依赖。
from .star_mixins import (
    ConversationContextMixin,
    GroupChatMixin,
    HealthProfileMixin,
    LLMRequestHookMixin,
    LLMTextMixin,
    PersonaContextMixin,
    ProfileMixin,
    RelationshipMixin,
)


class MingbuCore:
    """共享内核：持有全部引擎实例（单例）。

    由首个初始化的 Star 通过 ensure() 完成依赖 owner 的引擎与人格的惰性创建；
    后续 Star 复用同一实例。所有 Star 通过基类属性代理访问引擎。
    """

    def __init__(self):
        self._config: dict = {}
        # KV 运行时覆盖层（mingbu.runtime_config）的权威内存镜像。
        # configure() 会被 AstrBot 在热重载/重新实例化时再次调用并传入静态 config，
        # 若只用 _config 单独承载，静态 config 会把 KV 覆盖层（外部服务 API 地址等）
        # 冲掉；这里单独缓存一份镜像，configure 时重新合并，保证刷新后不丢。
        self._runtime_overlay: dict = {}
        # 不依赖 owner 的引擎（构造即可用）
        self.api = MingbuClient(MINGBU_API_URL)
        self.tracker = EmotionalTracker()
        self.diviner = SilentDiviner(self.api)
        self.diviner.set_interpret_config(self._config)
        # 依赖 owner（插件 Star）的引擎，ensure() 中创建
        self.memory: Optional[MemoryCore] = None
        self.persona: Optional[PersonaState] = None
        self.dream: Optional[DreamEngine] = None
        self.growth: Optional[GrowthEngine] = None
        self.proactive: Optional[ProactiveEngine] = None
        # 命卜后台关注 / 自我进化（ensure 中按配置惰性创建）
        self.background_watch = None
        self.self_evolution = None
        self.social: Optional[SocialGraph] = None
        self._rel_cache: Dict[str, Relationship] = {}
        self._max_rel_cache = 500  # 关系缓存上限
        # 活跃用户索引的内存镜像：_remember_user 在每条消息上都会调用，
        # 若每次都读 KV + json.loads 整个用户数组，成本随用户数线性增长。
        # 这里只在首次访问时加载一次，之后命中内存集合即可判重。
        self._known_uids: Optional[Set[str]] = None
        self._in_sys_llm = False  # 系统级 LLM 调用防递归（全局共享）
        self._owner: Optional[Star] = None
        # 角色收集玩法
        self.character_pool = None
        self.character_collection = None
        # 2.0 增强门控：FeatureFlags（三模式闸门）+ ContextGateway（统一上下文网关）。
        # 均在 ensure() 中按 config 构建（flags 只读 config；gateway 依赖 memory/
        # persona，必须在二者就绪后建）。__init__ 先声明 None：ensure() 完成前任何
        # 访问都优雅降级，绝不 AttributeError（proactive 当年此坑致整条链路失效）。
        self.flags = None
        self.gateway = None
        # Token 监控
        self.token_monitor = None
        # Provider 编排
        self.provider_router = None
        # 新闻/搜索
        self.news_search = None
        # 群黑话
        self.slang_manager = None
        # 对话检视
        self.conversation_inspector = None
        # 多轮心理疏导会话管理器
        self.counsel_sessions = None
        self._ensured = False
        # 能力运行状态供管理面板诊断：enabled / initialized / disabled / failed。
        # 不把初始化失败吞成“插件正常”，让用户能看到具体退化能力。
        self.capability_status: Dict[str, dict] = {}
        # 并发安全：保护 ensure() 构建段，避免多 Star 并行初始化时双建引擎（审查报告 P2）。
        # 惰性创建：asyncio.Lock 在 __init__（同步上下文）构造会在 Python 3.9 抛 RuntimeError，
        # 故此处仅占位 None，首次在协程 ensure() 内惰性建锁（同 proactive._get_save_lock 范式）。
        self._ensure_lock = None

    def configure(self, config: Optional[dict]):
        """接收 AstrBot 插件配置（首个 Star 调用时生效，幂等）。
        会自动合并运行时 KV 覆盖层（mingbu.runtime_config）。"""
        if not config:
            return
        # AstrBot v4.26+ 可能传入 AstrBotConfig 对象，安全转为 dict
        try:
            incoming = dict(config)
        except Exception:
            incoming = {}
        # 原地更新而非替换引用：多个 Star 实例在 __init__ 里都持有
        # `self._config = self.core._config` 的同一引用；若这里整体替换
        # core._config，旧 Star 实例的引用会断裂（热重载后读旧 dict）。
        self._config.clear()
        self._config.update(incoming)
        # 热重载/重新 configure 时，AstrBot 传入的静态 config 不含 KV 运行时
        # 覆盖层；这里把已加载的覆盖层（外部服务 API 地址等）重新合并回来，
        # 避免面板配置在刷新后丢失。首次加载时 _runtime_overlay 为空，无副作用。
        if self._runtime_overlay:
            self._config.update(self._runtime_overlay)
        self._sync_api_client()

    def _sync_api_client(self):
        """按当前配置同步命理后端客户端（地址变化则重建，并刷新暗盘 interpret 配置）。"""
        cfg_url = (self._config.get("mingbu_api_url") or "").strip()
        if cfg_url and getattr(self.api, "base_url", "") != cfg_url:
            self.api = MingbuClient(cfg_url)
            self.diviner = SilentDiviner(self.api)
        self.diviner.set_interpret_config(self._config)

    def reload_flags(self):
        """按当前 _config 重建增强门控快照（FeatureFlags + gateway.flags 引用）。

        背景：flags 在 ensure() 的 _ensured 守卫块内只建一次；面板切换
        integration_mode 后走 configure()+_apply_hot_reload()，二者都不重建
        flags，导致 observe/enhance 切换不重启不生效（端到端实测复现）。
        FeatureFlags 是可变 dataclass，这里重建实例并同步 gateway.flags 引用，
        使注入闸门（_inject_gateway_extras 读 core.flags.should_modify_request）
        立即随新模式变化。fail-open：重建失败只记日志，不改变现有 flags。
        """
        try:
            from .integration.feature_flags import FeatureFlags
            self.flags = FeatureFlags.from_config(self._config)
            if self.gateway is not None:
                self.gateway.flags = self.flags
        except Exception as e:
            logger.debug(f"[mingbu] 增强门控热重载失败(已忽略): {e}")

    async def _load_runtime_config(self, owner: Star):
        """从 KV 加载运行时配置覆盖层并合并到当前配置。"""
        try:
            raw = await owner.get_kv_data("mingbu.runtime_config", None)
            if raw:
                runtime = json.loads(raw) if isinstance(raw, str) else raw
                if isinstance(runtime, dict):
                    # 缓存权威镜像，供 configure 在热重载后重新合并
                    self._runtime_overlay = dict(runtime)
                    self._config.update(runtime)
                    logger.info(f"[mingbu] 已加载运行时配置覆盖层({len(runtime)}项)")
        except Exception as e:
            logger.debug(f"[mingbu] 运行时配置加载失败(忽略): {e}")
        # KV 覆盖层可能含 mingbu_api_url / mingbu_interpret_*，刷新到客户端与暗盘引擎
        self._sync_api_client()

    async def ensure(self, owner: Star):
        """惰性、幂等初始化（依赖 owner 的引擎 + 人格）。

        按能力注册表（features.py）的开关惰性构建各引擎，避免一长串手动 if
        分支；新增能力只改 features.py，无需触及此处。

        并发安全：用 asyncio.Lock 保护构建段（审查报告 P2）。AstrBot 并行
        初始化多个 Star 时，首个协程构建引擎，其余协程在锁外快速返回，
        不会双建引擎或泄漏 KV/HTTP 连接池。
        """
        # 每次 initialize 都重新加载 KV 运行时覆盖层（幂等合并）。
        # 这是修复「热重载后外部服务地址失效」的关键：热重载会重新实例化 Star、
        # 触发 configure 用静态 config 覆盖内存态，而 _ensured 守卫会让旧的
        # 加载逻辑直接 return、不再读 KV；把加载提到守卫之前，保证任何一次
        # initialize（含热重载）都能把 KV 里的外部服务地址等配置捞回来。
        await self._load_runtime_config(owner)
        if self._ensured:
            return
        if self._ensure_lock is None:
            self._ensure_lock = asyncio.Lock()
        async with self._ensure_lock:
            if self._ensured:
                return
            self._owner = owner
            cfg = self._config

            # 核心记忆（几乎所有功能都依赖，始终初始化）
            self.memory = MemoryCore(owner)

            # Provider 编排（始终初始化，供其他模块使用）
            self.provider_router = ProviderRouter(owner)

            # 迭代能力注册表，按 config_key 开关惰性构建，并记录可观测状态。
            from .features import get_features
            self.capability_status = {}
            for spec in get_features().values():
                enabled = bool(spec.enabled_if(cfg))
                status = {
                    "name": spec.name,
                    "config_key": spec.config_key,
                    "enabled": enabled,
                    "state": "disabled" if not enabled else "initializing",
                    "error": "",
                }
                self.capability_status[spec.name] = status
                if enabled:
                    try:
                        await spec.build(self, owner, cfg)
                        status["state"] = "initialized"
                    except Exception as e:
                        status["state"] = "failed"
                        status["error"] = str(e)[:200]
                        logger.warning(f"[mingbu] 能力 {spec.name} 初始化失败(已忽略): {e}")
                else:
                    logger.debug(f"[mingbu] 能力 {spec.name} 已禁用")

            # 暗盘缓存 TTL（diviner 在 __init__ 构建）
            ttl = cfg.get("darkchannel_cache_ttl", 3600)
            if self.diviner and hasattr(self.diviner, "_cache_ttl"):
                self.diviner._cache_ttl = ttl

            # 多轮心理疏导会话管理器（持久化到 KV）
            self.counsel_sessions = CounselingSessionManager(owner)

            # 载入 bot 自身人格状态
            persona_name = cfg.get("persona_name", "灵犀")
            try:
                raw = await owner.get_kv_data("mingbu.persona", None)
                self.persona = PersonaState.from_dict(json.loads(raw)) if raw else PersonaState(name=persona_name)
            except Exception as e:
                logger.warning(f"[mingbu] 人格载入失败，使用默认人格: {e}")
                self.persona = PersonaState(name=persona_name)

            # 2.0 增强门控：FeatureFlags（三模式闸门）+ ContextGateway。
            # 放在 memory(235)/persona(279) 均就绪之后：gateway.build 依赖二者，
            # 过早构建会拿到 None。flags 只读 config，本步不改变任何生产行为
            # （gateway 尚未接入主链路，observe 模式下 should_modify_request=False）。
            try:
                from .integration.context_gateway import ContextGateway
                from .integration.feature_flags import FeatureFlags
                self.flags = FeatureFlags.from_config(cfg)
                self.gateway = ContextGateway(self, flags=self.flags)
                logger.debug(
                    f"[mingbu] 增强门控就绪: mode={self.flags.mode}, "
                    f"modify_request={self.flags.should_modify_request()}")
            except Exception as e:
                # 门控构建失败优雅降级为 None：其余功能照常，绝不阻断内核就绪
                self.flags = None
                self.gateway = None
                logger.debug(f"[mingbu] 增强门控初始化失败(已忽略): {e}")

            self._ensured = True
            logger.info(f"[mingbu] 内核已就绪：{self.persona.name}（心情 {int(self.persona.mood)}）")

    async def shutdown(self):
        # 最先落盘：记忆/对话检视采用写回策略，卸载前必须把脏数据刷出去
        try:
            if self.memory:
                await self.memory.flush_all()
        except Exception as e:
            logger.warning(f"[mingbu] 记忆落盘失败: {e}")
        try:
            if self.conversation_inspector:
                await self.conversation_inspector.flush_all()
        except Exception as e:
            logger.warning(f"[mingbu] 对话记录落盘失败: {e}")
        # 先停主动引擎
        try:
            if self.proactive:
                await self.proactive.stop()
        except Exception as e:
            logger.debug(f"[mingbu] 停止主动引擎时忽略: {e}")
        # 停止后台关注 / 自我进化
        try:
            if self.background_watch:
                await self.background_watch.stop()
        except Exception as e:
            logger.debug(f"[mingbu] 停止后台关注时忽略: {e}")
        try:
            if self.self_evolution:
                await self.self_evolution.stop()
        except Exception as e:
            logger.debug(f"[mingbu] 停止自我进化时忽略: {e}")
        # 释放 HTTP 连接池
        try:
            await self.api.close()
        except Exception as e:
            logger.debug(f"[mingbu] 关闭 Mingbu 客户端时忽略: {e}")
        logger.info("[mingbu] 神秘朋友已卸载")


_CORE: Optional[MingbuCore] = None


def get_core() -> MingbuCore:
    """返回（按需创建）共享内核单例。"""
    global _CORE
    if _CORE is None:
        _CORE = MingbuCore()
    return _CORE


class MingbuStar(
    LLMTextMixin,
    RelationshipMixin,
    PersonaContextMixin,
    GroupChatMixin,
    ProfileMixin,
    ConversationContextMixin,
    HealthProfileMixin,
    LLMRequestHookMixin,
    Star,
):
    """所有命卜 Star 的基类：持有共享内核 + 混入全部助手方法。

    本类刻意保持极薄——只做两件事：

    1. **绑定共享内核**。所有 Star 复用同一个 ``MingbuCore`` 单例，
       避免重复初始化引擎与 HTTP 连接池。
    2. **属性代理**。``__getattr__`` 把引擎属性访问转发给内核，于是
       ``self.memory`` / ``self.persona`` / ``self.social`` 等在各 Star 与
       各 mixin 中都能直接使用。

    30 个共享助手方法已按职责迁出到 ``star_mixins/`` 子包（见该包文档）。
    mixin 必须排在 ``Star`` 之前，否则 AstrBot 基类的同名方法会遮蔽实现。
    """

    def __init__(self, context: Context, config: Optional[dict] = None):
        try:
            super().__init__(context, config)
        except Exception:
            super().__init__(context)
        # AstrBot 官方插件模板：Star 基类不会自动保存 config 到 self.config，
        # 需插件显式持有（官方文档：self.config = config）。这里必须显式赋值，
        # 否则后台配置页 _sync_astrbot_static_config 拿不到 AstrBotConfig，
        # save_config() 回写静默跳过 → AstrBot 原生「插件配置」页永远不更新。
        self.config = config
        self.core = get_core()
        self.core.configure(config)
        self._config = self.core._config


    def __getattr__(self, name):
        """动态代理所有引擎属性到内核。"""
        if hasattr(self.core, name):
            return getattr(self.core, name)
        raise AttributeError(f"'MingbuStar' object has no attribute '{name}'")
