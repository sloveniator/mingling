"""
devtools/astrbot_stub.py -- AstrBot 运行时最小桩（测试与本地开发共用）
======================================================================
原先这套桩只存在于 tests/conftest.py 里，导致两个问题：

1. `astrbot.api.web` 从未被打桩，于是 `stars/admin.py` 的 31 个 Web API
   路由在本地根本无法执行——这也是它长期零测试覆盖的直接原因；
2. 想在本地起一个能点的管理面板，就得把 conftest 的桩再抄一遍。

因此把桩抽到这里，由 `tests/conftest.py` 与 `devtools/devserver.py` 共同引用。

对外接口：
    install_astrbot_stub()      安装 astrbot.* 桩模块（幂等）
    bootstrap_plugin_modules()  安装短名→包名重映射并预加载插件模块（幂等）
"""
from __future__ import annotations

import contextvars
import importlib.abc
import importlib.machinery
import importlib.util
import os
import sys
import types
from typing import Any, Dict, Optional

PLUGIN_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PKG_NAME = "mingling"
STARS_DIR = os.path.join(PLUGIN_DIR, "stars")


# ══════════════════════════════════════════════════════════════
# Web 层：请求上下文与响应包装
# ══════════════════════════════════════════════════════════════

class StubRequest:
    """一次 HTTP 请求的最小载体。"""

    def __init__(self, method: str = "GET",
                 query: Optional[Dict[str, str]] = None,
                 body: Any = None):
        self.method = method.upper()
        self.query = dict(query or {})
        self._body = body

    async def json(self, default: Any = None) -> Any:
        """读取 JSON 请求体；缺失时返回 default（与 AstrBot 行为一致）。"""
        return default if self._body is None else self._body


# 用 ContextVar 而非模块级全局：dev server 会并发处理请求，
# 用普通全局变量会让并发请求互相踩到对方的参数。
_CURRENT_REQUEST: contextvars.ContextVar[StubRequest] = contextvars.ContextVar(
    "mingbu_current_request"
)


def set_current_request(req: StubRequest):
    """绑定当前请求，返回 token 供 reset 使用。"""
    return _CURRENT_REQUEST.set(req)


def reset_current_request(token):
    _CURRENT_REQUEST.reset(token)


class _RequestProxy:
    """模拟 Quart 的请求上下文代理：属性访问时才解析当前请求。"""

    @property
    def method(self) -> str:
        return _CURRENT_REQUEST.get().method

    @property
    def query(self) -> Dict[str, str]:
        return _CURRENT_REQUEST.get().query

    async def json(self, default: Any = None) -> Any:
        return await _CURRENT_REQUEST.get().json(default)


class StubResponse:
    """handler 的返回值容器：dev server 与测试都直接读 payload。"""

    __slots__ = ("payload", "status_code")

    def __init__(self, payload: Any, status_code: int = 200):
        self.payload = payload
        self.status_code = status_code

    # 让测试可以直接 resp["key"]，省掉一层 .payload
    def __getitem__(self, item):
        return self.payload[item]

    def get(self, item, default=None):
        if isinstance(self.payload, dict):
            return self.payload.get(item, default)
        return default

    def __contains__(self, item):
        return isinstance(self.payload, dict) and item in self.payload

    def __repr__(self):
        return f"StubResponse({self.status_code}, {self.payload!r})"


def _json_response(payload: Any = None, **kw) -> StubResponse:
    return StubResponse(
        payload if payload is not None else {},
        int(kw.get("status_code", 200)),
    )


def _error_response(message: str = "", status_code: int = 400, **_kw) -> StubResponse:
    # 前端约定：res.status === "error" 即视为失败，读取 res.message
    return StubResponse({"status": "error", "message": str(message)}, status_code)


# ══════════════════════════════════════════════════════════════
# astrbot.* 桩安装
# ══════════════════════════════════════════════════════════════

