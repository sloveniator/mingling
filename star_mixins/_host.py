"""
star_mixins/_host.py -- Mixin 的宿主属性类型声明（仅类型层，零运行时行为）
==========================================================================

``star_mixins/`` 下的各 Mixin 由 ``MingbuStar``（``core.py``）按 MRO 组合。
方法体内的 ``self.persona`` / ``self.memory`` / ``self.get_kv_data`` 等访问，
运行时由 ``MingbuStar.__getattr__`` 代理到 ``MingbuCore``，或由具体宿主
（生产为 AstrBot 宿主注入的 Star、测试为各 harness）提供——**在 Mixin 裸类上
mypy 静态不可见**，于是产生大量 ``attr-defined`` 误报（非真 bug）。

本模块用**类级注解**把这些宿主属性显式声明出来，供 mypy 静态识别：

- 纯类型层：声明不产生任何运行时绑定，不写 ``__init__``，不 import ``core``，
  因此不破坏 ``tests/test_star_mixins_package.py`` 钉死的 7 条不变量
  （无状态、无 ``__init__``、不 import core、MRO 顺序、无同名撞车等）。
- 各 Mixin 继承 :class:`MixinHost` 即可让 mypy 看到 ``self.<host_attr>``。
  继承只是把注解并入 MRO，不改任何运行时行为（无方法、无状态）。

类型上统一用 ``Any``：宿主属性的真实类型由 ``MingbuCore`` 决定，而 Mixin 只
做鸭子类型访问；用 ``Any`` 既消除误报，又避免与 ``MingbuCore`` 的具体声明在
MRO 上产生「incompatible definition」冲突。这是刻意的工程取舍，不是偷懒。
"""

from typing import Any, Callable, Coroutine


class MixinHost:
    """各 Mixin 的宿主属性类型声明基类（仅注解，无运行时行为）。

    分两组：

    1. **引擎属性**：运行时由 ``MingbuStar.__getattr__`` 代理到 ``MingbuCore``。
    2. **KV 方法**：由宿主 Star 直接提供（生产宿主注入、测试 harness 各自实现），
       不经 ``__getattr__``，故单独声明。
    """

    # ── 1. 引擎属性（经 MingbuStar.__getattr__ 代理到 MingbuCore）─────────────
    context: Any            # AstrBot Context
    core: Any               # MingbuCore 单例（__init__ 显式赋值）
    config: Any             # AstrBotConfig（MingbuStar.__init__ 显式赋值）
    _config: Any            # 运行时配置 dict（core._config）
    persona: Any            # PersonaState
    memory: Any             # MemoryCore
    social: Any             # SocialGraph
    dream: Any              # DreamEngine
    growth: Any             # GrowthEngine
    proactive: Any          # ProactiveEngine
    character_pool: Any     # CharacterPool
    character_collection: Any  # CharacterCollection
    token_monitor: Any      # TokenMonitor
    provider_router: Any    # ProviderRouter
    background_watch: Any   # BackgroundWatch
    self_evolution: Any     # SelfEvolution

    # ── 2. KV / 跨 Mixin 协作方法（运行时由宿主或同包其它 Mixin 的 MRO 提供）─────
    # 用属性注解（而非方法体）声明：既消除 empty-body，也避免与宿主真实实现
    # 在 MRO 上判为重定义（no-redef）。运行时 get_kv_data/put_kv_data 由宿主 Star
    # 提供，_get_bond 由同包 RelationshipMixin 提供，本基类均不实现。
    get_kv_data: Callable[..., Coroutine[Any, Any, Any]]
    put_kv_data: Callable[..., Coroutine[Any, Any, None]]
    _get_bond: Callable[[str, str], Coroutine[Any, Any, Any]]
