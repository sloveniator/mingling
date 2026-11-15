"""
tests/test_hooks_package.py -- hooks 子包拆分契约
==================================================

钩子类此前挤在 core.py 尾部（原 921-1092 行），本轮迁出到 hooks/ 子包。
这组测试锁定三件事，防止后续改动悄悄破坏：

1. **向后兼容**：``from core import CrisisHook`` 仍可用，且与
   ``from hooks import CrisisHook`` 是同一个类对象（不能因模块被加载
   两次而割裂成两个类——那会让 isinstance 判断莫名其妙地失败）。
2. **优先级顺序**：默认链的钩子顺序即注入优先级，危机支持必须最先。
3. **共享链安全性**：``get_default_chain()`` 返回进程内单例，前提是所有
   内置钩子无状态。这里直接断言"无实例字段"，一旦有人给钩子加了
   实例状态，测试会立刻失败并提醒改用 ``build_default_chain()``。
"""
import asyncio

import pytest

import core as core_mod
import hooks as hooks_pkg
from hooks import (
    DEFAULT_HOOK_CLASSES,
    BlessingHook,
    CharacterHook,
    CounselingHook,
    CrisisHook,
    HookChain,
    HookProcessor,
    PersonaHook,
    build_default_chain,
    get_default_chain,
)

ALL_HOOK_NAMES = [
    "HookProcessor", "HookChain", "CrisisHook", "PersonaHook",
    "CharacterHook", "CounselingHook", "BlessingHook",
]


class TestBackwardCompatReexport:
    @pytest.mark.parametrize("name", ALL_HOOK_NAMES)
    def test_core_still_exports(self, name):
        assert hasattr(core_mod, name), f"core.{name} re-export 丢失，旧导入路径会断"

    @pytest.mark.parametrize("name", ALL_HOOK_NAMES)
    def test_same_object_as_hooks_package(self, name):
        """必须是同一个类对象，而非重复加载出的副本。"""
        assert getattr(core_mod, name) is getattr(hooks_pkg, name)

    @pytest.mark.parametrize("name", ALL_HOOK_NAMES)
    def test_defined_in_hooks_subpackage(self, name):
        """实现应已真正搬到 hooks 子包，而不是留在 core 里。"""
        mod = getattr(hooks_pkg, name).__module__
        assert ".hooks." in mod or mod.endswith("hooks"), f"{name} 仍定义在 {mod}"


class TestDefaultChain:
    def test_order_is_priority(self):
        assert list(DEFAULT_HOOK_CLASSES) == [
            CrisisHook, PersonaHook, CharacterHook, CounselingHook, BlessingHook,
        ], "顺序即优先级，危机支持必须最先执行"

    def test_build_returns_fresh_instances(self):
        a, b = build_default_chain(), build_default_chain()
        assert a is not b
        assert [type(h) for h in a._hooks] == list(DEFAULT_HOOK_CLASSES)

    def test_get_default_chain_is_singleton(self):
        assert get_default_chain() is get_default_chain()

    def test_singleton_not_polluted_by_build(self):
        shared = get_default_chain()
        build_default_chain().register(CrisisHook())
        assert len(shared._hooks) == len(DEFAULT_HOOK_CLASSES)

    @pytest.mark.parametrize("cls", DEFAULT_HOOK_CLASSES)
    def test_hooks_are_stateless(self, cls):
        """无实例字段才可安全共享单例链。"""
        assert cls().__dict__ == {}, (
            f"{cls.__name__} 有实例状态，不能再共享 get_default_chain()，"
            f"请改用 build_default_chain()"
        )

    @pytest.mark.parametrize("cls", DEFAULT_HOOK_CLASSES)
    def test_all_hooks_subclass_base(self, cls):
        assert issubclass(cls, HookProcessor)


class TestSubmodulesImportable:
    @pytest.mark.parametrize("sub,cls_name", [
        ("base", "HookProcessor"),
        ("crisis", "CrisisHook"),
        ("persona", "PersonaHook"),
        ("character", "CharacterHook"),
        ("counseling", "CounselingHook"),
        ("blessing", "BlessingHook"),
    ])
    def test_each_module_standalone(self, sub, cls_name):
        """每个钩子应能独立导入——这正是拆分的意义。"""
        mod = __import__(f"hooks.{sub}", fromlist=[cls_name])
        assert hasattr(mod, cls_name)

    def test_base_has_no_sibling_dependency(self):
        """base 只依赖 astrbot logger，不得反向依赖具体钩子或 core。"""
        import inspect

        from hooks import base
        src = inspect.getsource(base)
        assert "import core" not in src
        assert "from ..core" not in src


class TestChainBehaviourPreserved:
    """拆分不得改变链的既有语义（返回 False 中断、异常不致命）。"""

    def test_false_short_circuits(self):
        calls = []

        class A(HookProcessor):
            async def process(self, core, event, req, **kw):
                calls.append("a")
                return False

        class B(HookProcessor):
            async def process(self, core, event, req, **kw):
                calls.append("b")
                return True

        chain = HookChain().register(A()).register(B())
        ok = asyncio.run(chain.run(None, None, None))
        assert calls == ["a"] and ok is False

    def test_exception_is_isolated(self):
        calls = []

        class Boom(HookProcessor):
            async def process(self, core, event, req, **kw):
                raise RuntimeError("boom")

        class After(HookProcessor):
            async def process(self, core, event, req, **kw):
                calls.append("after")
                return True

        chain = HookChain().register(Boom()).register(After())
        assert asyncio.run(chain.run(None, None, None)) is True
        assert calls == ["after"]

    def test_register_is_chainable(self):
        chain = HookChain()
        assert chain.register(CrisisHook()) is chain


def test_watch_note_counter_registered():
    """CounselingHook 里新收敛的静默 except 需有对应计数器。"""
    from metrics import get_failure_counts
    assert "watch_note_inject" in get_failure_counts()


class TestWiredChainIntegration:
    """默认链装配后能真正跑通——只"可导入"不等于"可运行"。"""

    class _Req:
        def __init__(self):
            self.extra_user_content_parts = []
            self.system_prompt = ""

    class _Event:
        def get_sender_id(self):
            return "u_test"

        def get_sender_name(self):
            return "测试用户"

    class _Core:
        """最小可用 core：只满足 CrisisHook，其余钩子会因缺字段被链吞掉。"""
        def __init__(self):
            self._config = {"counseling_crisis_enabled": True}
            self.persona = None
            self.character_collection = None

    def test_default_chain_injects_crisis_prompt(self):
        from counseling import detect_crisis

        req = self._Req()
        crisis = detect_crisis("我正在割腕")
        assert crisis is not None

        ok = asyncio.run(get_default_chain().run(
            self._Core(), self._Event(), req,
            is_private=True, is_group=False, mentioned=False,
            message_text="我正在割腕", user_is_divining=False,
            crisis=crisis, inject_persona=True, rel=None,
        ))

        assert ok is True, "链不应被中断"
        assert len(req.extra_user_content_parts) >= 1, "危机注入未生效，链装配可能已断"
        assert "热线" in req.extra_user_content_parts[0]

    def test_chain_survives_incomplete_core(self):
        """后续钩子访问缺失字段抛异常时，整条链仍须正常返回。"""
        req = self._Req()
        ok = asyncio.run(get_default_chain().run(
            self._Core(), self._Event(), req,
            is_private=True, message_text="今天天气不错", crisis=None,
        ))
        assert ok is True