def _real_astrbot_is_loaded() -> bool:
    """真实 AstrBot 是否已被本进程导入。

    区分依据：真模块由文件加载因而有 ``__file__``；本文件造的桩是
    ``types.ModuleType``，没有该属性。

    只判"已导入"而不判"已安装"是有意为之——开发机上装了 astrbot 但未导入时
    仍应允许打桩（测试进程就是这种情况），否则整套测试会在这些机器上罢工。
    """
    mod = sys.modules.get("astrbot")
    return mod is not None and getattr(mod, "__file__", None) is not None


def install_astrbot_stub() -> None:
    """安装 astrbot 最小桩模块（幂等）。

    必须在任何插件模块导入之前调用。

    Raises:
        RuntimeError: 检测到真实 AstrBot 运行时。桩里的 logger 是空实现
            （所有方法 ``pass``），一旦在生产环境顶掉真模块，插件会静默跑在
            空壳上、日志全部丢失且几乎无法排查。这条线必须硬失败，不能降级。
    """
    if _real_astrbot_is_loaded():
        raise RuntimeError(
            "[mingbu] 拒绝安装 astrbot 测试桩：当前进程已加载真实 AstrBot "
            f"({sys.modules['astrbot'].__file__})。\n"
            "devtools/ 仅供开发与测试使用，绝不可在插件运行时被导入。"
        )

    if "astrbot" in sys.modules and "astrbot.api.web" in sys.modules:
        return

    if "astrbot" not in sys.modules:
        _astrbot = types.ModuleType("astrbot")
        _api = types.ModuleType("astrbot.api")
        _event = types.ModuleType("astrbot.api.event")
        _components = types.ModuleType("astrbot.api.message_components")
        _provider = types.ModuleType("astrbot.api.provider")
        _star = types.ModuleType("astrbot.api.star")
        _core = types.ModuleType("astrbot.core")
        _core_provider = types.ModuleType("astrbot.core.provider")

        class _Logger:
            def info(self, *a, **k): pass
            def warning(self, *a, **k): pass
            def debug(self, *a, **k): pass
            def error(self, *a, **k): pass

        _api.logger = _Logger()  # type: ignore[attr-defined]

        # ── 消息组件 ──
        # 真实 AstrBot 里这些类定义在 astrbot.api.message_components，
        # astrbot.api.event 只是再导出一次。桩必须照做：两条路径拿到的
        # 若不是同一个类，stt.py 的 `isinstance(comp, _Record)` 就会对着
        # 同名的两个类返回 False，语音识别整条链路静默失效。
        class _Component:
            """组件基类：统一提供 ``type``（AstrBot 用它做链上分派）。"""

            type = "Component"

            def __repr__(self):
                return f"{type(self).__name__}({self.__dict__!r})"

        class Plain(_Component):
            type = "Plain"

            def __init__(self, text: str = "", **kw):
                self.text = text
                self.__dict__.update(kw)

        class Record(_Component):
            type = "Record"

            def __init__(self, file: str = "", url: str = "", **kw):
                self.file = file
                self.url = url
                self.__dict__.update(kw)

        class Image(_Component):
            type = "Image"

            def __init__(self, file: str = "", url: str = "", **kw):
                self.file = file
                self.url = url
                self.__dict__.update(kw)

        class At(_Component):
            type = "At"

            def __init__(self, qq: str = "", **kw):
                self.qq = qq
                self.__dict__.update(kw)

        class MessageChain:
            """消息链：一串组件。``proactive.py`` 会构造 ``MessageChain([Plain(...)])``。"""

            def __init__(self, chain=None, **kw):
                self.chain = list(chain or [])
                self.__dict__.update(kw)

            def append(self, comp):
                self.chain.append(comp)
                return self

            def message(self, text: str):
                self.chain.append(Plain(text))
                return self

            def __iter__(self):
                return iter(self.chain)

            def __len__(self):
                return len(self.chain)

        for _name, _obj in (
            ("Plain", Plain), ("Record", Record), ("Image", Image),
            ("At", At), ("MessageChain", MessageChain),
            ("BaseMessageComponent", _Component),
        ):
            setattr(_components, _name, _obj)
            setattr(_event, _name, _obj)

        class MessageEventResult(MessageChain):
            """``@filter.llm_tool`` 处理器的返回类型。

            源码里只把它当类型标注用（``-> MessageEventResult``），但标注在
            函数定义时就要求名字可解析——桩里缺这一个名字，``stars/tools.py``
            连同导入它的 ``main.py`` 就整个加载不了。真实类支持链式调用，
            这里一并照做，免得将来源码改成构造它时又要回来补。
            """

            def use_t2i(self, *_a, **_k):
                return self

            def set_result_content_type(self, *_a, **_k):
                return self

        _event.MessageEventResult = MessageEventResult  # type: ignore[attr-defined]

        class EventMessageType:
            FRIEND_MESSAGE = "friend_message"
            GROUP_MESSAGE = "group_message"
            ALL = "all"

        _event.EventMessageType = EventMessageType  # type: ignore[attr-defined]

        class _Filter:
            """AstrBot ``@filter.*`` 装饰器族的最小忠实桩。

            真实 AstrBot 里：

            * **入口装饰器** (``on_message`` / ``command`` / ``regex`` /
              ``on_llm_request`` / ``on_llm_response`` / ``on_astrbot_loaded``
              / ``llm_tool`` …) 会在函数上打 ``__astrbot_event_type__`` 标记，
              并登记进 ``star_handlers_registry``；
            * **条件装饰器** (``event_message_type`` / ``permission_type`` /
              ``platform_adapter_type`` …) 只往 ``__astrbot_event_filters__``
              追加过滤条件，**不会**单独创建处理器。

            只有带入口标记的函数才会被 AstrBot 事件分发挂载。本桩忠实复刻
            这一语义，使「处理器是否被注册」可被测试断言——这正是
            ``stars/enhancements.py`` 曾长期藏着的 P0（5 个处理器只挂了
            ``event_message_type`` 条件、缺 ``on_message`` 入口，导致生产
            零触发）。
            """

            # ── 入口装饰器：打事件类型标记 ──
            # 注意：on_message 在 AstrBot 4.27+ 已被移除（4.27.2 实测不存在），
            # 该版本改以 event_message_type(EventMessageType.ALL) 作为「捕获
            # 全部消息」的入口。本桩据此移除 on_message，使其在
            # getattr(filter, 'on_message', None) 时返回 None（见 __getattr__），
            # 从而忠实复刻 4.27.2 的注册语义，避免测试在「旧路径」上假绿、
            # 漏掉 v4.8.12 那种「部署环境导入即崩」的回归。

            @staticmethod
            def on_llm_request(*a, **k):
                def deco(f):
                    setattr(f, "__astrbot_event_type__", "OnLLMRequest")
                    return f
                return deco

            @staticmethod
            def on_llm_response(*a, **k):
                def deco(f):
                    setattr(f, "__astrbot_event_type__", "OnLLMResponse")
                    return f
                return deco

            @staticmethod
            def on_astrbot_loaded(*a, **k):
                def deco(f):
                    setattr(f, "__astrbot_event_type__", "OnAstrbotLoaded")
                    return f
                return deco

            @staticmethod
            def command(*a, **k):
                def deco(f):
                    setattr(f, "__astrbot_event_type__", "Command")
                    return f
                return deco

            @staticmethod
            def command_group(*a, **k):
                def deco(f):
                    setattr(f, "__astrbot_event_type__", "CommandGroup")
                    return f
                return deco

            @staticmethod
            def regex(*a, **k):
                def deco(f):
                    setattr(f, "__astrbot_event_type__", "Regex")
                    return f
                return deco

            @staticmethod
            def llm_tool(*a, **k):
                def deco(f):
                    setattr(f, "__astrbot_event_type__", "LLMTool")
                    return f
                return deco

            # ── 条件装饰器：只追加过滤条件，不创建处理器 ──
            @staticmethod
            def event_message_type(et, *a, **k):
                def deco(f):
                    # 在 4.27+ 真实 AstrBot 里，event_message_type 本身就是
                    # 「消息处理器入口」：仅挂它就足以让函数被事件分发挂载。
                    # 忠实复刻 —— 若尚无入口标记，则补上 OnMessage 标记。
                    if not getattr(f, "__astrbot_event_type__", None):
                        setattr(f, "__astrbot_event_type__", "OnMessage")
                    filters = list(getattr(f, "__astrbot_event_filters__", []))
                    filters.append(("event_message_type", et))
                    setattr(f, "__astrbot_event_filters__", filters)
                    return f
                return deco

            @staticmethod
            def permission_type(*a, **k):
                def deco(f):
                    return f
                return deco

            @staticmethod
            def platform_adapter_type(*a, **k):
                def deco(f):
                    return f
                return deco

            @staticmethod
            def _identity(*a, **k):
                def deco(f):
                    return f
                return deco

            def __getattr__(self, name):
                # 只有真实不存在的属性才会走到这里；下划线开头的留给内部协议
                if name.startswith("__"):
                    raise AttributeError(name)
                # on_message 在 4.27+ 已移除：模拟真实缺失（getattr 默认返回
                # None），让插件代码的 getattr(filter, 'on_message', None) 走到
                # 「无入口」分支、改以 event_message_type 作入口。
                if name == "on_message":
                    raise AttributeError(name)
                return self._identity

        _event.filter = _Filter()  # type: ignore[attr-defined]
        # 真实 AstrBot 中 EventMessageType 可通过 filter.EventMessageType 取到
        # （插件代码即如此访问：getattr(filter, 'EventMessageType', None)）。
        # 必须挂在 filter 实例上，否则 _EMT_ALL / _EMT_PRIVATE 解析为 None，
        # _message_handler 就不挂 event_message_type 条件，与本桩的注册语义
        # 自相矛盾。
        _event.filter.EventMessageType = EventMessageType

        class AstrMessageEvent:
            def __init__(self, *a, **kw):
                self.message_str = kw.get("message_str", "")
                self.unified_msg_origin = kw.get("unified_msg_origin", "")
                self.session_id = kw.get("session_id", "")
                self.message_obj = types.SimpleNamespace(
                    type=kw.get("msg_type", "friend_message"),
                    group_id=kw.get("group_id", ""),
                    message=[],
                )

            def get_sender_id(self): return "test_user"
            def get_sender_name(self): return "测试用户"
            def stop_event(self): pass

        _event.AstrMessageEvent = AstrMessageEvent  # type: ignore[attr-defined]

        class ProviderRequest:
            def __init__(self, prompt="", session_id="", **kw):
                self.prompt = prompt
                self.session_id = session_id
                self.extra_user_content_parts = []
                self.temperature = 0.7
                self.max_tokens = 0
                self.system_prompt = ""
                self.__dict__.update(kw)

        class LLMResponse:
            def __init__(self, text=""):
                self.completion_text = text
                self.response_text = text
                self.role = "assistant"
                self.cached_tokens = 0
                self.cache_read_tokens = 0
                self.result_chain = None

        # 桩早期用的名字，保留以免测试改名；必须是同一个类而不是两个同名类，
        # 否则 stars/enhancements.py 的 `isinstance(resp, LLMResponse)` 会
        # 对 Context.llm_request() 的返回值判否，TTS 分支静默走空。
        ProviderResponse = LLMResponse

        _provider.ProviderRequest = ProviderRequest  # type: ignore[attr-defined]
        _provider.ProviderResponse = ProviderResponse  # type: ignore[attr-defined]
        _provider.LLMResponse = LLMResponse  # type: ignore[attr-defined]
        # 不同 AstrBot 版本 LLMResponse 的位置不同，源码写了 api → core 的
        # 兜底链；两条路径都指向同一个类，兜底命中时行为一致。
        _core_provider.LLMResponse = LLMResponse  # type: ignore[attr-defined]
        _core_provider.ProviderRequest = ProviderRequest  # type: ignore[attr-defined]
        _core.provider = _core_provider  # type: ignore[attr-defined]

        class Context:
            def __init__(self): pass

            async def llm_request(self, req):
                return ProviderResponse("测试回复")

            def get_runtime_id(self): return "test"

        class Star:
            def __init__(self, context=None, config=None):
                self.context = context or Context()
                self._config = config or {}

            async def get_kv_data(self, key, default=None): return default
            async def put_kv_data(self, key, value): pass
            async def initialize(self): pass
            async def terminate(self): pass
            def register_llm_tool(self, *a, **k): pass

        _star.Context = Context  # type: ignore[attr-defined]
        _star.Star = Star  # type: ignore[attr-defined]

        _astrbot.api = _api  # type: ignore[attr-defined]
        _astrbot.core = _core  # type: ignore[attr-defined]
        _api.event = _event  # type: ignore[attr-defined]
        _api.message_components = _components  # type: ignore[attr-defined]
        _api.provider = _provider  # type: ignore[attr-defined]
        _api.star = _star  # type: ignore[attr-defined]

        sys.modules["astrbot"] = _astrbot
        sys.modules["astrbot.api"] = _api
        sys.modules["astrbot.api.event"] = _event
        sys.modules["astrbot.api.message_components"] = _components
        sys.modules["astrbot.api.provider"] = _provider
        sys.modules["astrbot.api.star"] = _star
        sys.modules["astrbot.core"] = _core
        sys.modules["astrbot.core.provider"] = _core_provider

    # ── astrbot.api.web：原 conftest 缺失的部分 ──
    if "astrbot.api.web" not in sys.modules:
        _web = types.ModuleType("astrbot.api.web")
        _web.request = _RequestProxy()  # type: ignore[attr-defined]
        _web.json_response = _json_response  # type: ignore[attr-defined]
        _web.error_response = _error_response  # type: ignore[attr-defined]
        _web.Request = StubRequest  # type: ignore[attr-defined]
        _web.Response = StubResponse  # type: ignore[attr-defined]
        sys.modules["astrbot.api.web"] = _web
        sys.modules["astrbot.api"].web = _web  # type: ignore[attr-defined]


