"""出生年份收集的语义判定回归测试（v4.8.105）。

历史缺陷：用户说"分析我在2022-2035年的感情运势"时，filter 正则
(r"我是?|我的.*?(\\d{4})\\s*年") 误命中"我…2035年"，且 BIRTH_YEAR_RE
把 "-2035年" 的后段当成出生年，导致档案被写坏成 2035。
修复：handler 内改用 _extract_birth_year_from_msg 做出生语义判定 +
范围排除 + 未来年份排除。
"""
from main import _extract_birth_year_from_msg


def test_range_year_not_collected():
    """运势时间范围（2022-2035年）不得被误判为出生年。"""
    assert _extract_birth_year_from_msg(
        "分析我在2022-2035年的感情运势，并给关键时间"
    ) is None


def test_plain_range_without_birth_semantic_ignored():
    """没有任何出生语义词的消息，即使含 4 位年份也不采信。"""
    assert _extract_birth_year_from_msg(
        "帮我看看2025年的事业运"
    ) is None


def test_normal_birth_year_collected():
    """常规出生句式应正常采集。"""
    assert _extract_birth_year_from_msg("我1990年生的，男") == 1990
    assert _extract_birth_year_from_msg("我出生在1990年") == 1990
    assert _extract_birth_year_from_msg("我是1990年5月20日早上8点生的") == 1990


def test_birth_hint_with_range_still_excluded():
    """即使有出生语义词，命中范围年（-2035年）仍应排除。"""
    assert _extract_birth_year_from_msg(
        "我1988年生，分析我在2022-2035年的感情运势"
    ) == 1988


def test_future_year_rejected():
    """未来年份（超过当前年）不可能是出生年。"""
    from datetime import datetime
    future = datetime.now().year + 1
    assert _extract_birth_year_from_msg(f"我{future}年生的") is None


def test_out_of_range_year_rejected():
    """超出合理范围（<1900）拒绝。"""
    assert _extract_birth_year_from_msg("我1800年生的") is None


def test_zodiac_triggered_year_collected():
    """属相句式含年份时采集（属羊1991年生）。"""
    assert _extract_birth_year_from_msg("我属羊，1991年生的") == 1991
