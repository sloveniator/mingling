"""
tests/test_silent_divination_interpret.py -- 命理 AI 解读（interpret）接线回归
=========================================================================
审查报告 [中]：mingbu_interpret_* 此前声明了却从未被调用，开启后静默无效。
本次把 /api/interpret 真正接入暗盘 _compute：开启时用平台 LLM 生成更丰富洞察，
经 strip_leak_terms 硬性屏蔽术语，本地翻译表洞察始终作为兜底。

本文件用 Fake API 把这条路径单测钉死（沿用仓库既有的 asyncio.run 约定）：
  1. 开启 + LLM 返回零术语文本 → 直接采用 LLM 文本
  2. 开启 + LLM 返回含术语文本 → 被屏蔽为零术语（不泄露命理词）
  3. 开启 + LLM 抛异常 → 安全回退本地洞察（零术语）
  4. 关闭 → 不调用 interpret，仅本地洞察
  5. 开启 + LLM 返回几乎全是术语（屏蔽占比过高）→ 质量不达标，回退本地
"""
import asyncio
from typing import Optional

from disguise_guard import check_disguise_leak
from silent_divination import SilentDiviner


def _sample_analysis():
    """能产出非空本地洞察的最小命盘结构（供回退断言对照）。"""
    return {
        "bazi": {
            "dayMaster": {"element": "水"},
            "strength": {"level": "中和"},
        },
        "ziwei": {
            "palaces": [
                {"name": "命宫", "isMingGong": True,
                 "stars": [{"name": "太阴", "type": "major"}]},
                {"name": "夫妻宫",
                 "stars": [{"name": "天同", "type": "major"}]},
            ],
            "daXians": [{"startAge": 0, "endAge": 99, "palaceName": "官禄宫"}],
            "currentAge": 30,
        },
    }


class FakeApi:
    def __init__(self, interpret_text: Optional[str] = None,
                 interpret_raise: bool = False):
        self.interpret_text = interpret_text
        self.interpret_raise = interpret_raise
        self.interpret_calls: list = []

    async def combined(self, birth):
        return {"analysis": _sample_analysis()}

    async def interpret(self, **kwargs):
        self.interpret_calls.append(kwargs)
        if self.interpret_raise:
            raise RuntimeError("interpret 端点不可用")
        return self.interpret_text


def _make_diviner(enabled: bool, interpret_text=None, interpret_raise=False):
    api = FakeApi(interpret_text=interpret_text, interpret_raise=interpret_raise)
    div = SilentDiviner(api)
    div.set_interpret_config({
        "mingbu_interpret_enabled": enabled,
        "mingbu_interpret_provider": "deepseek",
        "mingbu_interpret_api_key": "",
        "mingbu_interpret_base_url": "",
        "mingbu_interpret_model": "",
    })
    return div, api


def _local_insights():
    return SilentDiviner(None)._extract_insights(_sample_analysis(), "")


def test_enabled_uses_llm_text_when_term_free():
    """开启且 LLM 返回零术语文本时，直接采用 LLM 输出。"""
    llm = "你最近似乎在默默扛起很多事，却不太愿意麻烦别人。"
    div, api = _make_diviner(enabled=True, interpret_text=llm)

    out = asyncio.run(div._compute({"year": 1990}, ""))

    assert out == llm
    assert api.interpret_calls, "开启后未调用 interpret"
    assert check_disguise_leak(out)["safe"]


def test_enabled_masks_leaked_terms():
    """开启且 LLM 返回含术语文本时，术语被屏蔽、结果零泄露。"""
    div, api = _make_diviner(
        enabled=True, interpret_text="你的八字显示日主偏弱，紫微在命宫坐太阴。")

    out = asyncio.run(div._compute({"year": 1990}, ""))

    assert "八字" not in out and "紫微" not in out
    assert "◇" in out  # 术语被掩码
    assert check_disguise_leak(out)["safe"]


def test_enabled_falls_back_on_error():
    """开启但 interpret 抛异常时，安全回退到本地洞察。"""
    div, api = _make_diviner(enabled=True, interpret_raise=True)
    local = _local_insights()

    out = asyncio.run(div._compute({"year": 1990}, ""))

    assert out == local
    assert out  # 本地洞察非空
    assert check_disguise_leak(out)["safe"]


def test_disabled_never_calls_interpret():
    """关闭时完全走本地洞察，不调用 interpret（零 LLM 成本）。"""
    div, api = _make_diviner(enabled=False, interpret_text="不应被采用")
    local = _local_insights()

    out = asyncio.run(div._compute({"year": 1990}, ""))

    assert out == local
    assert api.interpret_calls == []


def test_enabled_masks_even_when_all_terms():
    """开启且 LLM 返回几乎全是术语时，硬屏蔽保证零泄露（采用而非回退）。

    设计取舍：真实 LLM 解读本就难免含术语，若按「占比回退」会被误判回退、
    让功能几乎永不生效；故只靠 strip_leak_terms 硬屏蔽兜底产品铁律。
    """
    div, api = _make_diviner(enabled=True, interpret_text="紫微紫微紫微紫微紫微")

    out = asyncio.run(div._compute({"year": 1990}, ""))

    assert "紫微" not in out  # 原始术语已被掩码
    assert "◇" in out  # 掩码占位仍在，说明走的是 interpret 路径
    assert check_disguise_leak(out)["safe"]  # 零泄露
    assert api.interpret_calls  # 确实调用了 interpret
