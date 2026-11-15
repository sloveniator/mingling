"""
tests/test_star_mixins_package.py -- star_mixins 子包拆分契约
=============================================================

``MingbuStar`` 此前在 core.py 里堆了 30 个成员、约 557 行，与内核
``MingbuCore`` 混在一个文件。本轮把其中 28 个助手方法迁到 ``star_mixins/``
的 8 个 mixin，``MingbuStar`` 只留 ``__init__`` 与 ``__getattr__``。

拆分靠 MRO 组合而非继承链改写，出问题的方式都很安静——方法名撞车会被 MRO
悄悄择一，mixin 排到 ``Star`` 后面会被基类同名方法遮蔽，漏搬一个方法要等
线上调用才炸。所以这里把不变量一条条钉死：

1. **方法面不缩水**：拆分前 core.py 里 ``MingbuStar`` 的 30 个成员，拆分后
   一个不少地仍可从 ``MingbuStar`` 访问到。
2. **确实搬走了**：28 个助手方法的 ``__module__`` 必须落在 ``star_mixins``，
   而不是还赖在 core 里（否则就只是复制而非迁移）。
3. **无同名撞车**：任意两个 mixin 不得定义同名方法。
4. **MRO 顺序**：全部 mixin 必须排在 ``Star`` 之前。
5. **声明源唯一**：core.py 里写死的基类列表必须与 ``STAR_MIXINS`` 完全一致
   （含顺序），防止两处各改一半。
6. **无循环依赖**：mixin 一律不 import core。
7. **无状态**：mixin 不得定义 ``__init__``，否则会插进 ``MingbuStar`` 的
   构造链，把 ``Star.__init__`` 的调用顺序搅乱。
"""
import inspect

import pytest
from astrbot.api.star import Star

import core as core_mod
import star_mixins as mixins_pkg
from core import MingbuStar
from star_mixins import STAR_MIXINS

# 拆分前 core.py 中 MingbuStar 的全部成员（AST 提取的历史快照，勿随手改）。
# 改这份清单意味着你在增删 MingbuStar 的公共方法面，请确认是有意为之。
ORIGINAL_MEMBERS = [
    "__init__", "__getattr__",
    "_llm_text",
    "_get_relationship", "_save_relationship", "_remember_user", "_active_uids",
    "_build_persona_block", "_rerank_memories", "_recall_semantic", "_save_persona",
    "_bot_mentioned", "_maybe_onboard",
    "_get_profile", "_save_profile", "_birth_from", "_birth_params",
    "_method_for", "_save_birth", "_validate_birth",
    "TURN_HISTORY", "_append_turn", "_get_turns", "_append_context",
    "_get_context", "_set_topic", "_get_topic",
    "_get_health_profile", "_save_health_profile",
    "_on_llm_request_body",
]

# 这两个留在 core.py 的 MingbuStar 本体（绑定内核 + 属性代理）
STAYS_IN_CORE = {"__init__", "__getattr__"}
MOVED_MEMBERS = [m for m in ORIGINAL_MEMBERS if m not in STAYS_IN_CORE]

MIXIN_NAMES = [
    "LLMTextMixin", "RelationshipMixin", "PersonaContextMixin", "GroupChatMixin",
    "ProfileMixin", "ConversationContextMixin", "HealthProfileMixin",
    "LLMRequestHookMixin",
]


class TestSurfacePreserved:
    """拆分不得让 MingbuStar 少任何一个成员。"""

    def test_original_snapshot_is_complete(self):
        assert len(ORIGINAL_MEMBERS) == 30, "历史快照应为 30 个成员"

    @pytest.mark.parametrize("name", ORIGINAL_MEMBERS)
    def test_member_still_reachable(self, name):
        assert hasattr(MingbuStar, name), (
            f"MingbuStar.{name} 在拆分后消失了——调用方会在运行时才炸"
        )

    def test_turn_history_value_unchanged(self):
        """常量类属性也要跟着 mixin 一起过来，且值不变。"""
        assert MingbuStar.TURN_HISTORY == 10


class TestActuallyMoved:
    """搬走 ≠ 复制：实现必须真的定义在 star_mixins 里。"""

    @pytest.mark.parametrize("name", MOVED_MEMBERS)
    def test_defined_in_star_mixins(self, name):
        owner = next(
            klass for klass in MingbuStar.__mro__ if name in vars(klass)
        )
        assert owner.__module__.split(".")[-2:][0] == "star_mixins" or (
            "star_mixins" in owner.__module__
        ), f"{name} 仍定义在 {owner.__module__}，没有真正迁出"

    @pytest.mark.parametrize("name", sorted(STAYS_IN_CORE))
    def test_core_keeps_only_binding_and_proxy(self, name):
        assert name in vars(MingbuStar), f"{name} 应留在 core.MingbuStar 本体"

    def test_mingbustar_body_is_thin(self):
        """MingbuStar 本体只应有绑定内核与属性代理两件事。

        用「类体成员集合」而不是行数做断言：行数会因注释增删误报，成员集合
        才是真正的不变量。有人往这里塞第三个方法时，应先问问它是否属于某个
        mixin。
        """
        own = {k for k in vars(MingbuStar) if not k.startswith("__")}
        own -= {"_abc_impl"}
        assert own == set(), (
            f"MingbuStar 本体又长出了成员 {sorted(own)}；"
            f"助手方法请放进 star_mixins/ 对应模块"
        )


