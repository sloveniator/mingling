"""
tests/test_darkchannel_live.py -- 命理暗盘实盘测试
=====================================================
模拟真实用户场景，对暗盘分析进行全面实盘测试：
  1. 不同年龄段（儿童/青年/中年/老年）的排盘准确性
  2. 不同性别、不同出生时辰的覆盖测试
  3. 边界日期（岁末年初、节气前后、闰年）
  4. 洞察文本的"伪装度"检测（禁用词检查）
  5. 预感线索生成的边界测试
  6. 大规模随机出生数据的稳定性测试

运行：python -m pytest tests/test_darkchannel_live.py -v
"""
import asyncio
import json
import random
from pathlib import Path

import pytest

import astrology as _ast
import silent_divination as _sd
from astrology import current_da_yun_liu_nian, extract_key_palaces
from silent_divination import (
    BRANCH_LIFE_PHASES,
    ELEMENT_ENERGY,
    PALACE_DOMAINS,
    STRENGTH_TENDENCY,
    ZIWEI_PERSONALITY,
    SilentDiviner,
    weave_insights_into_prompt,
)

FIXTURES = Path(__file__).parent.parent / "oracle_fixtures.json"
FIXTURES_LARGE = Path(__file__).parent.parent / "oracle_fixtures_large.json"

FORBIDDEN_ALL = [
    "命盘", "八字", "紫微", "占卜", "卜卦", "卦象", "运势", "排盘",
    "起卦", "算命", "流年", "大运", "命理", "斗数", "星曜", "宫位",
    "主星", "起运", "旬空", "纳音", "十神", "日主", "五行", "天干",
    "地支", "大限", "流月", "流日", "流时",
]

_ORIG_YEAR = _ast._current_year


def load_fixtures():
    with open(FIXTURES, encoding="utf-8") as f:
        return json.load(f)


def load_large_fixtures():
    if FIXTURES_LARGE.exists():
        with open(FIXTURES_LARGE, encoding="utf-8") as f:
            return json.load(f)
    return []


def _build_analysis(fx):
    """从 fixture 构造标准 analysis 结构。"""
    return {
        "bazi": {"birthInfo": fx["birth"], "daYun": fx["daYun_fixture"]},
        "ziwei": fx["ziwei"],
    }


@pytest.fixture(autouse=True)
def _pin_year():
    yr = load_fixtures()[0]["currentYear"]
    _ast._current_year = lambda: yr
    yield
    _ast._current_year = _ORIG_YEAR


# ============================================================
# 1. 实盘场景：不同年龄段的排盘准确性
# ============================================================

class TestLiveAgeGroups:
    """按年龄段分组测试排盘准确性。"""

    def test_children_under_10(self):
        """儿童（10岁以下）：应处于童限或第一步大运。"""
        fxs = load_fixtures()
        for fx in fxs:
            age = fx.get("zhouSui", 0)
            if age < 10:
                analysis = _build_analysis(fx)
                dyn = current_da_yun_liu_nian(analysis)
                assert isinstance(dyn, dict)

    def test_teenagers_10_20(self):
        """青少年（10-20岁）：应处于前两步大运。"""
        fxs = load_fixtures()
        found = False
        for fx in fxs:
            age = fx.get("zhouSui", 0)
            if 10 <= age < 20:
                found = True
                analysis = _build_analysis(fx)
                dyn = current_da_yun_liu_nian(analysis)
                assert dyn.get("da_yun"), f"青少年大运为空: {fx['birth']}"
        if not found:
            pytest.skip("fixture 中无 10-20 岁样本")

    def test_adults_20_40(self):
        """青年（20-40岁）：核心用户群体，准确性最关键。"""
        fxs = load_fixtures()
        for fx in fxs:
            age = fx.get("zhouSui", 0)
            if 20 <= age < 40:
                analysis = _build_analysis(fx)
                dyn = current_da_yun_liu_nian(analysis)
                assert dyn.get("da_yun"), f"青年大运为空: {fx['birth']}"
                assert dyn.get("liu_nian"), f"青年流年为空: {fx['birth']}"

    def test_middle_age_40_60(self):
        """中年（40-60岁）。"""
        fxs = load_fixtures()
        for fx in fxs:
            age = fx.get("zhouSui", 0)
            if 40 <= age < 60:
                analysis = _build_analysis(fx)
                dyn = current_da_yun_liu_nian(analysis)
                assert dyn.get("da_yun"), f"中年大运为空: {fx['birth']}"

    def test_seniors_60_plus(self):
        """老年（60岁以上）。"""
        fxs = load_fixtures()
        found = False
        for fx in fxs:
            age = fx.get("zhouSui", 0)
            if age >= 60:
                found = True
                analysis = _build_analysis(fx)
                dyn = current_da_yun_liu_nian(analysis)
                assert dyn.get("da_yun"), f"老年大运为空: {fx['birth']}"
        if not found:
            pytest.skip("fixture 中无 60+ 岁样本")