# ══════════════════════════════════════════════════════════════
# 插件模块加载：短名 ↔ 包名双向可用
# ══════════════════════════════════════════════════════════════

_TOP_LEVEL_MODULES = {
    f[:-3] for f in os.listdir(PLUGIN_DIR)
    if f.endswith(".py") and not f.startswith("_")
}

_TOP_LEVEL_PACKAGES = {
    d for d in os.listdir(PLUGIN_DIR)
    if os.path.isdir(os.path.join(PLUGIN_DIR, d))
    and not d.startswith("_")
    and os.path.isfile(os.path.join(PLUGIN_DIR, d, "__init__.py"))
}


class _ShortNameLoader(importlib.abc.Loader):
    """透传真实 loader，并在模块执行完成后补登记短名。

    import 机制只会把模块登记到 ``spec.name``（这里是包内全名），短名不会
    自动进 ``sys.modules``。不补这一步，下次 ``import X`` 又得重新走 finder，
    多绕一圈不说，还容易踩进「已加载」分支的各种边角。
    """

    def __init__(self, inner, short_name):
        self._inner = inner
        self._short_name = short_name

    def create_module(self, spec):
        return self._inner.create_module(spec)

    def exec_module(self, module):
        self._inner.exec_module(module)
        sys.modules.setdefault(self._short_name, module)

    def __getattr__(self, name):
        # get_source / get_filename 等仍交给真实 loader（inspect.getsource 要用）
        return getattr(self._inner, name)