class TestNoNameCollision:
    """同名方法会被 MRO 悄悄择一，必须提前拦住。"""

    def test_no_duplicate_method_across_mixins(self):
        seen = {}
        dupes = []
        for klass in STAR_MIXINS:
            for name in vars(klass):
                if name.startswith("__"):
                    continue
                if name in seen:
                    dupes.append((name, seen[name].__name__, klass.__name__))
                seen[name] = klass
        assert not dupes, f"mixin 之间存在同名成员，MRO 会静默择一：{dupes}"

    def test_every_moved_member_owned_by_exactly_one_mixin(self):
        for name in MOVED_MEMBERS:
            owners = [k.__name__ for k in STAR_MIXINS if name in vars(k)]
            assert len(owners) == 1, f"{name} 的归属 mixin 为 {owners}，应恰好一个"


class TestMroContract:
    def test_all_mixins_before_star(self):
        mro = MingbuStar.__mro__
        star_pos = mro.index(Star)
        for klass in STAR_MIXINS:
            assert mro.index(klass) < star_pos, (
                f"{klass.__name__} 排在 Star 之后，AstrBot 基类的同名方法会遮蔽它"
            )

    def test_declared_bases_match_star_mixins_tuple(self):
        """core.py 里写死的基类列表与 STAR_MIXINS 必须逐项对齐（含顺序）。"""
        declared = tuple(b for b in MingbuStar.__bases__ if b is not Star)
        assert declared == tuple(STAR_MIXINS), (
            "core.MingbuStar 的基类列表与 star_mixins.STAR_MIXINS 不一致，"
            "两处只改了一处"
        )

    def test_star_is_last_base(self):
        assert MingbuStar.__bases__[-1] is Star


class TestNoCircularDependency:
    @pytest.mark.parametrize("name", MIXIN_NAMES)
    def test_mixin_module_does_not_import_core(self, name):
        src = inspect.getsource(inspect.getmodule(getattr(mixins_pkg, name)))
        # 只看代码行，避免文档字符串里提到 "core" 造成误判
        code = "\n".join(
            ln for ln in src.splitlines()
            if not ln.lstrip().startswith("#")
        )
        assert "from ..core import" not in code
        assert "from .core import" not in code
        assert "import core" not in code

    def test_constants_module_does_not_import_core(self):
        import constants
        src = inspect.getsource(constants)
        assert "from .core import" not in src
        assert "import core" not in src


class TestStateless:
    @pytest.mark.parametrize("name", MIXIN_NAMES)
    def test_mixin_defines_no_init(self, name):
        klass = getattr(mixins_pkg, name)
        assert "__init__" not in vars(klass), (
            f"{name} 定义了 __init__，会插进 MingbuStar 的构造链；"
            f"实例状态请交给 MingbuCore 持有"
        )


class TestSubmodulesImportable:
    @pytest.mark.parametrize("sub,cls_name", [
        ("llm", "LLMTextMixin"),
        ("relations", "RelationshipMixin"),
        ("persona_ctx", "PersonaContextMixin"),
        ("group", "GroupChatMixin"),
        ("profile", "ProfileMixin"),
        ("conversation", "ConversationContextMixin"),
        ("health", "HealthProfileMixin"),
        ("llm_hook", "LLMRequestHookMixin"),
    ])
    def test_each_module_standalone(self, sub, cls_name):
        """每个 mixin 应能独立导入——这正是拆分的意义。"""
        mod = __import__(f"star_mixins.{sub}", fromlist=[cls_name])
        assert hasattr(mod, cls_name)

    @pytest.mark.parametrize("name", MIXIN_NAMES)
    def test_same_object_via_package_and_core(self, name):
        """经 core 与经子包拿到的必须是同一个类对象，不能被加载成两份。"""
        assert getattr(core_mod, name) is getattr(mixins_pkg, name)


class TestConstantsReexport:
    """常量下沉后，旧的 ``from core import KV_USERS`` 必须仍指向同一对象。"""

    @pytest.mark.parametrize("name", [
        "KV_USERS", "KV_PROFILE", "KV_CONTEXT", "MAX_CONTEXT_MSGS",
        "MINGBU_API_URL", "PLUGIN_NAME", "MINGPAN_METHODS", "BUGUA_METHODS",
        "MINGBU_METHODS_ALL", "BUGUA_BRIDGE_TO_LIUYAO", "LLM_TEXT_TIMEOUT",
        "EMOTION_KEYWORD_PATTERN", "EMOTION_KEYWORD_REGEX",
        "BIRTH_YEAR_RE", "ZODIAC_RE",
    ])
    def test_core_reexports_constant(self, name):
        import constants
        assert hasattr(core_mod, name), f"core.{name} re-export 丢失，旧导入路径会断"
        assert getattr(core_mod, name) is getattr(constants, name)

    def test_background_watch_uses_shared_constants(self):
        """曾手抄一份副本，靠注释维持同步——现在必须是同一个对象。"""
        import background_watch as bw
        import constants
        assert bw._MINGPAN_METHODS is constants.MINGPAN_METHODS
        assert bw._BUGUA_METHODS is constants.BUGUA_METHODS
