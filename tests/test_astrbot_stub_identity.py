"""
tests/test_astrbot_stub_identity.py -- 测试桩的「模块只加载一次」契约
=====================================================================

``devtools/astrbot_stub.py`` 让测试既能写 ``import core``（短名），也能让源码里
的 ``from .constants import ...``（包内相对导入）正常解析。这套双轨制有一个
必须守住的前提：**两条路径必须指向同一个模块对象。**

破坏它的后果是安静的。同一份源码被执行两遍后，模块级状态会分裂成两套：
``core._CORE`` 变成两个内核、预编译正则变成两份、``MINGPAN_METHODS`` 等值却
不同一、``isinstance`` 对着同名的两个类返回 False。测试可能仍然大面积通过，
只在某个恰好跨越两份副本的断言上莫名其妙地失败。

实际踩过两次：

1. 短名 ``import X`` 命中 finder 的「已加载」分支时，旧实现把已加载模块的
   ``SourceFileLoader`` 原样交回 import 机制——机制会新建模块对象再执行一遍
   源码。现改为 ``_AliasLoader``（``create_module`` 直接返回既有对象）。
2. ``_TOP_MODULE_ORDER`` 是手工维护的加载顺序表，不是全集。新增的顶层模块
   （如 ``constants.py``）不在表里，短名别名就没人登记，于是第一次
   ``import constants`` 正好落进上面那个分支。现按磁盘实际情况兜底回填。

所以这里不针对具体模块写死断言，而是遍历磁盘上全部顶层模块与子包做全量校验：
以后再加新模块，漏登记会立刻被这组测试抓住。
"""
import os
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_PKG = "astrbot_plugin_mingbu"

# 脚本型模块：导入即执行主体逻辑，不适合在测试里拉起来
_SCRIPT_MODULES = {"verify_hooks"}


def _top_level_modules():
    return sorted(
        f[:-3] for f in os.listdir(_ROOT)
        if f.endswith(".py") and not f.startswith("_")
        and f[:-3] not in _SCRIPT_MODULES
    )


def _top_level_packages():
    return sorted(
        d for d in os.listdir(_ROOT)
        if os.path.isdir(os.path.join(_ROOT, d))
        and not d.startswith("_") and not d.startswith(".")
        and os.path.isfile(os.path.join(_ROOT, d, "__init__.py"))
    )


def _star_modules():
    stars_dir = os.path.join(_ROOT, "stars")
    if not os.path.isdir(stars_dir):
        return []
    return sorted(
        f[:-3] for f in os.listdir(stars_dir)
        if f.endswith(".py") and not f.startswith("_")
    )


class TestSingleInstancePerModule:
    """短名与包内全名必须解析到同一个模块对象。"""

    @pytest.mark.parametrize("name", _top_level_modules())
    def test_module_not_loaded_twice(self, name):
        mod = __import__(name)
        full = f"{_PKG}.{name}"
        assert full in sys.modules, f"{name} 未以包内全名注册，相对导入会另起一份"
        assert mod is sys.modules[full], (
            f"{name} 被加载成两份：短名 {id(mod)} vs 包名 {id(sys.modules[full])}；"
            f"模块级单例会随之割裂"
        )
        assert sys.modules[name] is mod

    @pytest.mark.parametrize("name", _top_level_packages())
    def test_package_not_loaded_twice(self, name):
        pkg = __import__(name)
        full = f"{_PKG}.{name}"
        assert full in sys.modules
        assert pkg is sys.modules[full], f"子包 {name} 被加载成两份"

    def test_module_name_not_clobbered_by_alias(self):
        """别名解析不得把真实模块的 __name__ 改成短名。

        ``__name__`` 决定该模块内相对导入的锚点，被改写后一旦发生重载，
        ``from .x import y`` 就会解析到错误的包。
        """
        import constants
        assert constants.__name__ == f"{_PKG}.constants"

    def test_repeated_import_is_idempotent(self):
        """反复短名导入应始终拿到同一个对象（第二次会走 finder 的已加载分支）。"""
        import importlib
        first = importlib.import_module("constants")
        second = importlib.import_module("constants")
        third = __import__("constants")
        assert first is second is third


