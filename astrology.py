
"""
astrology.py -- 命盘数据提取（共用）
====================================
从 mingbu combined 分析结果中提取结构化信息，供多个 LLM 工具复用，
避免 `query_situation` / `query_combined` / `silent_divination` 各自重复解析。

P1 去重要点：关键宫位（命宫/迁移宫/福德宫/官禄宫/财帛宫/夫妻宫）
的主星提取原本散落在 query_situation 内联循环与 silent_divination 中，
现统一收敛到 extract_key_palaces。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Optional

from .bj_time import now_bj

# 朋友关心场景下最值得"点一下"的关键宫位
KEY_PALACES = ["命宫", "迁移宫", "福德宫", "官禄宫", "财帛宫", "夫妻宫"]

# iztro（以及后端 mingbu 经 iztro 出的盘）实际宫名多数**不带"宫"字**
# （迁移 / 福德 / 官禄 / 财帛 / 夫妻，仅命宫带宫），而插件内部约定用
# 带宫字（迁移宫 …）。若按精确字符串匹配，5/6 关键宫位会被静默漏掉。
# 统一归一到插件约定，对两种命名都幂等安全（修复前 extract_key_palaces
# 对真实数据只返回命宫一个宫位）。
_PALACE_NORM = {
    "命": "命宫", "迁移": "迁移宫", "福德": "福德宫",
    "官禄": "官禄宫", "财帛": "财帛宫", "夫妻": "夫妻宫",
    "兄弟": "兄弟宫", "父母": "父母宫", "田宅": "田宅宫",
    "仆役": "仆役宫", "疾厄": "疾厄宫", "子女": "子女宫",
}


def normalize_palace_name(name) -> str:
    """把 iztro/后端真实宫名归一为插件约定（带'宫'）。

    入参 '迁移'/'迁移宫' 都返回 '迁移宫'；未知宫名原样返回。
    幂等：插件内部若已传带宫字名称，结果不变。
    """
    if not isinstance(name, str) or not name:
        return ""
    base = name[:-1] if name.endswith("宫") else name
    return _PALACE_NORM.get(base, name)


def _current_year() -> int:
    return now_bj().year


def _find_current_da_yun(bazi: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """从序列化的 bazi.daYun 数组里推算当前所处大运。

    后端 buildBaziChart 序列化的是 daYun 数组（每项含
    ganZhi/startAge/endAge/startYear/isTongXian/liuNian），并不挂
    currentDaYun/currentLiuNian 字段——那两个是后端 getCurrentDaYun() 现算的。

    准确性策略（规避虚岁/周岁口径之争）：
      1) 主路径：按 Gregorian `startYear` 匹配。大运开始的真实公历年是命理上
         最无歧义的定义，直接 `startYear <= 当前年` 取最后一个命中的大运，
         彻底绕开“年龄用虚岁还是周岁”的口径问题。
      2) 兜底：若数据缺 startYear，则按“虚岁 = 当前年-出生年+1”匹配
         startAge/endAge（后端序列化的 startAge 即虚岁）。
      3) 未成年人（当前年早于所有大运起点）= 童限，返回 None（不编造大运）；
         超龄（晚于末运起点）返回最后一个大运。
    """
    if not isinstance(bazi, dict):
        return None
    da_yun_list = bazi.get("daYun")
    if not isinstance(da_yun_list, list) or not da_yun_list:
        return None
    cur = _current_year()

    # ---- 主路径：Gregorian startYear 匹配（最精确） ----
    ordered = [
        d for d in da_yun_list
        if isinstance(d, dict) and not d.get("isTongXian")
        and isinstance(d.get("startYear"), int)
    ]
    if ordered:
        ordered.sort(key=lambda d: d["startYear"])
        selected = None
        for d in ordered:
            if d["startYear"] <= cur:
                selected = d
            else:
                break
        if selected is not None:
            return selected
        # 当前年早于所有大运起点 → 童限，无所谓大运
        if cur < ordered[0]["startYear"]:
            return None
        return ordered[-1]  # 超龄，给末运

    # ---- 兜底：按虚岁（当前年 - 出生年 + 1）匹配 ----
    birth = bazi.get("birthInfo")
    birth_year = birth.get("year") if isinstance(birth, dict) else None
    if not isinstance(birth_year, int):
        return None
    age = cur - birth_year + 1  # 虚岁（后端 startAge 即虚岁）
    valid = [
        d for d in da_yun_list
        if isinstance(d, dict) and not d.get("isTongXian")
        and isinstance(d.get("startAge"), int)
    ]
    if not valid:
        return None
    for d in valid:
        start = d["startAge"]
        end = d.get("endAge")
        end = end if isinstance(end, int) else 999
        if start <= age <= end:
            return d
    first_start = min(d["startAge"] for d in valid)
    if age < first_start:
        return None  # 童限
    return max(valid, key=lambda d: d["startAge"])  # 超龄给末运


def _current_liu_nian_ganzhi(da_yun: Dict[str, Any]) -> Optional[str]:
    """从当前大运的 liuNian 数组里取本流年干支。"""
    ln_list = da_yun.get("liuNian")
    if not isinstance(ln_list, list):
        return None
    cur = _current_year()
    for ln in ln_list:
        if isinstance(ln, dict) and ln.get("year") == cur:
            gz = ln.get("ganZhi")
            if isinstance(gz, str) and gz:
                return gz
    return None


def extract_key_palaces(analysis: Dict[str, Any]) -> Dict[str, str]:
    """从 combined 分析的 ziwei.palaces 中提取关键宫位的主星（major）。

    Args:
        analysis: mingbu `/api/combined` 返回的 `analysis` 字段（或整包 result）。

    Returns:
        {宫位名: "主星A、主星B" 或 "空宫"}
    """
    if not isinstance(analysis, dict):
        return {}
    ziwei = analysis.get("ziwei", {})
    if not isinstance(ziwei, dict):
        return {}
    palaces = ziwei.get("palaces", [])
    if not isinstance(palaces, list):
        return {}

    result: Dict[str, str] = {}
    for p in palaces:
        if not isinstance(p, dict):
            continue
        # 归一宫名后再匹配（iztro/后端多数宫名无"宫"字，否则 5/6 宫位漏掉）
        name = normalize_palace_name(p.get("name", ""))
        if name in KEY_PALACES:
            stars = p.get("stars", [])
            major = [s.get("name", "") for s in stars if s.get("type") == "major"]
            result[name] = "\u3001".join(major[:3]) if major else "空宫"
    return result


# ---- 流月推导（纯 Python，无需 lunar 库） ----
# 流月干支由「流年天干 + 当前节气月」按五虎遁推出。后端 combined 序列化的
# bazi.daYun[].liuNian[] 仅含流年（不含流月），且平台无 /api/liuyue 端点，
# 故在此用确定性规则推导，零外部依赖、可单测。
_GAN = "甲乙丙丁戊己庚辛壬癸"
_ZHI = "子丑寅卯辰巳午未申酉戌亥"
_JIAZI = [f"{_GAN[i % 10]}{_ZHI[i % 12]}" for i in range(60)]

# 五虎遁：流年天干 → 寅月（流月序号=1）在六十甲子中的位置
# 甲己→丙寅(3) 乙庚→戊寅(15) 丙辛→庚寅(27) 丁壬→壬寅(39) 戊癸→甲寅(51)
_YIN_MONTH_POS: Dict[frozenset, int] = {
    frozenset("甲己"): 3,
    frozenset("乙庚"): 15,
    frozenset("丙辛"): 27,
    frozenset("丁壬"): 39,
    frozenset("戊癸"): 51,
}

# 各「节」起始（月,日），按节气月近似当前流月（小寒起丑月…大雪起子月）
# 顺序：小寒(丑)→立春(寅)→惊蛰(卯)→清明(辰)→立夏(巳)→芒种(午)
#       →小暑(未)→立秋(申)→白露(酉)→寒露(戌)→立冬(亥)→大雪(子)
_SOLAR_TERM_BOUNDS = [
    (1, 6), (2, 4), (3, 6), (4, 5), (5, 6), (6, 6),
    (7, 7), (8, 8), (9, 8), (10, 8), (11, 7), (12, 7),
]
# 对应的流月序号（1=寅 … 12=丑），与上面边界一一对应
_SOLAR_TERM_MONTH_IDX = [12, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11]


def _current_liu_yue_index(now: Optional[datetime] = None) -> int:
    """当前流月序号（1=寅 … 12=丑），按节气月近似。

    now 可注入（测试用）；默认取 now_bj()。误差 ±1~2 天（节气漂移），
    对「后台关注」这类模糊提示足够。
    """
    if now is None:
        now = now_bj()
    m, d = now.month, now.day
    idx = 11  # 小寒前：子月（去年大雪后）
    for i, (bm, bd) in enumerate(_SOLAR_TERM_BOUNDS):
        if (m, d) >= (bm, bd):
            idx = _SOLAR_TERM_MONTH_IDX[i]
    return idx


def current_liu_yue_ganzhi(liu_nian_gz: str,
                           month_index: Optional[int] = None) -> str:
    """由流年干支推导当前流月干支（五虎遁）。

    liu_nian_gz: 流年干支，如 "丙午"。
    month_index: 流月序号(1=寅…12=丑)；缺省用 _current_liu_yue_index()。
    纯函数、可单测。返回 "" 表示无法推导。
    """
    if not isinstance(liu_nian_gz, str) or len(liu_nian_gz) < 1:
        return ""
    tg = liu_nian_gz[0]
    base = None
    for keys, pos in _YIN_MONTH_POS.items():
        if tg in keys:
            base = pos
            break
    if base is None:
        return ""
    idx = month_index if isinstance(month_index, int) else _current_liu_yue_index()
    pos = (base + idx - 2) % 60 + 1
    return _JIAZI[pos - 1]


def current_da_xian(analysis: Dict[str, Any]) -> Dict[str, str]:
    """返回当前所处紫微「大限」的宫位名与干支（若有）。命盘类「大限」专用。

    大限即紫微斗数的十年大运，落在某宫位；本函数从 combined 分析的
    ziwei.daXians 中按 currentAge 匹配当前大限，返回其宫位名（归一为
    带「宫」）与干支（若有）。纯函数、可单测；缺失则返回空 dict。
    """
    out: Dict[str, str] = {}
    if not isinstance(analysis, dict):
        return out
    ziwei = analysis.get("ziwei", {})
    if not isinstance(ziwei, dict):
        return out
    da_xians = ziwei.get("daXians")
    if not isinstance(da_xians, list) or not da_xians:
        return out
    cur_age = ziwei.get("currentAge")
    try:
        cur_age = int(cur_age) if cur_age is not None else 0
    except Exception:
        cur_age = 0
    for dx in da_xians:
        if not isinstance(dx, dict):
            continue
        start = dx.get("startAge")
        end = dx.get("endAge")
        try:
            start = int(start) if start is not None else 0
            end = int(end) if end is not None else 999
        except Exception:
            continue
        if start <= cur_age <= end:
            palace = normalize_palace_name(dx.get("palaceName", ""))
            if palace:
                out["palace"] = palace
            gz = dx.get("ganZhi") or dx.get("ganZhiLimit")
            if isinstance(gz, str) and gz:
                out["ganzhi"] = gz
            break
    return out


def current_da_yun_liu_nian(analysis: Dict[str, Any]) -> Dict[str, str]:
    """提取当前大运 / 流年 / 流月干支，供近况分析展示。

    修复：原实现读 bazi.currentDaYun / bazi.currentLiuNian，但后端并未序列化
    这两个字段（大运由后端 getCurrentDaYun() 现算，仅 daYun 数组进入响应）。
    现改为从序列化后的 bazi.daYun 数组 + 出生年推算当前大运，再取其 liuNian
    中匹配本年的流年干支；流月由流年干支按五虎遁推导（见 current_liu_yue_ganzhi）。
    """
    out: Dict[str, str] = {}
    if not isinstance(analysis, dict):
        return out
    bazi = analysis.get("bazi", {})
    if not isinstance(bazi, dict):
        return out
    da_yun = _find_current_da_yun(bazi)
    if isinstance(da_yun, dict):
        gz = da_yun.get("ganZhi")
        if isinstance(gz, str) and gz:
            out["da_yun"] = gz
        ln = _current_liu_nian_ganzhi(da_yun)
        if ln:
            out["liu_nian"] = ln
            ly = current_liu_yue_ganzhi(ln)
            if ly:
                out["liu_yue"] = ly
    return out


# ---- 岁运六冲判断（2026-09-22 P3 预感信号分级） ----
# 地支六冲：子午、丑未、寅申、卯酉、辰戌、巳亥。
# 干支字符串末位是地支（如 "甲辰" -> 辰）。六冲=命理上最直白的"冲撞/变动"
# 事件：流年冲大运（岁运逢冲，年级稀疏事件）为强信号；
# 流月冲流年（月级事件）为弱信号。宫位存在性对任何用户任何时间几乎恒真
# （12 宫里 6 个关键宫总在），无区分度，P3 起不再作为预感信号。
_LIU_CHONG = frozenset({
    frozenset(("子", "午")), frozenset(("丑", "未")),
    frozenset(("寅", "申")), frozenset(("卯", "酉")),
    frozenset(("辰", "戌")), frozenset(("巳", "亥")),
})


def di_zhi_clash(gz_a: Optional[str], gz_b: Optional[str]) -> bool:
    """判断两个干支（如 "甲辰"/"庚戌"）的地支是否六冲。

    非法/缺失输入一律返回 False（宁可不报信号，不可误报）。
    """
    if not isinstance(gz_a, str) or not isinstance(gz_b, str):
        return False
    if len(gz_a) < 2 or len(gz_b) < 2:
        return False
    zhi_a, zhi_b = gz_a[-1], gz_b[-1]
    if zhi_a not in "子丑寅卯辰巳午未申酉戌亥" or zhi_b not in "子丑寅卯辰巳午未申酉戌亥":
        return False
    return zhi_a != zhi_b and frozenset((zhi_a, zhi_b)) in _LIU_CHONG