class _RemapSpec(importlib.machinery.ModuleSpec):
    """包装 spec，使模块以包内全名加载。

    有两处容易漏掉的细节：

    1. ``ModuleSpec.__init__`` 的 ``has_location`` 默认为 False，模块因此拿
       不到 ``__file__``。``main.py`` 里那句
       ``os.path.dirname(os.path.abspath(__file__))`` 会当场 NameError——
       而且是在导入期抛出、又被 ``_load_module`` 的 except 吞掉，从外面只看到
       「main 没加载」，看不到原因。所以必须原样继承原 spec 的定位信息。
    2. 加载后需要补登记短名，见 ``_ShortNameLoader``。
    """

    def __init__(self, original_spec, short_name, full_name):
        super().__init__(
            full_name,
            _ShortNameLoader(original_spec.loader, short_name),
            origin=original_spec.origin,
        )
        self._short_name = short_name
        self._original_spec = original_spec
        self.submodule_search_locations = original_spec.submodule_search_locations
        self.has_location = original_spec.has_location
        self.cached = original_spec.cached


class _AliasLoader(importlib.abc.Loader):
    """把短名解析到「已加载的那个模块对象本身」，而不是照源码再跑一遍。

    这里曾经直接把已加载模块的 ``SourceFileLoader`` 交回给 import 机制，
    看着像是复用，实则机制会 ``module_from_spec`` 新建一个模块对象再
    ``exec_module`` 执行一遍源码——同一份代码被加载成两份，模块级单例
    （``core._CORE``、预编译正则、常量集合……）随之割裂成两套，等值但不同一。
    ``create_module`` 返回既有对象 + ``exec_module`` 空实现，才是真别名。
    """

    def __init__(self, module):
        self._module = module

    def create_module(self, spec):
        return self._module

    def exec_module(self, module):
        return None


