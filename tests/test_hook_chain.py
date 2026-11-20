"""
tests/test_hook_chain.py -- LLM 注入钩子链回归测试
====================================================

覆盖本轮修复：CrisisHook 与 _on_llm_request_body 曾各自 detect_crisis 一次
并各自 append 一次提示词，导致同一次请求被注入两份危机引导（浪费 token，
且两段互相矛盾的指令会削弱模型遵循度）。现在检测在外层完成、注入只在钩子里。
"""
import asyncio

import pytest

from core import CrisisHook, HookChain


class FakeReq:
    def __init__(self):
        self.extra_user_content_parts = []
        self.system_prompt = ""


class FakeCore:
    def __init__(self, **cfg):
        self._config = {"counseling_crisis_enabled": True, **cfg}


def _run(hook, core, req, **kw):
    return asyncio.run(hook.process(core, object(), req, **kw))


class TestCrisisHook:
    def test_injects_once_when_crisis_passed_in(self):
        from counseling import detect_crisis

        req = FakeReq()
        crisis = detect_crisis("我正在割腕")
        assert crisis is not None
        _run(CrisisHook(), FakeCore(), req, is_private=True,
             message_text="我正在割腕", crisis=crisis)
        assert len(req.extra_user_content_parts) == 1

    def test_no_injection_when_crisis_is_none(self):
        """外层已判定无危机时，钩子不得自行再检测一遍。"""
        req = FakeReq()
        _run(CrisisHook(), FakeCore(), req, is_private=True,
             message_text="我正在割腕", crisis=None)
        assert req.extra_user_content_parts == []

    def test_fallback_detect_when_kwarg_absent(self):
        """未传 crisis 键时保留自检能力，便于单独复用该钩子。"""
        req = FakeReq()
        _run(CrisisHook(), FakeCore(), req, is_private=True,
             message_text="我正在割腕")
        assert len(req.extra_user_content_parts) == 1

    def test_group_message_skipped(self):
        req = FakeReq()
        from counseling import detect_crisis
        _run(CrisisHook(), FakeCore(), req, is_private=False,
             message_text="我正在割腕", crisis=detect_crisis("我正在割腕"))
        assert req.extra_user_content_parts == []

    def test_disabled_by_config(self):
        req = FakeReq()
        from counseling import detect_crisis
        _run(CrisisHook(), FakeCore(counseling_crisis_enabled=False), req,
             is_private=True, message_text="我正在割腕",
             crisis=detect_crisis("我正在割腕"))
        assert req.extra_user_content_parts == []

    @pytest.mark.parametrize("text,marker", [
        ("我正在割腕", "热线"),      # action -> 危机支持（含热线）
    ])
    def test_action_level_uses_hotline_prompt(self, text, marker):
        from counseling import detect_crisis

        req = FakeReq()
        _run(CrisisHook(), FakeCore(), req, is_private=True,
             message_text=text, crisis=detect_crisis(text))
        assert marker in req.extra_user_content_parts[0]

    def test_ideation_level_avoids_hotline(self):
        from counseling import detect_crisis

        req = FakeReq()
        crisis = detect_crisis("活着没意思，不如消失了")
        assert crisis["level"] == "ideation"
        _run(CrisisHook(), FakeCore(), req, is_private=True,
             message_text="活着没意思", crisis=crisis)
        assert len(req.extra_user_content_parts) == 1


class TestHookChain:
    def test_chain_runs_all_and_stops_on_false(self):
        calls = []

        class A:
            async def process(self, core, event, req, **kw):
                calls.append("a")
                return True

        class B:
            async def process(self, core, event, req, **kw):
                calls.append("b")
                return False

        class C:
            async def process(self, core, event, req, **kw):
                calls.append("c")
                return True

        chain = HookChain()
        chain.register(A())
        chain.register(B())
        chain.register(C())
        asyncio.run(chain.run(FakeCore(), object(), FakeReq()))
        assert calls == ["a", "b"], "返回 False 应中断后续钩子"

    def test_one_hook_failure_does_not_break_chain(self):
        calls = []

        class Boom:
            async def process(self, core, event, req, **kw):
                raise RuntimeError("hook exploded")

        class Later:
            async def process(self, core, event, req, **kw):
                calls.append("later")
                return True

        chain = HookChain()
        chain.register(Boom())
        chain.register(Later())
        asyncio.run(chain.run(FakeCore(), object(), FakeReq()))
        assert calls == ["later"], "单个钩子异常不应中断整条链"


# ---- v4.8.101 回归：extra_user_content_parts 必须为 ContentPart 兼容对象 ----

def test_inject_text_part_elements_support_model_dump():
    """AstrBot 4.27.4 的 assemble_context 要求元素有 model_dump_for_context；
    注入的元素必须满足该契约（否则 agent run 崩溃: 'str' object has no
    attribute 'model_dump_for_context'）。"""
    from mingling.util import inject_text_part
    req = FakeReq()
    part = inject_text_part(req, "测试注入文本")
    assert len(req.extra_user_content_parts) == 1
    assert hasattr(req.extra_user_content_parts[0], "model_dump_for_context"), \
        "注入元素必须支持 model_dump_for_context()（真实 AstrBot 4.27.4 硬性要求）"
    dumped = req.extra_user_content_parts[0].model_dump_for_context()
    assert dumped.get("type") == "text"
    assert "测试注入文本" in dumped.get("text", "")
    assert str(part) == "测试注入文本" or part.text == "测试注入文本"
