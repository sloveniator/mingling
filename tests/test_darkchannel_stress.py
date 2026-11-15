"""
tests/test_darkchannel_stress.py -- 暗盘(静默排盘)准确性压力测试
=================================================================
基于「原先 mingbu 经验」（lunar-javascript 八字引擎 + iztro 紫微引擎，
即后端同一套计算内核）生成的真值 fixture，对暗盘做全面准确性验证：

  1. 跨真实出生数据交叉验证：插件选出的大运/流年/命宫主星，
     是否等于命理正确值（公历年 startYear 匹配 → 最精确口径）。
  2. 边界/异常压力：童限(未成年)、大运起止年精确命中、超龄、
     缺失字段、流年缺失等，确认不崩溃且语义正确。
  3. 护栏模糊测试：_extract_insights / premonition_cue 在覆盖全部
     五行/旺衰/地支/主星组合下，绝不泄漏命理禁用词。
  4. 知识映射覆盖：BRANCH_LIFE_PHASES 等字典覆盖全 12 地支且无禁用词。

运行：.venv/Scripts/python -m pytest tests/test_darkchannel_stress.py -q
"""
import asyncio
import inspect
import json
import random
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

import astrology as _ast
import silent_divination as _sd
from astrology import (
    KEY_PALACES,
    _find_current_da_yun,
    current_da_yun_liu_nian,
    extract_key_palaces,
    normalize_palace_name,
)
from silent_divination import (
    BRANCH_LIFE_PHASES,
    ELEMENT_ENERGY,
    PALACE_DOMAINS,
    STRENGTH_TENDENCY,
    ZIWEI_PERSONALITY,
    SilentDiviner,
    weave_insights_into_prompt,
)

FIXTURES_LARGE = Path(__file__).parent.parent / "oracle_fixtures_large.json"

# 命理禁用词（暗盘伪装铁律：朋友口吻中绝不能出现）
FORBIDDEN = ["命盘", "八字", "紫微", "占卜", "卜卦", "卦象",
             "运势", "排盘", "起卦", "算命", "流年"]
# 扩展技术词（朋友口吻中同样不应出现）
FORBIDDEN_EXT = ["大运", "命理", "斗数", "星曜", "宫位", "主星", "起运",
                "旬空", "纳音", "十神", "日主", "五行", "天干", "地支",
                "大限", "流月", "流日", "流时"]

FIXTURES = Path(__file__).parent.parent / "oracle_fixtures.json"
_ORIG_YEAR = _ast._current_year