def _alias_spec(short_name, mod):
    """为已加载的模块对象造一个「短名指向它本身」的 spec。"""
    is_pkg = hasattr(mod, "__path__")
    spec = importlib.util.spec_from_loader(
        short_name, _AliasLoader(mod),
        origin=getattr(mod, "__file__", None), is_package=is_pkg,
    )
    if spec is not None and is_pkg:
        spec.submodule_search_locations = list(mod.__path__)
    return spec


class _PluginRemapFinder(importlib.abc.MetaPathFinder):
    """将短名导入（import dream）重映射为包内导入（mingling.dream）。

    好处：测试可以继续写 `import dream`，而源码里的相对导入
    `from .astrology import ...` 同样能解析，且两者指向同一个模块对象。
    """

    def find_spec(self, fullname, path=None, target=None):
        if "." in fullname:
            return None
        if fullname in _TOP_LEVEL_MODULES:
            mapped_name = f"{PKG_NAME}.{fullname}"
            if mapped_name in sys.modules:
                return _alias_spec(fullname, sys.modules[mapped_name])
            file_path = os.path.join(PLUGIN_DIR, f"{fullname}.py")
            if not os.path.exists(file_path):
                return None
            spec = importlib.util.spec_from_file_location(mapped_name, file_path)
            if spec is None:
                return None
            return _RemapSpec(spec, short_name=fullname, full_name=mapped_name)
        if fullname in _TOP_LEVEL_PACKAGES:
            mapped_name = f"{PKG_NAME}.{fullname}"
            if mapped_name in sys.modules:
                return _alias_spec(fullname, sys.modules[mapped_name])
            pkg_dir = os.path.join(PLUGIN_DIR, fullname)
            init_path = os.path.join(pkg_dir, "__init__.py")
            if not os.path.isdir(pkg_dir) or not os.path.isfile(init_path):
                return None
            spec = importlib.util.spec_from_file_location(
                mapped_name, init_path, submodule_search_locations=[pkg_dir]
            )
            if spec is None:
                return None
            return _RemapSpec(spec, short_name=fullname, full_name=mapped_name)
        return None