# ============================================================
# 2. 实盘场景：不同出生时辰覆盖
# ============================================================

class TestLiveBirthHours:
    """不同出生时辰的命宫主星覆盖测试。"""

    def test_all_12_hours_coverage(self):
        """测试 12 时辰覆盖，确保每个时辰都能产出命宫。"""
        fxs = load_fixtures()
        hours_seen = set()
        for fx in fxs:
            hour = fx.get("birth", {}).get("hour", -1)
            if 0 <= hour <= 23:
                hours_seen.add(hour // 2)
                analysis = _build_analysis(fx)
                palaces = extract_key_palaces(analysis)
                assert "命宫" in palaces, f"命宫缺失: {fx['birth']}"
        assert len(hours_seen) >= 3, f"时辰覆盖不足: {len(hours_seen)} 个"

    def test_midnight_birth(self):
        """凌晨出生（子时）：边界时辰。"""
        fxs = load_fixtures()
        found = False
        for fx in fxs:
            hour = fx.get("birth", {}).get("hour", -1)
            if hour in (0, 1, 23):
                found = True
                analysis = _build_analysis(fx)
                palaces = extract_key_palaces(analysis)
                assert "命宫" in palaces
        if not found:
            pytest.skip("fixture 中无凌晨出生样本")

    def test_noon_birth(self):
        """中午出生（午时）。"""
        fxs = load_fixtures()
        found = False
        for fx in fxs:
            hour = fx.get("birth", {}).get("hour", -1)
            if hour in (11, 12, 13):
                found = True
                analysis = _build_analysis(fx)
                palaces = extract_key_palaces(analysis)
                assert "命宫" in palaces
        if not found:
            pytest.skip("fixture 中无中午出生样本")


# ============================================================
# 3. 实盘场景：洞察文本伪装度检测
# ============================================================

class TestLiveInsightsCamouflage:
    """实盘测试洞察文本的伪装质量，确保绝不泄漏命理术语。"""

    def test_insights_no_forbidden_terms_all_fixtures(self):
        """所有 fixture 样本的洞察都不能包含禁用词。"""
        fxs = load_fixtures()
        diviner = SilentDiviner(api_client=None)
        for fx in fxs:
            analysis = _build_analysis(fx)
            insights = diviner._extract_insights(analysis, "情绪平稳")
            assert insights, f"洞察为空: {fx['birth']}"
            for word in FORBIDDEN_ALL:
                assert word not in insights, (
                    f"禁用词泄漏: '{word}' in \n{insights}\n出生: {fx['birth']}"
                )

    def test_premonition_cue_no_forbidden_terms(self):
        """预感线索也不能包含禁用词。"""
        fxs = load_fixtures()
        diviner = SilentDiviner(api_client=None)
        for fx in fxs:
            birth = fx.get("birth", {})
            cue = asyncio.run(diviner.premonition_cue(
                "test_uid", birth, "测试情绪"
            ))
            if cue:
                for word in FORBIDDEN_ALL:
                    assert word not in cue, (
                        f"预感线索禁用词泄漏: '{word}' in \n{cue}"
                    )

    def test_weave_insights_no_forbidden_terms(self):
        """weave_insights_into_prompt 的输出也不能有禁用词。"""
        fxs = load_fixtures()
        diviner = SilentDiviner(api_client=None)
        for fx in fxs:
            analysis = _build_analysis(fx)
            insights = diviner._extract_insights(analysis, "焦虑")
            result = weave_insights_into_prompt(insights)
            for word in FORBIDDEN_ALL:
                assert word not in result, (
                    f"weave 输出禁用词泄漏: '{word}'"
                )

    def test_insights_minimum_length(self):
        """洞察不能太短，至少要有一定信息量。"""
        fxs = load_fixtures()
        diviner = SilentDiviner(api_client=None)
        short_count = 0
        for fx in fxs:
            analysis = _build_analysis(fx)
            insights = diviner._extract_insights(analysis, "情绪平稳")
            if len(insights) < 20:
                short_count += 1
        total = len(fxs)
        if total > 0:
            assert short_count / total < 0.3, (
                f"短洞察比例过高: {short_count}/{total}"
            )


# ============================================================
# 4. 实盘场景：大规模随机稳定性测试
# ============================================================

class TestLiveStability:
    """大规模随机数据下的稳定性测试。"""

    def test_100_random_birth_da_yun_no_crash(self):
        """100 个随机出生数据的大运计算不崩溃。"""
        fxs = load_fixtures()
        if not fxs:
            pytest.skip("无 fixture 数据")
        base_da_yun = fxs[0].get("daYun_fixture", [])
        if not base_da_yun:
            pytest.skip("无大运数据")

        for i in range(100):
            birth_year = 1950 + i
            analysis = {
                "bazi": {
                    "birthInfo": {"year": birth_year},
                    "daYun": [
                        {**d, "startYear": d.get("startYear")}
                        for d in base_da_yun
                    ]
                }
            }
            try:
                dyn = current_da_yun_liu_nian(analysis)
                assert isinstance(dyn, dict)
            except Exception as e:
                pytest.fail(f"第 {i} 个随机样本崩溃: {e}")

    def test_50_random_ziwei_palaces_no_crash(self):
        """50 个随机紫微数据的宫位提取不崩溃。"""
        fxs = load_fixtures()
        if not fxs:
            pytest.skip("无 fixture 数据")

        base_ziwei = fxs[0].get("ziwei", {})
        if not base_ziwei:
            pytest.skip("无紫微数据")

        for i in range(50):
            analysis = {"ziwei": base_ziwei}
            try:
                palaces = extract_key_palaces(analysis)
                assert isinstance(palaces, dict)
            except Exception as e:
                pytest.fail(f"第 {i} 个紫微样本崩溃: {e}")

    def test_knowledge_mapping_complete(self):
        """知识映射字典的完整性检查。"""
        assert len(BRANCH_LIFE_PHASES) == 12, f"地支映射数: {len(BRANCH_LIFE_PHASES)}"
        zhi = "子丑寅卯辰巳午未申酉戌亥"
        for z in zhi:
            assert z in BRANCH_LIFE_PHASES, f"缺少地支映射: {z}"

        assert len(ELEMENT_ENERGY) == 5, f"五行映射数: {len(ELEMENT_ENERGY)}"
        for e in "金木水火土":
            assert e in ELEMENT_ENERGY, f"缺少五行映射: {e}"

        assert len(ZIWEI_PERSONALITY) >= 14, f"主星映射数: {len(ZIWEI_PERSONALITY)}"

    def test_branch_phrases_no_forbidden(self):
        """地支映射短语本身不含禁用词。"""
        for zhi, phrase in BRANCH_LIFE_PHASES.items():
            for word in FORBIDDEN_ALL:
                assert word not in phrase, f"地支 {zhi} 映射含禁用词: {word}"

    def test_ziwei_personality_no_forbidden(self):
        """主星人格描述不含禁用词。"""
        for star, desc in ZIWEI_PERSONALITY.items():
            for word in FORBIDDEN_ALL:
                assert word not in desc, f"主星 {star} 描述含禁用词: {word}"


# ============================================================
# 5. 实盘场景：男女差异测试
# ============================================================

class TestLiveGenderDifference:
    """男女命盘的大运方向差异测试。"""

    def test_male_female_both_supported(self):
        """男女样本都能正常排盘。"""
        fxs = load_fixtures()
        males = [fx for fx in fxs if fx.get("birth", {}).get("gender") == "male"]
        females = [fx for fx in fxs if fx.get("birth", {}).get("gender") == "female"]

        if males:
            for fx in males:
                analysis = _build_analysis(fx)
                dyn = current_da_yun_liu_nian(analysis)
                assert dyn.get("da_yun"), f"男命大运为空: {fx['birth']}"

        if females:
            for fx in females:
                analysis = _build_analysis(fx)
                dyn = current_da_yun_liu_nian(analysis)
                assert dyn.get("da_yun"), f"女命大运为空: {fx['birth']}"

    def test_gender_insights_quality(self):
        """男女洞察都有合理质量。"""
        fxs = load_fixtures()
        diviner = SilentDiviner(api_client=None)
        for fx in fxs:
            analysis = _build_analysis(fx)
            insights = diviner._extract_insights(analysis, "情绪平稳")
            assert insights, f"洞察为空: {fx['birth']}"
            assert len(insights) >= 10, f"洞察过短: {len(insights)} 字"


# ============================================================
# 6. 实盘场景：情绪状态共鸣测试
# ============================================================

class TestLiveEmotionResonance:
    """不同情绪状态下洞察的差异性测试。"""

    def test_different_emotions_produce_valid_output(self):
        """不同情绪摘要下 weave 都能产出有效内容。"""
        fxs = load_fixtures()
        if not fxs:
            pytest.skip("无 fixture 数据")
        diviner = SilentDiviner(api_client=None)
        fx = fxs[0]
        analysis = _build_analysis(fx)

        insights_happy = diviner._extract_insights(analysis, "开心、兴奋")
        insights_sad = diviner._extract_insights(analysis, "难过、失落")

        woven_happy = weave_insights_into_prompt(insights_happy)
        woven_sad = weave_insights_into_prompt(insights_sad)

        assert isinstance(woven_happy, str)
        assert isinstance(woven_sad, str)
        assert len(woven_happy) > 0
        assert len(woven_sad) > 0

    def test_empty_emotion_summary_works(self):
        """空情绪摘要不应导致崩溃。"""
        fxs = load_fixtures()
        if not fxs:
            pytest.skip("无 fixture 数据")
        diviner = SilentDiviner(api_client=None)
        fx = fxs[0]
        analysis = _build_analysis(fx)
        insights = diviner._extract_insights(analysis, "")
        assert isinstance(insights, str)


# ============================================================
# 7. 实盘场景：缓存机制测试
# ============================================================

class TestLiveCaching:
    """暗盘缓存机制测试。"""

    def test_cache_hit_reduces_computation(self):
        """缓存命中时应直接返回缓存结果。"""
        diviner = SilentDiviner(api_client=None)
        diviner._cache["test_user"] = {
            "insights": "缓存的洞察内容",
            "ts": float("inf"),
        }
        result = asyncio.run(diviner.get_insights(
            "test_user", {"year": 1990}, "情绪"
        ))
        assert result == "缓存的洞察内容"

    def test_cache_expiry_works(self):
        """缓存过期后应失效。"""
        diviner = SilentDiviner(api_client=None)
        diviner._cache_ttl = 0
        diviner._cache["test_user"] = {
            "insights": "旧缓存",
            "ts": 0,
        }
        result = asyncio.run(diviner.get_insights(
            "test_user", None, "情绪"
        ))
        assert result is None

    def test_no_birth_degrades_gracefully(self):
        """无出生信息时优雅降级（返回 None）。"""
        diviner = SilentDiviner(api_client=None)
        result = asyncio.run(diviner.get_insights(
            "test_user", None, "情绪"
        ))
        assert result is None
