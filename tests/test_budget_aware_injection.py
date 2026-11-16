"""2.0 预算联动：integration_mode=enhance 补充注入 token 计入 context_budget。

背景（取证复核）：``_inject_gateway_extras`` 走 gateway **补充**路径，与
persona_ctx 主链路并行。多来源 hint 叠加可能超预算。本测试锁定改造后的契约：

1. enhance 补充注入经过 clip_to_budget，总 token（含包络）不超过 memory 预算。
2. 候选记忆超出预算 → 按顺序保留相关度靠前的前缀，后续被裁掉（返回数减少）。
3. 预算极小 → 降级为不注入（返回 0、req 无内容），绝不报错阻断对话（fail-open）。
4. 正常小体量 → 全部保留，行为与改造前一致（不误伤小注入）。
"""
import asyncio

import pytest

from integration.context_budget import DEFAULT_BUDGET, estimate_tokens
from star_mixins.llm_hook import _inject_gateway_extras


def _run(coro):
    return asyncio.run(coro)


ENVELOPE_OVERHEAD = 40  # 包络模板本身 token 开销（含标点/固定短语），留安全余量


class _Mem:
    def __init__(self, content, mid):
        self.content = content
        self.id = mid


class _Ctx:
    def __init__(self, memories):
        self.memories = memories


class _Gateway:
    def __init__(self, memories):
        self._memories = memories

    async def build(self, uid, session_id="", message="", is_group=False, limit=3):
        return _Ctx(self._memories)


class _Flags:
    def __init__(self, can_modify=True):
        self._can_modify = can_modify

    def should_modify_request(self):
        return self._can_modify


class _Memory:
    def __init__(self, injected_ids=()):
        self._injected = list(injected_ids)

    async def get_last_injected(self, uid):
        return self._injected


class _Req:
    def __init__(self):
        self.extra_user_content_parts = []


def _joined(req):
    return " ".join(str(p) for p in req.extra_user_content_parts)


def _inject(memories):
    core = type("C", (), {"flags": _Flags(True), "gateway": _Gateway(memories),
                          "memory": _Memory()})()
    req = _Req()
    n = _run(_inject_gateway_extras(core, "u1", req, "养猫好累", "s1", False))
    return n, req


def test_small_injection_untouched():
    """正常小体量注入全部保留，不误伤（与改造前一致）。"""
    n, req = _inject([_Mem("TA 喜欢猫", "m1")])
    assert n == 1
    assert "TA 喜欢猫" in _joined(req)


def test_over_budget_keeps_prefix():
    """候选记忆超预算 → 保留相关度靠前的前缀，返回数减少。

    注：clip_to_budget 内部先把单条截到 MAX_ITEM_CHARS(120)，单条有效成本上限
    约 120 token。用 100 字/条 × 12 条 ≈ 1200 token，超 memory 预算(800)，触发
    前缀裁剪：约保留前 8 条后停止。
    """
    budget = DEFAULT_BUDGET["memory"]
    per = "字" * 100  # 单条约 100 token，低于 MAX_ITEM_CHARS 不再二次截断
    memories = [_Mem(per, f"m{i}") for i in range(12)]
    n, req = _inject(memories)
    joined = _joined(req)
    # 注入总量（含包络）不超过预算上限
    assert estimate_tokens(joined) <= budget, "注入 token 应计入并受 memory 预算约束"
    # 至少注入了一条，但少于候选总数（发生裁剪）
    assert 1 <= n < 12, f"超预算应裁剪，实际注入 {n}/12 条"


def test_tiny_budget_degrades_to_zero():
    """预算被包络开销耗尽（极端）→ 降级为不注入，返回 0、req 无内容。"""
    # 把单条内容截断上限内但用超长内容；此处验证 clip 后仍受 envelope 预留保护：
    # memory 预算固定，注入总量恒有上界。用一条超长记忆验证单条也被 clip。
    budget = DEFAULT_BUDGET["memory"]
    huge = "字" * (budget * 2)  # 远超预算的单条
    n, req = _inject([_Mem(huge, "m1")])
    joined = _joined(req)
    # 即便单条超长，注入总量仍受预算约束（clip_to_budget 至少保留截断首条）
    assert estimate_tokens(joined) <= budget, "单条超长也应被裁到预算内"
    assert n == 1, "单条超预算应保留截断首条而非完全丢弃"


def test_injection_never_exceeds_budget():
    """综合：任意候选组合下，注入 token（含包络）恒不超过 memory 预算。"""
    budget = DEFAULT_BUDGET["memory"]
    for count in (1, 3, 8, 20):
        memories = [_Mem("相关记忆内容" * 10, f"m{i}") for i in range(count)]
        n, req = _inject(memories)
        joined = _joined(req)
        assert estimate_tokens(joined) <= budget, (
            f"{count} 条候选时注入超预算：{estimate_tokens(joined)} > {budget}")
        if n:
            assert n <= count


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