# 依赖从浅到深，尽量避开循环导入
_TOP_MODULE_ORDER = [
    "_version", "formatters", "astrology", "client", "tarot",
    "health", "kv_store", "group_slang", "token_monitor", "provider_router",
    "worldview_adapter", "memory_core", "persona",
    "silent_divination", "dream", "growth", "news_search", "social",
    "character_pool", "character_collection", "conversation_inspector",
    "proactive", "core", "main",
]


def _is_plugin_subpackage(mod, pkg_name) -> bool:
    """判断已加载的扁平模块是否就是本插件目录下的那个子包。

    只比对 ``__file__`` 的所在目录，不看名字——同名但来自 site-packages
    的第三方包（比如真有个叫 ``hooks`` 的库）不能被误当成自己人。
    """
    path = getattr(mod, "__file__", None)
    if not path:
        return False
    expected = os.path.join(PLUGIN_DIR, pkg_name, "__init__.py")
    return os.path.normcase(os.path.abspath(path)) == os.path.normcase(expected)


def _load_module(name, file_path, package=None):
    try:
        spec = importlib.util.spec_from_file_location(name, file_path)
        if not spec or not spec.loader:
            return None
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        if package:
            mod.__package__ = package
        spec.loader.exec_module(mod)
        return mod
    except Exception:
        # 半初始化的模块留在 sys.modules 里会污染后续导入
        sys.modules.pop(name, None)
        return None


