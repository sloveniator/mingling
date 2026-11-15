"""
star_mixins -- MingbuStar 的共享助手方法（按职责切分）
=====================================================
``core.py`` 中 ``MingbuStar`` 原本堆了 30 个助手方法、约 557 行，与
``MingbuCore``（内核生命周期）混在同一个文件里。本子包把这些方法按职责
拆成 8 个 mixin，``MingbuStar`` 退化为「组合 mixin + 持有内核」的薄基类。

## 设计约定

1. **mixin 不 import core**。方法体内的 ``self.persona`` / ``self.memory`` /
   ``self.get_kv_data`` 等访问，运行时由 ``MingbuStar.__getattr__`` 代理到
   ``MingbuCore``（鸭子类型注入）。共享常量则来自 ``constants`` 模块。
   因此依赖方向是单向的：``constants ← star_mixins ← core``，无环。

2. **mixin 无状态、无 ``__init__``**。它们只提供方法；实例状态一律由
   ``MingbuCore`` 持有。这样组合顺序不影响行为，也不需要协作式 super()。

3. **MRO 顺序**：``class MingbuStar(<各 mixin>, Star)``，mixin 必须排在
   ``Star`` 之前，否则 AstrBot 基类的同名方法会遮蔽 mixin 实现。

## 模块划分

| 模块 | 类 | 职责 |
|---|---|---|
| ``llm`` | ``LLMTextMixin`` | 系统级 LLM 文本生成（防递归 + 超时 + Token 监控） |
| ``relations`` | ``RelationshipMixin`` | 用户关系读写、活跃用户索引（热路径） |
| ``persona_ctx`` | ``PersonaContextMixin`` | 人格块拼装、记忆语义召回与重排 |
| ``group`` | ``GroupChatMixin`` | 群聊 @ 检测、首次私聊 onboarding |
| ``profile`` | ``ProfileMixin`` | 用户档案、出生信息与校验 |
| ``conversation`` | ``ConversationContextMixin`` | 结构化轮次记录、话题追踪 |
| ``health`` | ``HealthProfileMixin`` | 健康档案存取 |
| ``llm_hook`` | ``LLMRequestHookMixin`` | LLM 请求钩子主体（driver → HookChain） |

``X as X`` 为 PEP 484 显式 re-export（ruff 原生识别为有意再导出）。
"""

from .conversation import ConversationContextMixin as ConversationContextMixin
from .group import GroupChatMixin as GroupChatMixin
from .health import HealthProfileMixin as HealthProfileMixin
from .llm import LLMTextMixin as LLMTextMixin
from .llm_hook import LLMRequestHookMixin as LLMRequestHookMixin
from .persona_ctx import PersonaContextMixin as PersonaContextMixin
from .profile import ProfileMixin as ProfileMixin
from .relations import RelationshipMixin as RelationshipMixin

# MingbuStar 的 mixin 组合顺序（不含末尾的 Star 基类）。
# 集中在此声明，便于测试断言 MRO 完整性。
STAR_MIXINS = (
    LLMTextMixin,
    RelationshipMixin,
    PersonaContextMixin,
    GroupChatMixin,
    ProfileMixin,
    ConversationContextMixin,
    HealthProfileMixin,
    LLMRequestHookMixin,
)

__all__ = [
    "ConversationContextMixin",
    "GroupChatMixin",
    "HealthProfileMixin",
    "LLMRequestHookMixin",
    "LLMTextMixin",
    "PersonaContextMixin",
    "ProfileMixin",
    "RelationshipMixin",
    "STAR_MIXINS",
]
