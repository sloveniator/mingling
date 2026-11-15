"""
tests/test_health.py -- 健康生活指导引擎纯逻辑测试
"""
import health


def test_profile_from_dict_gender_normalization():
    p = health.HealthProfile.from_dict({"gender": "男", "age": 30, "exercise_freq": 2})
    assert p.gender == "male"
    assert p.age == 30
    assert p.exercise_freq == 2


def test_profile_has_basic_false_when_empty():
    assert health.HealthProfile().has_basic is False


def test_sleep_duration_cross_midnight():
    d = health._sleep_duration_hours("23:30", "07:00")
    assert d is not None
    assert abs(d - 7.5) < 0.01


def test_routine_short_sleep():
    p = health.HealthProfile(sleep_time="03:00", wake_time="09:00")  # 仅 6 小时
    tips = health.build_routine_advice(p)
    joined = " ".join(tips)
    assert any("偏少" in t or "提前" in t for t in tips)
    assert "凌晨" in joined or "固定起床" in joined


def test_diet_habits_takeout():
    p = health.HealthProfile(diet_habits="常吃外卖", goals=["减脂"])
    tips = health.build_diet_advice(p)
    assert any("外卖" in t for t in tips)
    assert any("减脂" in t for t in tips)


def test_exercise_zero_freq():
    p = health.HealthProfile(exercise_freq=0)
    tips = health.build_exercise_advice(p)
    assert any("动起来" in t or "快走" in t for t in tips)


def test_conditions_sedentary():
    p = health.HealthProfile(conditions=["久坐腰酸"])
    plan = health.generate_health_plan(p)
    assert "conditions_tips" in plan
    assert any("久坐" in t for t in plan["conditions_tips"])


def test_disclaimer_present():
    p = health.HealthProfile(age=25)
    plan = health.generate_health_plan(p)
    assert health.HEALTH_DISCLAIMER in plan["disclaimer"]


def test_plan_prompt_contains_disclaimer():
    p = health.HealthProfile(age=28, sleep_time="23:30", wake_time="07:00")
    plan = health.generate_health_plan(p)
    prompt = health.health_plan_prompt(p, plan)
    assert "医生" in prompt
    assert "作息" in prompt
