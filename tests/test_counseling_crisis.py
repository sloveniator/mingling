"""
tests/test_counseling_crisis.py -- 心理健康：危机识别 / 情绪严重度 / 多轮会话
"""
import asyncio

from counseling import (
    COUNSEL_STAGES,
    CounselingSession,
    CounselingSessionManager,
    EmotionalTracker,
    counseling_turn_prompt,
    crisis_support_prompt,
    detect_crisis,
    technical_guidance_prompt,
)


def test_detect_crisis_hit():
    assert detect_crisis("我真的想死") is not None
    assert detect_crisis("活着没意思，不如消失了") is not None
    assert detect_crisis("今天天气真好") is None


def test_detect_crisis_keyword():
    r = detect_crisis("我不想活了")
    assert r["category"] == "self_harm"
    assert "活" in r["keyword"]


def test_detect_crisis_ideation_level():
    # 纯意念：仅技术性开导，不推热线
    r = detect_crisis("活着没意思，不如消失了")
    assert r["level"] == "ideation"
    r2 = detect_crisis("我不想活了")
    assert r2["level"] == "ideation"
    # 提到手段但无"进行中"信号 -> 仍按意念级处理
    r3 = detect_crisis("我有时候想割腕")
    assert r3["level"] == "ideation"


def test_detect_crisis_action_in_progress():
    # 明确行动 + 实施进行中 -> 行动红线（推热线）
    assert detect_crisis("我正在割腕")["level"] == "action"
    assert detect_crisis("我吃了安眠药")["level"] == "action"
    assert detect_crisis("我准备跳楼")["level"] == "action"
    assert detect_crisis("我已经跳下去了")["level"] == "action"
    assert detect_crisis("我正在自杀")["level"] == "action"


def test_crisis_support_prompt_has_hotline():
    prompt = crisis_support_prompt("想死")
    assert "400-161-9995" in prompt
    assert "命理" in prompt  # 红线：危机时不做命理预测


def test_technical_guidance_no_hotline():
    # 意念级：技术性开导不含热线，但仍声明不做命理
    prompt = technical_guidance_prompt("想死")
    assert "400-161-9995" not in prompt
    assert "命理" in prompt  # 此刻仍不做命理预测


def test_emotion_intensity_anger_added():
    # 愤怒维度已被补充进 EMOTION_KEYWORDS
    assert "anger" in __import__("counseling").EMOTION_KEYWORDS
    assert "愤怒" in __import__("counseling").EMOTION_KEYWORDS["anger"]


def test_severity_boosted_by_intensifier():
    t = EmotionalTracker()
    t.record("test_user", "我好累")
    base = t.severity_level("test_user")
    t.record("test_user", "我快崩溃了")
    boosted = t.severity_level("test_user")
    assert boosted >= base
    assert boosted == 3  # 含加重词 → 顶格


def test_severity_no_signal_is_zero():
    t = EmotionalTracker()
    assert t.severity_level("someone_else") == 0


def test_counseling_session_stage_flow():
    s = CounselingSession(topic="工作")
    assert s.stage == "opening"
    s.advance()
    assert s.stage == "listening"
    # 推进到末尾不再越界
    for _ in range(10):
        s.advance()
    assert s.stage == COUNSEL_STAGES[-1]


def test_counseling_session_turn_cap():
    s = CounselingSession()
    for i in range(20):
        s.add_turn("user", "第%d句" % i)
    assert len(s.turns) <= 12


def test_session_roundtrip_dict():
    s = CounselingSession(topic="感情")
    s.add_turn("user", "我很难过")
    d = s.to_dict()
    s2 = CounselingSession.from_dict(d)
    assert s2.topic == "感情"
    assert s2.turns[0]["text"] == "我很难过"


def test_session_manager_memory_only():
    # 无 plugin 时退化为纯内存
    mgr = CounselingSessionManager(plugin=None)

    async def go():
        sess = CounselingSession(topic="测试")
        await mgr.save("u1", sess)
        got = await mgr.get("u1")
        return got

    got = asyncio.run(go())
    assert got is not None
    assert got.topic == "测试"


def test_counseling_turn_prompt_includes_transcript():
    s = CounselingSession(topic="迷茫")
    s.add_turn("user", "不知道该不该辞职")
    s.add_turn("bot", "听起来你很纠结")
    prompt = counseling_turn_prompt(s, "我怕选错", "迷茫和不确定", "uncertainty")
    assert "不知道该不该辞职" in prompt
    assert "迷茫" in prompt
