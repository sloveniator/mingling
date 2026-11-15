"""2.0 第一阶段任务 4：FeatureFlags / ContextBudget / ContextGateway 回归测试。

覆盖：
- FeatureFlags：总开关、三模式、observe 不改请求、单项能力门控。
- ContextBudget：token 估算、按预算裁剪、语义边界截断。
- ContextGateway：相关记忆检索、群聊只召回 group scope、observe 不注入、
  单点增强异常不中断 build（fail-open 局部化）。
"""
import asyncio

import pytest

from integration.context_budget import (
    estimate_tokens,
    clip_to_budget,
)
from integration.context_gateway import ContextGateway, EnhancementContext
from integration.feature_flags import (
    MODE_ASSIST,
    MODE_ENHANCE,
    MODE_OBSERVE,
    FeatureFlags,
)


def _run(coro):
    return asyncio.run(coro)


# ---- FeatureFlags ----

def test_flags_default_enhance_and_open():
    f = FeatureFlags.from_config({})
    assert f.enabled is True
    assert f.mode == MODE_ENHANCE
    assert f.should_modify_request() is True


def test_flags_disabled_blocks_all():
    f = FeatureFlags.from_config({"enabled": False})
    assert f.should_modify_request() is False
    assert f.is_enabled("memory") is False


def test_flags_observe_mode_no_modify():
    f = FeatureFlags.from_config({"integration_mode": MODE_OBSERVE})
    assert f.is_observe is True
    assert f.should_modify_request() is False


def test_flags_assist_and_enhance_modify():
    for mode in (MODE_ENHANCE, MODE_ASSIST):
        f = FeatureFlags.from_config({"integration_mode": mode})
        assert f.should_modify_request() is True


def test_flags_invalid_mode_falls_back_enhance():
    f = FeatureFlags.from_config({"integration_mode": "bogus"})
    assert f.mode == MODE_ENHANCE


def test_flags_single_capability_toggle():
    f = FeatureFlags.from_config({"memory_enabled": False})
    assert f.is_enabled("memory") is False
    assert f.is_enabled("persona") is True


# ---- ContextBudget ----

def test_estimate_tokens_cjk():
    assert estimate_tokens("你好世界") == 4
    assert estimate_tokens("") == 0


def test_clip_to_budget_respects_limit():
    items = [f"心理陪伴记忆条目编号 {i} " * 8 for i in range(20)]
    small_budget = 300
    kept, report = clip_to_budget(items, budget=small_budget)
    assert len(kept) < len(items), "预算应裁剪掉部分条目"
    # 报告应准确反映实际保留的条目数
    assert report.kept_items == len(kept)
    assert report.original_items == len(items)


def test_clip_item_semantic_boundary():
    long = "这是一句话。" * 40  # 远超 120 字符
    kept, _ = clip_to_budget([long], budget=1000, max_item_chars=120)
    # 截断后不超过合理长度且以句号结尾（语义边界）
    assert len(kept[0]) <= 130
    assert kept[0].endswith("。")


# ---- ContextGateway ----

class _FakeMemory:
    def __init__(self):
        self.calls = []

    async def retrieve_relevant(self, uid, query, limit=5, max_scope=None):
        self.calls.append((uid, query, limit, max_scope))

        class M:
            def __init__(self, c):
                self.content = c
                self.id = c
        return [M(f"{query}-记忆1"), M(f"{query}-记忆2")]


class _FakePersona:
    name = "灵犀"


class _Core:
    def __init__(self, memory=None, persona=None):
        self.memory = memory
        self.persona = persona


def test_gateway_retrieves_relevant_memories():
    mem = _FakeMemory()
    gw = ContextGateway(_Core(memory=mem),
                        flags=FeatureFlags.from_config({}))
    ctx = _run(gw.build("u1", "s1", message="心理陪伴项目", is_group=False))
    assert ctx.injected is True
    assert len(ctx.memories) == 2
    assert ctx.memories[0].content == "心理陪伴项目-记忆1"
    assert ctx.recall_scope is None  # 私聊含全部


def test_gateway_group_sets_group_scope():
    mem = _FakeMemory()
    gw = ContextGateway(_Core(memory=mem),
                        flags=FeatureFlags.from_config({}))
    ctx = _run(gw.build("u1", "s1", message="群话题", is_group=True))
    assert ctx.recall_scope == "group"
    # 透传 group scope 给记忆检索
    assert mem.calls[0][3] == "group"


def test_gateway_observe_mode_no_injection():
    mem = _FakeMemory()
    gw = ContextGateway(_Core(memory=mem),
                        flags=FeatureFlags.from_config(
                            {"integration_mode": MODE_OBSERVE}))
    ctx = _run(gw.build("u1", "s1", message="x", is_group=False))
    assert ctx.injected is False
    assert ctx.memories == []
    assert mem.calls == []  # observe 不触发检索


def test_gateway_memory_failure_does_not_raise():
    """记忆引擎抛异常 → build 不中断，返回空记忆（fail-open 局部化）。"""
    class _BadMem:
        async def retrieve_relevant(self, *a, **k):
            raise RuntimeError("boom")
    gw = ContextGateway(_Core(memory=_BadMem()),
                        flags=FeatureFlags.from_config({}))
    ctx = _run(gw.build("u1", "s1", message="x", is_group=False))
    assert ctx.memories == []
    assert ctx.user_id == "u1"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