def bootstrap_plugin_modules() -> None:
    """注册包模块 + import finder，并预加载全部插件模块（幂等）。"""
    if PKG_NAME not in sys.modules:
        pkg = types.ModuleType(PKG_NAME)
        pkg.__path__ = [PLUGIN_DIR]
        pkg.__package__ = PKG_NAME
        sys.modules[PKG_NAME] = pkg

    if os.path.isdir(STARS_DIR):
        stars_pkg_name = f"{PKG_NAME}.stars"
        if stars_pkg_name not in sys.modules:
            spkg = types.ModuleType(stars_pkg_name)
            spkg.__path__ = [STARS_DIR]
            spkg.__package__ = stars_pkg_name
            sys.modules[stars_pkg_name] = spkg
            sys.modules["stars"] = spkg

    if not any(isinstance(f, _PluginRemapFinder) for f in sys.meta_path):
        sys.meta_path.insert(0, _PluginRemapFinder())

    # 子包（hooks / counseling …）：交给标准导入机制加载真实的 __init__.py，
    # 再补注册短名别名（`import hooks` / `import counseling`），避免被当成
    # 顶层包二次加载、导致「同一个类被加载成两个对象」。子模块别名一并注册，
    # 使 `import hooks.crisis` / `import counseling.tracker` 也指向同一对象。
    for pkg_name in sorted(_TOP_LEVEL_PACKAGES):
        full_pkg = f"{PKG_NAME}.{pkg_name}"
        if full_pkg not in sys.modules:
            # 引导代码本身可能已经用扁平名加载过某个子包（历史上
            # tests/conftest.py 的 `from devtools.astrbot_stub import ...`
            # 就是如此）。此时再 import_module(full_pkg) 会照源码跑第二遍，
            # 于是同一个包有两份。反过来把全名指向既有对象才对。
            flat = sys.modules.get(pkg_name)
            if flat is not None and _is_plugin_subpackage(flat, pkg_name):
                sys.modules[full_pkg] = flat
                flat_prefix = pkg_name + "."
                for mod_name, mod in list(sys.modules.items()):
                    if mod_name.startswith(flat_prefix):
                        sys.modules.setdefault(
                            f"{PKG_NAME}.{mod_name}", mod
                        )
            else:
                try:
                    importlib.import_module(full_pkg)
                except Exception:
                    continue
        if full_pkg not in sys.modules:
            continue
        pkg = sys.modules[full_pkg]
        sys.modules.setdefault(pkg_name, pkg)
        prefix = full_pkg + "."
        for mod_name, mod in list(sys.modules.items()):
            if mod_name.startswith(prefix):
                sys.modules.setdefault(mod_name.replace(full_pkg, pkg_name), mod)

    for mod_name in _TOP_MODULE_ORDER:
        file_path = os.path.join(PLUGIN_DIR, f"{mod_name}.py")
        if not os.path.exists(file_path):
            continue
        full_name = f"{PKG_NAME}.{mod_name}"
        # 已被别的模块连带导入过：仍要补上短名别名，否则 `import core`
        # 会绕过 finder 再加载一份，模块级单例被割裂成两个对象。
        if full_name in sys.modules:
            sys.modules.setdefault(mod_name, sys.modules[full_name])
            continue
        mod = _load_module(full_name, file_path, package=PKG_NAME)
        if mod is not None:
            sys.modules[mod_name] = mod

    # _TOP_MODULE_ORDER 只是「按依赖深浅排的加载顺序」，不是全集——新增顶层
    # 模块（如 constants.py）不会自动进这份手工清单。它们往往已被上面的模块
    # 连带导入过，只是短名别名没人登记；等测试里第一次 `import constants`，
    # 就会走 finder 的已加载分支，在旧实现下被再执行一遍变成第二份。
    # 这里按磁盘实际情况兜底回填别名：只登记已加载的，不主动加载新模块，
    # 避免把 verify_hooks.py 这类脚本型模块也执行一遍。
    for mod_name in sorted(_TOP_LEVEL_MODULES):
        full_name = f"{PKG_NAME}.{mod_name}"
        if full_name in sys.modules:
            sys.modules.setdefault(mod_name, sys.modules[full_name])

    if os.path.isdir(STARS_DIR):
        stars_pkg = f"{PKG_NAME}.stars"
        for f in sorted(os.listdir(STARS_DIR)):
            if not f.endswith(".py") or f.startswith("_"):
                continue
            mod_name = f[:-3]
            full_name = f"{stars_pkg}.{mod_name}"
            short_name = f"stars.{mod_name}"
            # 同上：main.py 会连带导入所有 stars 子模块，若在此处直接
            # continue，短名别名就永远注册不上，`from stars.admin import X`
            # 会以 __package__="stars" 重新加载，相对导入 `from ..core`
            # 随即报 "attempted relative import beyond top-level package"。
            if full_name in sys.modules:
                sys.modules.setdefault(short_name, sys.modules[full_name])
                continue
            mod = _load_module(full_name, os.path.join(STARS_DIR, f"{mod_name}.py"),
                               package=stars_pkg)
            if mod is not None:
                sys.modules[short_name] = mod

        # stars 下的嵌套子包（apis/ …）：上面的循环只扫 *.py，目录会被跳过。
        # 这些子包由 stars/admin.py 连带导入，全名已在 sys.modules，但短名
        # 别名没人登记——一旦测试里写 `from stars.apis._base import X`，
        # Python 会沿 stars.__path__ 把 apis 当新包再执行一遍，
        # 于是 MAX_LIST_USERS 之类的模块级常量出现两份互不相等的副本。
        stars_prefix = stars_pkg + "."
        for mod_name, mod in list(sys.modules.items()):
            if mod_name.startswith(stars_prefix):
                sys.modules.setdefault(
                    "stars." + mod_name[len(stars_prefix):], mod
                )

    # 子包（hooks / counseling …）的预加载与短名别名已在上方统一处理。


def bootstrap_all() -> None:
    """一次性完成桩安装与模块预加载。"""
    install_astrbot_stub()
    try:
        bootstrap_plugin_modules()
    except Exception:
        # 预加载失败不致命，真正导入时会报出具体错误
        pass
