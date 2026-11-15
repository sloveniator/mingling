"""
tests/test_birth_hour_normalize.py -- _birth_from 的 hour 归一化
===============================================================
命理服务端契约：birthInfo.hour 必须是 0–11 地支索引（0=子…11=亥）。
24 小时制钟点（≥12）原样直传会被服务端以 ``wrong hour 2×hour`` 拒绝
（服务端以 ``2*hour`` 作内部索引并约束 ``hour <= 11``）。

``_birth_from`` 是「档案 → 命盘」的唯一出口（background_watch / dream /
commands / proactive / silent_divination 下游均经此），故在此统一归一化：
- 0–11 地支索引：原样返回（幂等，不改动正常档案）；
- 12–23 24h 钟点：经 ``_clock_to_shichen`` 折算为地支索引；
- 异常值：原样返回交由服务端校验。
"""
import pytest

from star_mixins.profile import ProfileMixin, _apply_late_zi_swap, _normalize_birth_hour


@pytest.mark.parametrize(
    "raw, expected",
    [
        (0, 0),    # 子时（0 点）
        (5, 5),    # 巳时（0–11 已是地支索引，保持不变）
        (10, 10),  # 戌时
        (11, 11),  # 亥时
        (12, 6),   # 12 点 → 午时(6)
        (13, 7),   # 13 点 → 未时(7)
        (14, 7),   # 14 点 → 未时(7)
        (23, 0),   # 23 点 → 子时(0)
        ("14", 7), # 字符串钟点也应被折算
    ],
)
def test_normalize_clock_to_branch(raw, expected):
    assert _normalize_birth_hour(raw) == expected


@pytest.mark.parametrize("raw", [-1, 24, 100, None, "abc"])
def test_normalize_passthrough_out_of_range(raw):
    # 超出 0–23 常规范围的值原样返回，交由服务端校验（保持既有失败语义）
    assert _normalize_birth_hour(raw) is raw


def _profile(hour):
    return {
        "name": "测试",
        "birth": {"year": 1990, "month": 6, "day": 15, "hour": hour, "gender": "male"},
    }


def test_birth_from_normalizes_24h_hour():
    birth = ProfileMixin._birth_from(None, _profile(14))
    assert birth is not None
    assert birth["hour"] == 7  # 14 点（下午 2 点）→ 未时(7)


def test_birth_from_keeps_branch_index():
    birth = ProfileMixin._birth_from(None, _profile(7))
    assert birth["hour"] == 7  # 已是地支索引，保持不变


def test_birth_from_missing_hour_returns_none():
    profile = {"name": "测试", "birth": {"year": 1990, "month": 6, "day": 15, "gender": "male"}}
    assert ProfileMixin._birth_from(None, profile) is None


# ------------------------------------------------------------
# 晚子时换日（23 点出生按次日排日柱/时柱）
# ------------------------------------------------------------

def test_late_zi_swap_basic():
    assert _apply_late_zi_swap(1990, 5, 20, 23) == (1990, 5, 21, 0, True)


def test_late_zi_swap_cross_month():
    assert _apply_late_zi_swap(1990, 1, 31, 23) == (1990, 2, 1, 0, True)


def test_late_zi_swap_cross_year():
    assert _apply_late_zi_swap(1990, 12, 31, 23) == (1991, 1, 1, 0, True)


def test_late_zi_swap_leap_year():
    assert _apply_late_zi_swap(2000, 2, 28, 23) == (2000, 2, 29, 0, True)


def test_late_zi_swap_no_swap():
    # 非 23 点（含早子时 0 点）不换日
    assert _apply_late_zi_swap(1990, 5, 20, 8) == (1990, 5, 20, 8, False)
    assert _apply_late_zi_swap(1990, 5, 20, 0) == (1990, 5, 20, 0, False)
    assert _apply_late_zi_swap(1990, 5, 20, 22) == (1990, 5, 20, 22, False)


def test_birth_params_late_zi_swap():
    """_birth_params 在 23 点时应换日并归子时（hour=0）。"""
    birth = ProfileMixin._birth_params(None, 1990, 5, 20, 23, "male", "测试")
    assert birth["year"] == 1990
    assert birth["month"] == 5
    assert birth["day"] == 21       # 换日
    assert birth["hour"] == 0       # 归子时
    assert birth["gender"] == "male"


def test_birth_params_no_swap_at_8():
    birth = ProfileMixin._birth_params(None, 1990, 5, 20, 8, "male", "测试")
    assert birth["day"] == 20
    assert birth["hour"] == 4       # 8 点 → 辰时(4)
