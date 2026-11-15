"""features.py -- 能力注册表（声明式惰性构建）
============================================
把 core.ensure() 里「按配置开关惰性构建引擎」的一长串手动 if 分支收敛为
声明式注册表。

收益：
  - 新增一个能力只需在此登记一条 ``@feature(...)``，无需改动 core.ensure；
  - 能力的「开关配置项 + 默认值 + 如何构建」内聚到一处，单一事实源；
  - 为将来支持「用户级能力开关」预留了扩展点（只需把 enabled_if 改为读
    用户级配置）。

builder 一律在函数体内做懒导入，避免 features 模块在导入期与 core 形成循环依赖。
"""
from __future__ import annotations

from typing import Callable, Dict


class _Feature:
    """一条能力登记。

    ``config_key`` 保留开关配置项名以便自省（测试逐项验证降级、后台面板
    列出能力开关都要用）。复合条件能力（传了 ``enabled_if``）没有单一开关，
    此字段为 None。
    """

    def __init__(self, name: str, build: Callable, enabled_if: Callable[["dict"], bool],
                 config_key: str = None, default: bool = True):
        self.name = name
        self.build = build
        self.enabled_if = enabled_if
        self.config_key = config_key
        self.default = default


_REGISTRY: Dict[str, _Feature] = {}


def feature(name: str, *, config_key: str = None, default: bool = True,
            enabled_if: Callable[["dict"], bool] = None):
    """能力登记装饰器工厂。

    用法::

        @feature("dream", config_key="dream_engine_enabled", default=True)
        async def _build_dream(core, owner, cfg):
            from .dream import DreamEngine
            core.dream = DreamEngine(owner, core.memory)

    Args:
        name: 能力名（用于日志）。
        config_key: 启用开关对应的配置项；省略时配合 default 始终启用。
        default: config_key 缺省时的启用默认值。
        enabled_if: 自定义启用判定（覆盖 config_key/default），用于复合条件
            （如 news 同时看 news_enabled / proactive_search_enabled）。
    """
    def decorator(build: Callable) -> Callable:
        if enabled_if is None:
            key, dflt = config_key, default

            def _is_enabled(cfg: "dict") -> bool:
                return cfg.get(key, dflt) if key else dflt

            introspect_key = config_key
        else:
            _is_enabled = enabled_if
            # 复合条件无单一开关，不做自省承诺。
            introspect_key = None
        _REGISTRY[name] = _Feature(name, build, _is_enabled,
                                   config_key=introspect_key, default=default)
        return build
    return decorator


def get_features() -> Dict[str, _Feature]:
    """返回（按登记顺序）能力注册表。"""
    return _REGISTRY


# ============================================================
# 能力登记（顺序即原 core.ensure 的构建顺序）
# ============================================================

@feature("dream", config_key="dream_engine_enabled", default=True)
async def _build_dream(core, owner, cfg):
    from .dream import DreamEngine
    core.dream = DreamEngine(owner, core.memory)


@feature("growth", config_key="growth_engine_enabled", default=True)
async def _build_growth(core, owner, cfg):
    from .growth import GrowthEngine
    core.growth = GrowthEngine(owner, core.memory)


@feature("proactive", config_key="proactive_enabled", default=True)
async def _build_proactive(core, owner, cfg):
    from .proactive import ProactiveEngine
    core.proactive = ProactiveEngine(owner)
    # 应用配置中的间隔参数
    interval_min = cfg.get("proactive_interval_min", 60)
    if hasattr(core.proactive, "_interval_min"):
        core.proactive._interval_min = interval_min


@feature("background_watch", config_key="background_watch_enabled", default=True)
async def _build_background_watch(core, owner, cfg):
    if not core.api:
        return
    from .background_watch import BackgroundWatcher
    core.background_watch = BackgroundWatcher(owner)
    await core.background_watch.register_jobs()


@feature("self_evolution", config_key="self_evolution_enabled", default=False)
async def _build_self_evolution(core, owner, cfg):
    if not core.memory:
        return
    from .self_evolution import SelfEvolution
    core.self_evolution = SelfEvolution(owner, core.memory)
    await core.self_evolution.register_jobs()


@feature("social", config_key="social_graph_enabled", default=True)
async def _build_social(core, owner, cfg):
    from .social import SocialGraph
    core.social = SocialGraph(owner)


@feature("character_collection", config_key="character_collection_enabled", default=True)
async def _build_character(core, owner, cfg):
    from .character_collection import CharacterCollection
    from .character_pool import CharacterPool
    core.character_pool = CharacterPool(owner)
    await core.character_pool.initialize()
    core.character_collection = CharacterCollection(owner)


@feature("token_monitor", config_key="token_monitor_enabled", default=True)
async def _build_token_monitor(core, owner, cfg):
    from .token_monitor import TokenMonitor
    core.token_monitor = TokenMonitor(owner)
    await core.token_monitor.initialize()


@feature(
    "news",
    enabled_if=lambda cfg: bool(
        cfg.get("news_enabled", False) or cfg.get("proactive_search_enabled", False)
    ),
)
async def _build_news(core, owner, cfg):
    from .news_search import NewsSearchEngine
    core.news_search = NewsSearchEngine(owner)


@feature("slang", config_key="group_slang_enabled", default=True)
async def _build_slang(core, owner, cfg):
    from .group_slang import GroupSlangManager
    core.slang_manager = GroupSlangManager(owner)


@feature("conversation_inspector", config_key="conversation_inspection_enabled", default=True)
async def _build_conversation_inspector(core, owner, cfg):
    from .conversation_inspector import ConversationInspector
    inspector = ConversationInspector(owner)
    # 应用配置中的权重阈值。此前这里包了一层 hasattr 守卫，而引擎从未定义
    # 该字段，于是守卫恒假、配置项静默失效；现在 weight_threshold 是
    # ConversationInspector 的实例字段，直接赋值即可。
    inspector.weight_threshold = cfg.get(
        "weight_threshold", inspector.weight_threshold
    )
    core.conversation_inspector = inspector