class TestSingletonsNotSplit:
    """挑几个「一旦分裂就会出错」的典型对象直接验明正身。"""

    def test_core_singleton_is_shared(self):
        import core
        import core as core_again
        assert core is core_again
        assert core.get_core() is core.get_core()

    def test_constants_objects_are_identical_across_consumers(self):
        import background_watch
        import constants
        import core
        assert core.MINGPAN_METHODS is constants.MINGPAN_METHODS
        assert background_watch._MINGPAN_METHODS is constants.MINGPAN_METHODS
        assert background_watch._BUGUA_METHODS is constants.BUGUA_METHODS

    def test_mixin_classes_are_identical_across_paths(self):
        import core
        import star_mixins
        from star_mixins.llm_hook import LLMRequestHookMixin
        assert core.LLMRequestHookMixin is star_mixins.LLMRequestHookMixin
        assert core.LLMRequestHookMixin is LLMRequestHookMixin

    def test_hook_classes_are_identical_across_paths(self):
        import core
        import hooks
        assert core.CrisisHook is hooks.CrisisHook


class TestStubCoversRealImportSurface:
    """桩必须覆盖源码真正用到的 astrbot 符号面。

    缺一个名字的代价远超预期：``_Filter`` 少了 ``regex``，
    ``stars/collectors.py`` 就在导入期抛 AttributeError；异常被
    ``_load_module`` 的 except 吞掉后，``main.py`` 连同它导入的 4 个 Star
    一起从 ``sys.modules`` 里消失，测试却一片绿——这几个模块等于长期零
    导入级覆盖。这组测试把「能导入」本身变成断言。
    """

    @pytest.mark.parametrize("name", _star_modules())
    def test_star_module_imports(self, name):
        import importlib
        mod = importlib.import_module(f"{_PKG}.stars.{name}")
        assert mod is sys.modules[f"{_PKG}.stars.{name}"]
        # 短名别名同样要在，否则 `from stars.admin import X` 会另起一份
        assert sys.modules.get(f"stars.{name}") is mod

    def test_main_is_loaded_with_all_stars(self):
        """main 是插件入口，它加载不了等于整个插件在测试里没被验证过。"""
        main = __import__("main")
        # 所有 @filter handler（指令/被动收集/LLM 工具/增强）已内联迁回 main.py
        # 的 MingbuPlugin（AstrBot 只识别入口模块的 handler），仅 StarAdmin 保留。
        assert hasattr(main, "StarAdmin"), "main 未成功导入 StarAdmin"
        assert hasattr(main, "MingbuPlugin"), "main 未成功导入 MingbuPlugin"

    @pytest.mark.parametrize("dotted,names", [
        ("astrbot.api", ["logger"]),
        ("astrbot.api.event", ["AstrMessageEvent", "MessageChain", "Plain",
                               "MessageEventResult", "filter"]),
        ("astrbot.api.message_components", ["Plain", "Record"]),
        ("astrbot.api.provider", ["ProviderRequest", "LLMResponse"]),
        ("astrbot.api.star", ["Context", "Star"]),
        ("astrbot.api.web", ["request", "json_response", "error_response"]),
        ("astrbot.core.provider", ["LLMResponse"]),
    ])
    def test_stub_exposes_symbol(self, dotted, names):
        import importlib
        mod = importlib.import_module(dotted)
        for n in names:
            assert hasattr(mod, n), f"{dotted} 缺少 {n}"

    def test_components_shared_between_event_and_components_module(self):
        """两条导入路径必须是同一个类，否则 isinstance 会静默判否。

        ``stt.py`` 从 ``message_components`` 拿 ``Record`` 做 isinstance，
        而消息链里的组件可能来自 ``astrbot.api.event``——不是同一个类的话，
        语音识别会安静地什么都收不到。
        """
        from astrbot.api import event, message_components
        assert event.Plain is message_components.Plain
        assert event.Record is message_components.Record
