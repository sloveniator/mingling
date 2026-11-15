"""stars/apis/ -- 管理面板 Web API 按领域拆分（建议①）
==================================================
拆分前 ``stars/admin.py`` 是 792 行、31 路由、7 个领域的上帝类。
现在：

* ``_base.py``     : 惰性 web 包装 + 参数/模块/异常装饰器 + KV 命名空间护栏
* ``_schema.py``   : 配置校验复用 ``_conf_schema.json``（建议④）
* ``overview.py``  : stats / users / user / graph / recent_activity /
                     trigger / token_stats
* ``system.py``    : kv / kv_set / config
* ``collection.py``: 角色收集与角色管理
* ``slang.py``     : 群黑话
* ``conversation.py``: 对话检视
* ``worldview.py`` : 世界观
* ``diagnostics.py``: 系统依赖状态与外部服务连通性诊断

每个模块是 ``MingbuStar`` 的 mixin（不 import core，只通过 ``self.<engine>``
鸭子类型访问引擎，与 ``star_mixins/`` 同一套约定，因此不构成循环依赖）。
``StarAdmin`` 多继承这些 mixin，路由表 ``specs`` 仍集中在 ``admin.py``，
新增端点 = 一个领域文件加一个方法。
"""

from ._base import MAX_LIST_USERS as MAX_LIST_USERS
from ._base import gather_limited as gather_limited
from ._schema import coerce_by_schema as coerce_by_schema
from ._schema import load_schema as load_schema
from .collection import CollectionApiMixin as CollectionApiMixin
from .conversation import ConversationApiMixin as ConversationApiMixin
from .diagnostics import DiagnosticsApiMixin as DiagnosticsApiMixin
from .identity import IdentityApiMixin as IdentityApiMixin
from .overview import OverviewApiMixin as OverviewApiMixin
from .slang import SlangApiMixin as SlangApiMixin
from .system import SystemApiMixin as SystemApiMixin
from .worldview import WorldviewApiMixin as WorldviewApiMixin
from .tts_voices import TtsVoicesApiMixin as TtsVoicesApiMixin

__all__ = [
    "MAX_LIST_USERS",
    "CollectionApiMixin",
    "ConversationApiMixin",
    "DiagnosticsApiMixin",
    "IdentityApiMixin",
    "OverviewApiMixin",
    "SlangApiMixin",
    "SystemApiMixin",
    "WorldviewApiMixin",
    "TtsVoicesApiMixin",
    "coerce_by_schema",
    "gather_limited",
    "load_schema",
]
