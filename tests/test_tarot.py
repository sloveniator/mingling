"""
tests/test_tarot.py -- 塔罗引擎纯逻辑测试
"""
import random

import tarot


def test_deck_has_78_cards():
    assert len(tarot.DECK) == 78
    names = {c["name"] for c in tarot.DECK}
    assert len(names) == 78  # 无重复


def test_spreads_sizes():
    assert len(tarot.SPREADS["single"]) == 1
    assert len(tarot.SPREADS["three_card"]) == 3
    assert len(tarot.SPREADS["celtic_cross"]) == 10
    assert len(tarot.SPREADS["love"]) == 4


def test_draw_no_duplicates():
    res = tarot.draw_spread(spread="celtic_cross", question="测试")
    names = [c["name"] for c in res["cards"]]
    assert len(names) == len(set(names))  # 抽牌不重复


def test_draw_deterministic_same_day():
    r1 = tarot.draw_spread(spread="three_card", question="同一问题", seed=12345)
    r2 = tarot.draw_spread(spread="three_card", question="同一问题", seed=12345)
    assert [c["name"] for c in r1["cards"]] == [c["name"] for c in r2["cards"]]
    assert [c["orientation"] for c in r1["cards"]] == [c["orientation"] for c in r2["cards"]]


def test_draw_different_seed_differs():
    """不同 seed 必须产生不同结果——否则 seed 形同虚设。

    原实现只断言 len(cards)==3，即使 seed 被完全忽略也能通过。
    改为跨 10 个 seed 统计去重结果数：单次碰撞是正常的，
    但 10 次全撞在 78 张牌的三张牌阵下不可能发生。
    """
    outcomes = {
        tuple(
            (c["name"], c["orientation"])
            for c in tarot.draw_spread(spread="three_card", question="q", seed=s)["cards"]
        )
        for s in range(1, 11)
    }
    assert len(outcomes) >= 8, f"seed 未生效或熵不足，10 个 seed 只得到 {len(outcomes)} 种结果"


def test_unknown_spread_falls_back():
    res = tarot.draw_spread(spread="不存在的牌阵", question="x")
    assert res["spread"] == "不存在的牌阵"
    assert len(res["cards"]) == len(tarot.SPREADS["three_card"])


def test_orientation_values():
    rng = random.Random(7)
    res = tarot.draw_spread(spread="single", question="q", rng=rng)
    assert res["cards"][0]["orientation"] in ("up", "rev")


def test_build_prompt_contains_cards():
    res = tarot.draw_spread(spread="three_card", question="我会好转吗")
    prompt = tarot.build_tarot_prompt(res, "我会好转吗")
    assert "塔罗" in prompt
    assert res["cards"][0]["name"] in prompt


def test_spread_for_question_mapping():
    assert tarot.spread_for_question("love") == "love"
    assert tarot.spread_for_question("career") == "career"
    assert tarot.spread_for_question("unknown") == "three_card"
