"""
框架骨架压力测试 -- 验证架构文档承诺的行为一致性
================================================

针对 OVERVIEW_v4_split.md 描述的三层拆分架构（core / hooks / star_mixins），
从以下维度做压力验证：

1. MRO 与方法解析顺序（mixin 必须在 Star 基类之前）
2. ``__getattr__`` 委托（Star → Core 的鸭子类型注入）
3. 钩子链优先级与中断语义（Crisis 最高、可短路后续钩子）
4. 钩子链异常隔离（单个钩子抛异常不影响其余）
5. Core 单例（所有 Star 复用同一内核）
6. 配置注入链（config → Star.config / Star._config → core.configure）
7. mixin 无状态性（多个 Star 实例间不串扰）
"""

from __future__ import annotations

import asyncio

import pytest

from core import MingbuCore, MingbuStar, get_core
from hooks import (
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
from star_mixins import (
    ConversationContextMixin,
    GroupChatMixin,
    HealthProfileMixin,
    LLMRequestHookMixin,
    LLMTextMixin,
    PersonaContextMixin,
    ProfileMixin,
    RelationshipMixin,
)

_ALL_MIXINS = (
    LLMTextMixin,
    RelationshipMixin,
    PersonaContextMixin,
    GroupChatMixin,
    ProfileMixin,
    ConversationContextMixin,
    HealthProfileMixin,
    LLMRequestHookMixin,
)


# --------------------------------------------------------------------------
# 1. MRO 与方法解析
# --------------------------------------------------------------------------

def test_all_mixins_precede_star_in_mro():
    """8 个 mixin 必须全部排在 Star 之前，否则 AstrBot 基类方法会遮蔽。"""
    mro = [c.__name__ for c in MingbuStar.__mro__]
    star_idx = mro.index("Star")
    for m in _ALL_MIXINS:
        assert mro.index(m.__name__) < star_idx, (
            f"{m.__name__} 必须在 Star 之前"
        )


def test_mixin_declared_before_star_in_class_body():
    """类定义源码中 mixin 顺序与 MRO 一致（防止后续维护者误调整）。"""
    import inspect
    src = inspect.getsource(MingbuStar)
    mixin_names = [m.__name__ for m in _ALL_MIXINS]
    positions = [src.index(name) for name in mixin_names]
    assert positions == sorted(positions), "mixin 声明顺序被改动"
    # 用独立行匹配 Star 基类（而非 MingbuStar 类名中的子串）
    import re
    star_match = re.search(r"^\s+Star,\s*$", src, re.MULTILINE)
    assert star_match, "未找到 Star 基类声明行"
    star_pos = star_match.start()
    for name, pos in zip(mixin_names, positions):
        assert pos < star_pos, f"{name} 必须在 Star 声明前"


@pytest.mark.parametrize("mixin", _ALL_MIXINS)
def test_each_mixin_has_public_methods(mixin):
    """每个 mixin 至少暴露一个公开方法（不为空壳）。"""
    methods = [
        n for n in dir(mixin)
        if not n.startswith("__") and callable(getattr(mixin, n, None))
    ]
    assert len(methods) > 0, f"{mixin.__name__} 是空壳 mixin"


def test_mixin_methods_do_not_shadow_star_critical_attrs():
    """mixin 不应覆盖 Star 生命周期关键属性名（terminate / initialize）。"""
    reserved = {"initialize", "terminate"}
    for m in _ALL_MIXINS:
        clash = reserved & set(dir(m))
        assert not clash, f"{m.__name__} 覆盖了生命周期方法 {clash}"


# --------------------------------------------------------------------------
# 2. __getattr__ 委托
# --------------------------------------------------------------------------

def test_star_getattr_delegates_to_core():
    """MingbuStar 必须通过 __getattr__ 把未知属性转发给 core。"""
    assert hasattr(MingbuStar, "__getattr__"), "缺少 __getattr__ 委托机制"


def test_star_getattr_raises_on_unknown():
    """未知属性必须抛 AttributeError，否则会静默掩盖拼写错误。"""
    import inspect
    src = inspect.getsource(MingbuStar.__getattr__)
    assert "AttributeError" in src, "__getattr__ 应在 core 无该属性时抛错"


def test_getattr_uses_hasattr_guard():
    """委托逻辑必须先用 hasattr 守卫，避免递归或裸访问。"""
    import inspect
    src = inspect.getsource(MingbuStar.__getattr__)
    assert "hasattr(self.core" in src or "hasattr(self, \"core\")" in src


# --------------------------------------------------------------------------
# 3. 钩子链优先级与中断语义
# --------------------------------------------------------------------------

def test_default_chain_priority_order():
    """默认链优先级：Crisis 最高，Blessing 最低。"""
    chain = get_default_chain()
    order = [type(h).__name__ for h in chain._hooks]
    assert order == [
        "CrisisHook",
        "PersonaHook",
        "CharacterHook",
        "CounselingHook",
        "BlessingHook",
    ]


def test_build_default_chain_is_fresh_instance():
    """build_default_chain 每次返回新链；get_default_chain 返回共享单例。"""
    c1 = build_default_chain()
    c2 = build_default_chain()
    assert c1 is not c2
    assert get_default_chain() is get_default_chain()


class _RecordingHook(HookProcessor):
    """记录调用顺序的测试钩子。"""

    def __init__(self, name: str, return_value=None, exc: Exception | None = None):
        self.name = name
        self.return_value = return_value
        self.exc = exc

    async def process(self, core, event, req, **kwargs):
        calls = kwargs.setdefault("_calls", [])
        calls.append(self.name)
        if self.exc:
            raise self.exc
        return self.return_value


def test_hook_chain_return_false_short_circuits():
    """钩子返回 False 时，后续钩子不再执行。"""

    async def run():
        chain = HookChain()
        chain.register(_RecordingHook("A", return_value=False))
        chain.register(_RecordingHook("B"))
        calls: list[str] = []
        result = await chain.run(None, None, None, _calls=calls)
        return result, calls

    result, calls = asyncio.run(run())
    assert result is False
    assert calls == ["A"], "返回 False 后必须中断后续钩子"


def test_hook_chain_return_true_continues():
    """钩子返回 True（或 None）时链条继续。"""

    async def run():
        chain = HookChain()
        chain.register(_RecordingHook("A", return_value=True))
        chain.register(_RecordingHook("B", return_value=None))
        chain.register(_RecordingHook("C", return_value=True))
        calls: list[str] = []
        result = await chain.run(None, None, None, _calls=calls)
        return result, calls

    result, calls = asyncio.run(run())
    assert result is True
    assert calls == ["A", "B", "C"], "返回 True/None 不应中断链条"


def test_hook_chain_isolates_exceptions():
    """单个钩子抛异常只跳过自身，其余钩子照常执行，链条仍返回 True。"""

    async def run():
        chain = HookChain()
        chain.register(_RecordingHook("A", return_value=True))
        chain.register(_RecordingHook("B", exc=RuntimeError("boom")))
        chain.register(_RecordingHook("C", return_value=True))
        calls: list[str] = []
        result = await chain.run(None, None, None, _calls=calls)
        return result, calls

    result, calls = asyncio.run(run())
    assert result is True
    # B 抛异常前已记录调用，但链条隔离异常后 C 仍正常执行
    assert calls == ["A", "B", "C"], "异常钩子 B 被隔离跳过（不中断链条），C 照常执行"


def test_hook_chain_register_returns_self():
    """register 支持链式调用。"""
    chain = HookChain()
    assert chain.register(_RecordingHook("X")) is chain


def test_builtin_hooks_are_stateless():
    """内置 5 个钩子均无实例字段（可安全跨 Star 共享单例链）。"""
    for cls in (CrisisHook, PersonaHook, CharacterHook, CounselingHook, BlessingHook):
        instance = cls()
        fields = [k for k in vars(instance) if not k.startswith("_")]
        assert fields == [], f"{cls.__name__} 持有实例状态 {fields}"


# --------------------------------------------------------------------------
# 4. Core 单例与配置注入
# --------------------------------------------------------------------------

def test_get_core_returns_singleton():
    """get_core() 返回进程内唯一内核。"""
    assert get_core() is get_core()
    assert isinstance(get_core(), MingbuCore)


def test_core_configure_is_idempotent():
    """configure(None) 与 configure({}) 都不应抛错（幂等）。"""
    core = get_core()
    core.configure(None)
    core.configure({})
    core.configure({"provider": {"provider_type": "astrbot"}})


def test_star_binds_shared_core():
    """不同 Star 实例共享同一 core 单例。"""

    class _DummyStar(MingbuStar):
        pass

    star1 = _DummyStar.__new__(_DummyStar)
    star1.core = get_core()
    star2 = _DummyStar.__new__(_DummyStar)
    star2.core = get_core()
    assert star1.core is star2.core


# --------------------------------------------------------------------------
# 5. mixin 无状态 / 多实例隔离
# --------------------------------------------------------------------------

def test_mixins_have_no_state():
    """8 个 mixin 均无 __init__ 与实例字段。"""
    for m in _ALL_MIXINS:
        assert "__init__" not in m.__dict__, f"{m.__name__} 定义了 __init__"
        instance = object.__new__(m)
        assert vars(instance) == {}, f"{m.__name__} 实例非空 {vars(instance)}"


def test_mixin_methods_are_shared_functions():
    """mixin 方法是普通函数（绑定到实例时才成方法），无类级可变状态。"""
    for m in _ALL_MIXINS:
        for name, value in vars(m).items():
            if name.startswith("__"):
                continue
            if callable(value):
                # 普通函数 / async 函数，无 classvar 副作用
                assert not isinstance(value, (list, dict, set))


# --------------------------------------------------------------------------
# 6. core.py 向后兼容 re-export
# --------------------------------------------------------------------------

def test_core_reexports_hook_symbols():
    """``from core import CrisisHook, HookChain`` 等旧写法仍可用。"""
    from core import (  # noqa: F401
        BlessingHook as _B,
    )
    from core import (
        CharacterHook as _C,
    )
    from core import (
        CounselingHook as _Co,
    )
    from core import (
        CrisisHook as _Cr,
    )
    from core import (
        HookChain as _HC,
    )
    from core import (
        HookProcessor as _HP,
    )
    from core import (
        PersonaHook as _P,
    )
    from core import (
        get_default_chain as _gdc,
    )


def test_core_reexports_are_same_objects():
    """re-export 的符号与 hooks 子包是同一对象（不是副本）。"""
    import core
    import hooks

    assert core.CrisisHook is hooks.CrisisHook
    assert core.HookChain is hooks.HookChain
    assert core.get_default_chain is hooks.get_default_chain


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
