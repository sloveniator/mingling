"""命灵·流月推导单测（纯 Python 五虎遁，零外部依赖）。"""
from datetime import datetime

from astrology import (
    _current_liu_yue_index,
    current_da_yun_liu_nian,
    current_liu_yue_ganzhi,
)


def test_liu_yue_ganzhi_wuxu_dun():
    # 五虎遁已知结果
    assert current_liu_yue_ganzhi("甲辰", month_index=1) == "丙寅"  # 甲己→丙寅
    assert current_liu_yue_ganzhi("乙巳", month_index=5) == "壬午"  # 乙庚→戊寅, +4
    assert current_liu_yue_ganzhi("丙午", month_index=6) == "乙未"  # 丙辛→庚寅, +5
    assert current_liu_yue_ganzhi("丙午", month_index=12) == "辛丑"  # 年末丑月


def test_liu_yue_ganzhi_invalid():
    assert current_liu_yue_ganzhi("") == ""
    assert current_liu_yue_ganzhi("ZZ") == ""  # 未知天干


def test_liu_yue_index_by_solar_term():
    # 注入日期验证节气月近似
    assert _current_liu_yue_index(datetime(2026, 7, 17)) == 6   # 小暑后 → 未月
    assert _current_liu_yue_index(datetime(2026, 2, 10)) == 1   # 立春后 → 寅月
    assert _current_liu_yue_index(datetime(2026, 1, 3)) == 11   # 小寒前 → 子月
    assert _current_liu_yue_index(datetime(2026, 12, 20)) == 11  # 大雪后 → 子月


def test_da_yun_liu_nian_includes_liu_yue(monkeypatch):
    # 固定当前流月为 未月(6)，使结果确定
    import astrology
    monkeypatch.setattr(astrology, "_current_liu_yue_index", lambda: 6)
    analysis = {
        "bazi": {
            "daYun": [
                {
                    "ganZhi": "甲子",
                    "startYear": 1990,
                    "liuNian": [{"ganZhi": "丙午", "year": 2026}],
                }
            ]
        }
    }
    dyn = current_da_yun_liu_nian(analysis)
    assert dyn.get("da_yun") == "甲子"
    assert dyn.get("liu_nian") == "丙午"
    assert dyn.get("liu_yue") == "乙未"  # 丙午年未月