def load_fixtures():
    with open(FIXTURES, encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture(autouse=True)
def _pin_year():
    # 固定“当前年”= fixture 的 currentYear，保证与 liuNian 年份一致
    yr = load_fixtures()[0]["currentYear"]
    _ast._current_year = lambda: yr
    yield
    _ast._current_year = _ORIG_YEAR


def _set_year(y):
    _ast._current_year = lambda: y


# ============================================================
# 1. 跨真实出生数据交叉验证（命理引擎真值）
# ============================================================

def test_crossvalidate_da_yun_liu_nian_against_engine():
    fxs = load_fixtures()
    drifts = []  # 后端周岁口径 ≠ 命理精确口径 的样本
    for fx in fxs:
        birth = fx["birth"]
        analysis = {
            "bazi": {"birthInfo": birth, "daYun": fx["daYun_fixture"]},
            "ziwei": fx["ziwei"],
        }
        res = current_da_yun_liu_nian(analysis)
        dy = res.get("da_yun")
        ln = res.get("liu_nian")

        # 命理精确真值 = gregorian startYear 匹配得到的大运
        exp_dy = fx["truth_gregorian_da_yun"]
        assert dy == exp_dy, (
            f"大运不符: {birth} got={dy} 命理正确={exp_dy}")

        # 期望流年：该大运中 year==currentYear 的干支（独立于大运选择）
        exp_ln = ""
        for d in fx["daYun_fixture"]:
            if d.get("ganZhi") == exp_dy:
                for e in d.get("liuNian", []):
                    if e.get("year") == fx["currentYear"]:
                        exp_ln = e.get("ganZhi")
                        break
        assert ln == exp_ln, f"流年不符: {birth} got={ln} exp={exp_ln}"

        # 非漂移样本：插件结果应等于后端周岁口径（一致未回归）
        if fx["truth_backend_da_yun"] == exp_dy:
            assert dy == fx["truth_backend_da_yun"]

        if fx["truth_backend_da_yun"] != exp_dy:
            drifts.append((birth, fx["truth_backend_da_yun"], exp_dy))

    # 漂移仅发生在大运边界（后端周岁口径的固有限制），数量应很小
    assert len(drifts) <= 4, f"周岁/虚岁漂移样本过多: {drifts}"


def test_crossvalidate_key_palaces_against_iztro():
    fxs = load_fixtures()
    for fx in fxs:
        analysis = {"ziwei": fx["ziwei"]}
        res = extract_key_palaces(analysis)
        ming = res.get("命宫")
        stars = fx["ziwei"]["mingStars"]
        exp = "、".join(stars) if stars else "空宫"
        assert ming == exp, (
            f"命宫主星提取不符: {fx['birth']} got={ming} exp={exp}")


# ============================================================
# 2. 边界 / 异常压力（确定性，monkeypatch 当前年）
# ============================================================

def _build_standard_da_yun(base_year, with_start_year=True):
    """标准 7 段大运：童限占位 + 甲子..己巳 六步（每步 10 年）。"""
    steps = [
        ("", 1, 7, True),
        ("甲子", 8, 17, False),
        ("乙丑", 18, 27, False),
        ("丙寅", 28, 37, False),
        ("丁卯", 38, 47, False),
        ("戊辰", 48, 57, False),
        ("己巳", 58, 67, False),
    ]
    out, idx = [], 0
    for gz, sa, ea, tx in steps:
        if tx:
            sy = None
        else:
            sy = base_year + idx * 10 if with_start_year else None
            idx += 1
        liu = [{"year": sy + i, "ganZhi": f"L{i:02d}"} for i in range(10)] if sy else []
        out.append({"ganZhi": gz, "startAge": sa, "endAge": ea,
                    "startYear": sy, "isTongXian": tx, "liuNian": liu})
    return out


def _analysis(da_yun, birth_year=None):
    bazi = {"daYun": da_yun}
    if birth_year is not None:
        bazi["birthInfo"] = {"year": birth_year}
    return {"bazi": bazi}


def test_child_below_first_da_yun_returns_none():
    # 当前年早于所有大运起点 → 童限，必须返回 None（禁止编造末运）
    dy = _build_standard_da_yun(base_year=2010)
    _set_year(2008)  # 2008 < 2010
    assert _find_current_da_yun(_analysis(dy).get("bazi")) is None
    assert current_da_yun_liu_nian(_analysis(dy)) == {}


def test_exact_start_year_boundary():
    dy = _build_standard_da_yun(base_year=2010)
    _set_year(2010)  # 甲子起始当年
    assert _find_current_da_yun(_analysis(dy).get("bazi"))["ganZhi"] == "甲子"
    _set_year(2019)  # 甲子末年
    assert _find_current_da_yun(_analysis(dy).get("bazi"))["ganZhi"] == "甲子"
    _set_year(2020)  # 乙丑起始当年
    assert _find_current_da_yun(_analysis(dy).get("bazi"))["ganZhi"] == "乙丑"


def test_last_da_yun_and_overage():
    dy = _build_standard_da_yun(base_year=2010)
    _set_year(2068)  # 己巳起始当年 (base+50)
    assert _find_current_da_yun(_analysis(dy).get("bazi"))["ganZhi"] == "己巳"
    _set_year(2100)  # 远超末运 → 回退末运（不崩溃）
    assert _find_current_da_yun(_analysis(dy).get("bazi"))["ganZhi"] == "己巳"


def test_missing_fields_robust():
    # 空 / 缺失 daYun
    assert _find_current_da_yun({}) is None
    assert _find_current_da_yun({"daYun": []}) is None
    assert _find_current_da_yun({"daYun": None}) is None
    # 缺 birthInfo 且无 startYear → 兜底也拿不到年龄 → None
    dy = [{"ganZhi": "甲子", "startAge": 8, "endAge": 17, "isTongXian": False, "liuNian": []}]
    assert _find_current_da_yun({"daYun": dy}) is None
    # 整体空 analysis
    assert current_da_yun_liu_nian({}) == {}
    assert current_da_yun_liu_nian({"bazi": {}}) == {}


def test_liu_nian_missing_for_current_da_yun():
    # 当前大运存在但无 liuNian → 大运有、流年无
    dy = _build_standard_da_yun(base_year=2010)
    dy[1]["liuNian"] = []  # 甲子无流年
    _set_year(2015)
    res = current_da_yun_liu_nian(_analysis(dy))
    assert res.get("da_yun") == "甲子"
    assert "liu_nian" not in res


def test_virtual_age_fallback_without_start_year():
    # 无 startYear 时走虚岁兜底；出生年使虚岁落入甲子[8,17]
    dy = _build_standard_da_yun(base_year=2010, with_start_year=False)
    _set_year(2018)  # 虚岁 = 2018 - (2010-8) + 1 = 17 → 甲子
    assert _find_current_da_yun(_analysis(dy, birth_year=2002).get("bazi"))["ganZhi"] == "甲子"
    # 童限：虚岁 4 < 8 → None
    _set_year(2006)
    assert _find_current_da_yun(_analysis(dy, birth_year=2002).get("bazi")) is None


# ============================================================
# 3. 护栏模糊测试：零命理术语泄漏
# ============================================================

def test_extract_insights_zero_forbidden_all_branches():
    fxs = load_fixtures()
    cur = fxs[0]["currentYear"]
    elements = ["金", "木", "水", "火", "土"]
    strengths = ["身旺", "身弱", "中和", "从强", "从弱"]
    ziwei_stars = list(ZIWEI_PERSONALITY.keys())
    domain_palaces = list(PALACE_DOMAINS.keys())  # 命宫/迁移宫/福德宫/官禄宫/财帛宫/夫妻宫
    branches = list(BRANCH_LIFE_PHASES.keys())     # 12 地支
    all_terms = FORBIDDEN + FORBIDDEN_EXT

    count = 0
    for ei, elem in enumerate(elements):
        for lvl in strengths:
            for bi, branch in enumerate(branches):
                dy_gz = "玄" + branch          # 大运干支，末位=地支
                ln_gz = "玄" + branches[(bi + 1) % 12]
                da_yun = [
                    {"ganZhi": "", "startAge": 1, "endAge": 7,
                     "isTongXian": True, "liuNian": []},
                    {"ganZhi": dy_gz, "startAge": 8, "endAge": 107,
                     "startYear": cur, "isTongXian": False,
                     "liuNian": [{"year": cur, "ganZhi": ln_gz}]},
                ]
                palaces = []
                for pi, pname in enumerate(domain_palaces):
                    star = ziwei_stars[(ei * 6 + pi) % len(ziwei_stars)]
                    palaces.append({
                        "name": pname,
                        "isMingGong": pname == "命宫",
                        "stars": [{"name": star, "type": "major"}],
                    })
                analysis = {
                    "bazi": {
                        "birthInfo": {"year": cur - 30},
                        "dayMaster": {"element": elem},
                        "strength": {"level": lvl},
                        "daYun": da_yun,
                    },
                    "ziwei": {
                        "palaces": palaces,
                        "currentAge": 30,
                        "daXians": [{"startAge": 20, "endAge": 39,
                                     "palaceName": domain_palaces[0]}],
                    },
                }
                out = SilentDiviner(None)._extract_insights(analysis, "压力")
                assert out, "洞察不应为空"
                for w in all_terms:
                    assert w not in out, (
                        f"禁用词泄漏[{elem}/{lvl}/{branch}/{star}]: {w}\n-->{out}")
                count += 1
    assert count == len(elements) * len(strengths) * len(branches)


class _FakeApi:
    def __init__(self, analysis):
        self._a = analysis

    async def combined(self, birth):
        return {"analysis": self._a}


def test_premonition_cue_zero_forbidden():
    fxs = load_fixtures()
    all_terms = FORBIDDEN + FORBIDDEN_EXT
    for fx in fxs:
        analysis = {"bazi": {"birthInfo": fx["birth"],
                             "daYun": fx["daYun_fixture"]},
                    "ziwei": fx["ziwei"]}
        cue = asyncio.run(SilentDiviner(_FakeApi(analysis))
                           .premonition_cue("u", fx["birth"], ""))
        if cue:
            for w in all_terms:
                assert w not in cue, f"premonition_cue 泄漏禁用词: {w} -> {cue}"
    # 无出生信息 / 无 API → 必须返回 None（优雅降级）
    assert asyncio.run(SilentDiviner(None).premonition_cue("u", fxs[0]["birth"], "")) is None
    assert asyncio.run(SilentDiviner(_FakeApi({})).premonition_cue("u", None, "")) is None


# ============================================================
# 4. 知识映射覆盖
# ============================================================

def test_knowledge_mapping_coverage():
    assert set(BRANCH_LIFE_PHASES.keys()) == set("子丑寅卯辰巳午未申酉戌亥")
    all_terms = FORBIDDEN + FORBIDDEN_EXT
    for name, d in {
        "BRANCH_LIFE_PHASES": BRANCH_LIFE_PHASES,
        "ELEMENT_ENERGY": ELEMENT_ENERGY,
        "STRENGTH_TENDENCY": STRENGTH_TENDENCY,
        "ZIWEI_PERSONALITY": ZIWEI_PERSONALITY,
        "PALACE_DOMAINS": PALACE_DOMAINS,
    }.items():
        assert len(d) >= 5, f"{name} 映射过薄"
        for v in d.values():
            assert isinstance(v, str) and v.strip(), f"{name} 含空值"
            for w in all_terms:
                assert w not in v, f"{name} 含禁用词: {w} -> {v}"


def test_darkchannel_single_direction_guard():
    # 暗盘不得反向 import/依赖 dream 模块（保持“暗盘不进梦境”单向）
    src = inspect.getsource(_sd)
    assert "import dream" not in src
    assert "from dream" not in src


# ============================================================
# 5. 全关键宫位交叉验证（宫名归一回归守卫）
# ============================================================

def _load_all():
    data = load_fixtures()
    if FIXTURES_LARGE.exists():
        data = data + json.load(open(FIXTURES_LARGE, encoding="utf-8"))
    return data


def test_crossvalidate_all_key_palaces_against_iztro():
    """宫名归一修复的回归守卫：extract_key_palaces 必须对真实 iztro 数据
    返回全部 6 个关键宫位（修复前只返回命宫，漏掉 5/6）。"""
    fxs = _load_all()
    for fx in fxs:
        analysis = {"ziwei": fx["ziwei"]}
        res = extract_key_palaces(analysis)
        # 期望：对每个 KEY_PALACE，从 fixture 实际 palaces 取 major 星
        expected = {}
        for p in fx["ziwei"]["palaces"]:
            nk = normalize_palace_name(p["name"])
            if nk in KEY_PALACES:
                majors = [s["name"] for s in p.get("stars", []) if s.get("type") == "major"]
                expected[nk] = "、".join(majors[:3]) if majors else "空宫"
        assert set(res.keys()) == set(expected.keys()), (
            f"宫位集合不符 {fx['birth']}: got {sorted(res)} exp {sorted(expected)}")
        for k, v in expected.items():
            assert res.get(k) == v, f"{k} 主星不符 {fx['birth']}: got={res.get(k)} exp={v}"
    # 强守卫：每个样本都必须提取到全部 6 宫位（修复后不再只有命宫）
    assert all(len(extract_key_palaces({"ziwei": fx["ziwei"]})) == 6 for fx in fxs)


# ============================================================
# 6. 大数据量：真实引擎样本准确率 + 吞吐（要求③⑤）
# ============================================================

def _expected_liu_nian(fx):
    for d in fx["daYun_fixture"]:
        if d.get("ganZhi") == fx["truth_gregorian_da_yun"]:
            for e in d.get("liuNian", []):
                if e.get("year") == fx["currentYear"]:
                    return e.get("ganZhi")
    return ""


def test_large_data_da_yun_liu_nian_accuracy_and_throughput():
    """大数据量场景：
    准确性指标：大运/流年 100% 对齐真实引擎(公历口径)真值。
    通过标准：错误数=0 且 吞吐>100 条/秒。"""
    fxs = _load_all()
    n = len(fxs)
    t0 = time.perf_counter()
    mism_dy = mism_ln = drift = 0
    for fx in fxs:
        analysis = {
            "bazi": {"birthInfo": fx["birth"], "daYun": fx["daYun_fixture"]},
            "ziwei": fx["ziwei"],
        }
        res = current_da_yun_liu_nian(analysis)
        if res.get("da_yun") != fx["truth_gregorian_da_yun"]:
            mism_dy += 1
        if res.get("liu_nian") != _expected_liu_nian(fx):
            mism_ln += 1
        if fx["truth_backend_da_yun"] != fx["truth_gregorian_da_yun"]:
            drift += 1
    dt = time.perf_counter() - t0
    rate = n / dt
    print(f"\n[大数据量] 样本={n} 耗时={dt:.3f}s 吞吐={rate:.0f}/s "
          f"大运错={mism_dy} 流年错={mism_ln} 后端漂移={drift}")
    assert mism_dy == 0, f"大运错误 {mism_dy}/{n}"
    assert mism_ln == 0, f"流年错误 {mism_ln}/{n}"
    assert rate > 100, f"吞吐过低 {rate:.0f}/s"


# ============================================================
# 7. 合成大规模差分：_find_current_da_yun 边界全覆盖（要求③）
# ============================================================

def _ref_find_da_yun(da_yun_list, cur):
    """独立参考实现：取最后一个 startYear<=cur 的非童限大运；
    早于首运返回 None；超龄返回末运。"""
    ordered = [d for d in da_yun_list
               if not d.get("isTongXian") and isinstance(d.get("startYear"), int)]
    if not ordered:
        return None
    ordered.sort(key=lambda d: d["startYear"])
    sel = None
    for d in ordered:
        if d["startYear"] <= cur:
            sel = d["ganZhi"]
        else:
            break
    if sel is not None:
        return sel
    if cur < ordered[0]["startYear"]:
        return None
    return ordered[-1]["ganZhi"]


def test_synthetic_large_scale_da_yun_differential():
    """用 800 个合成的合法 daYun 序列（覆盖任意 startYear 边界/超龄/童限），
    与独立参考实现做差分，验证 _find_current_da_yun 在大批量下 100% 准确。"""
    random.seed(20260715)
    cur = 2026
    _set_year(cur)
    N = 800
    mism = 0
    t0 = time.perf_counter()
    for _ in range(N):
        base = random.randint(1990, 2010)
        steps = []
        for i in range(random.randint(7, 9)):
            steps.append({
                "ganZhi": f"X{i}",
                "startAge": 8 + i * 10, "endAge": 17 + i * 10,
                "startYear": base + i * 10, "isTongXian": False, "liuNian": [],
            })
        full = [{"ganZhi": "", "startAge": 1, "endAge": 7,
                 "startYear": base - 8, "isTongXian": True, "liuNian": []}] + steps
        bazi = {"daYun": full, "birthInfo": {"year": base - 30}}
        got = _find_current_da_yun(bazi)
        exp = _ref_find_da_yun(full, cur)
        got_gz = got["ganZhi"] if isinstance(got, dict) else None
        if got_gz != exp:
            mism += 1
    dt = time.perf_counter() - t0
    rate = N / dt
    print(f"\n[合成大数据] N={N} 耗时={dt:.3f}s 吞吐={rate:.0f}/s mism={mism}")
    assert mism == 0, f"差分不匹配 {mism}/{N}"
    assert rate > 2000, f"吞吐过低 {rate:.0f}/s"


# ============================================================
# 8. 高并发：零串扰 + 零异常 + 降级稳健（要求③）
# ============================================================

def _bkey(b):
    return (b["year"], b["month"], b["day"], b["hour"], b["gender"])


class _MapApi:
    """按 birth 返回对应 analysis 的假 API（无共享可变状态）。"""

    def __init__(self, mapping):
        self._m = mapping

    async def combined(self, birth):
        return {"analysis": self._m[_bkey(birth)]}


def test_high_concurrency_pure_functions_no_contamination():
    """纯函数（current_da_yun_liu_nian）在 32 线程并发下，每条结果必须
    精确等于自身样本的真值（零串扰、零全局状态污染）。"""
    fxs = _load_all()
    mapping = {
        _bkey(fx["birth"]): {
            "bazi": {"birthInfo": fx["birth"], "daYun": fx["daYun_fixture"]},
            "ziwei": fx["ziwei"],
        } for fx in fxs
    }

    def worker(fx):
        a = mapping[_bkey(fx["birth"])]
        return _bkey(fx["birth"]), current_da_yun_liu_nian(a)

    with ThreadPoolExecutor(max_workers=32) as ex:
        results = list(ex.map(worker, fxs))

    for birth, res in results:
        fx = next(f for f in fxs if _bkey(f["birth"]) == birth)
        assert res.get("da_yun") == fx["truth_gregorian_da_yun"], (
            f"并发结果串扰? {birth}: got={res.get('da_yun')} exp={fx['truth_gregorian_da_yun']}")
        assert res.get("liu_nian") == _expected_liu_nian(fx)


def test_high_concurrency_async_insights_and_cue():
    """异步高并发：get_insights + premonition_cue 在共享 SilentDiviner 实例 +
    缓存下并发执行，必须零异常、零术语泄漏、各 uid 结果独立正确。"""
    fxs = _load_all()[:60]
    mapping = {
        _bkey(fx["birth"]): {
            "bazi": {"birthInfo": fx["birth"], "daYun": fx["daYun_fixture"]},
            "ziwei": fx["ziwei"],
        } for fx in fxs
    }
    api = _MapApi(mapping)
    div = SilentDiviner(api)
    all_terms = FORBIDDEN + FORBIDDEN_EXT

    async def run():
        tasks = []
        for i, fx in enumerate(fxs):
            uid = f"u{i}"
            tasks.append(div.get_insights(uid, fx["birth"], "焦虑"))
            tasks.append(div.premonition_cue(uid, fx["birth"], ""))
        return await asyncio.gather(*tasks)

    results = asyncio.run(run())
    assert len(results) == len(fxs) * 2
    for i, fx in enumerate(fxs):
        ins = results[i * 2]
        cue = results[i * 2 + 1]
        assert isinstance(ins, str) and ins, f"洞察为空 {fx['birth']}"
        for w in all_terms:
            assert w not in ins, f"并发洞察泄漏术语 {w} @ {fx['birth']}"
        if cue:
            for w in all_terms:
                assert w not in cue, f"并发 cue 泄漏术语 {w} @ {fx['birth']}"

    # 缓存/无状态确定性：同 uid+同 birth 复现须一致
    again = asyncio.run(div.get_insights("u0", fxs[0]["birth"], "焦虑"))
    assert again == results[0]


def test_high_concurrency_none_degradation():
    """高并发下“无出生信息”必须稳定降级为 None（不抛异常、不串扰）。"""
    div = SilentDiviner(_MapApi({}))

    async def run_none():
        tasks = [div.premonition_cue(f"n{i}", None, "") for i in range(24)]
        return await asyncio.gather(*tasks)

    out = asyncio.run(run_none())
    assert all(r is None for r in out)


# ============================================================
# 9. 输入模块：异常/缺失输入优雅降级（要求②③）
# ============================================================

def test_input_module_degradation():
    div = SilentDiviner(None)
    # 无出生信息
    assert asyncio.run(div.get_insights("u", None, "")) is None
    assert asyncio.run(div.premonition_cue("u", None, "")) is None
    # 有 birth 但无 api（api=None → 直接返回 None）
    assert asyncio.run(SilentDiviner(None).get_insights("u", {"year": 1990}, "")) is None

    # 处理层对畸形输入稳健
    assert current_da_yun_liu_nian(None) == {}
    assert current_da_yun_liu_nian({}) == {}
    assert current_da_yun_liu_nian({"bazi": {}}) == {}
    assert extract_key_palaces(None) == {}
    assert extract_key_palaces({}) == {}
    assert _find_current_da_yun(None) is None
    assert _find_current_da_yun("not-a-dict") is None
    assert _find_current_da_yun({"daYun": "bad"}) is None


# ============================================================
# 10. 输出模块：weave 包装 + _extract_insights 内容准确性（要求②）
# ============================================================

def test_output_module_weave_and_extract():
    all_terms = FORBIDDEN + FORBIDDEN_EXT
    # weave 空输入
    assert weave_insights_into_prompt("") == ""
    # weave 正常输入：包含原文 + 守卫提示 + 零术语
    ins = "  • 你有一种内在的决断力\n  • 你现在正处在蓄力的时期"
    wrapped = weave_insights_into_prompt(ins)
    assert ins in wrapped
    assert "说不清的直觉" in wrapped
    for w in all_terms:
        assert w not in wrapped, f"weave 泄漏术语 {w}"

    # _extract_insights 内容准确性：大运地支→人生阶段映射必须正确
    fx = load_fixtures()[0]
    analysis = {
        "bazi": {"birthInfo": fx["birth"], "daYun": fx["daYun_fixture"]},
        "ziwei": fx["ziwei"],
    }
    out = SilentDiviner(None)._extract_insights(analysis, "焦虑")
    assert out, "洞察不应为空"
    for w in all_terms:
        assert w not in out, f"洞察泄漏术语 {w}"
    # 验证大运地支映射正确：truth_gregorian 末位地支对应的文案应出现
    dy_zhi = fx["truth_gregorian_da_yun"][-1]
    assert BRANCH_LIFE_PHASES[dy_zhi] in out, (
        f"大运地支映射错误: {dy_zhi} 文案未出现")


# ============================================================
# 11. daXian 宫名归一（非后缀宫名也能映射，修复守卫）
# ============================================================

def test_daxian_palace_normalization():
    dy = _build_standard_da_yun(base_year=2010)
    palaces = [
        {"name": "福德", "stars": [{"name": "太阳", "type": "major"}]},  # 非后缀
        {"name": "命宫", "isMingGong": True, "stars": [{"name": "紫微", "type": "major"}]},
    ]
    analysis = {
        "bazi": {"birthInfo": {"year": 2000}, "daYun": dy},
        "ziwei": {
            "palaces": palaces,
            "currentAge": 30,
            "daXians": [{"startAge": 20, "endAge": 39, "palaceName": "福德"}],  # 非后缀
        },
    }
    out = SilentDiviner(None)._extract_insights(analysis, "")
    # daXian（非后缀宫名）应被正确归一为“你内心的幸福感来源”
    assert "你这几年的主题围绕着你内心的幸福感来源展开" in out, out
    # 关键宫位（非后缀宫名 福德）也应被识别（输出用领域描述，非宫名）
    assert "在你内心的幸福感来源方面，你温暖慷慨" in out, out
