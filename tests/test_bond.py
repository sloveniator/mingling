"""
tests/test_bond.py -- 角色羁绊（Bond）模型与事件检测
====================================================
覆盖：detect_bond_event（羁绊事件识别 + 优先级）、bond_title（称号）、
Bond 的 interact/nudge（普通互动 + 事件级变化 + 里程碑）与序列化往返。
"""
from bond import Bond, bond_title, detect_bond_event


# ------------------------------------------------------------
# 羁绊事件检测
# ------------------------------------------------------------
def test_detect_bond_event_basic():
    assert detect_bond_event("对不起，我错了") == "apology"
    assert detect_bond_event("别理我，我想静静") == "refusing"
    assert detect_bond_event("你最近好冷淡，是不是敷衍我") == "hurt"
    assert detect_bond_event("今天辛苦了，抱抱你") == "comfort"
    assert detect_bond_event("爱你，想你了") == "intimacy"


def test_detect_bond_event_priority():
    # 复合语义按优先级：道歉 > 想静静 > 受伤 > 安慰 > 亲密
    assert detect_bond_event("对不起，别生气，抱抱你") == "apology"
    assert detect_bond_event("别理我，我不想说话") == "refusing"


def test_detect_bond_event_none():
    assert detect_bond_event("今天天气不错") is None
    assert detect_bond_event("") is None
    assert detect_bond_event(None) is None


# ------------------------------------------------------------
# 羁绊称号
# ------------------------------------------------------------
def test_bond_title_thresholds():
    assert bond_title(10, 0)[0] == "初识"
    assert bond_title(30, 0)[0] == "熟悉"
    assert bond_title(55, 0)[0] == "挚友"
    assert bond_title(80, 0)[0] == "羁绊"
    # 亲密时刻达到 5 次，即使分数不够也升「羁绊」
    assert bond_title(50, 5)[0] == "羁绊"


# ------------------------------------------------------------
# Bond 模型
# ------------------------------------------------------------
def test_bond_interact_and_mode():
    b = Bond("u1", "char1")
    assert b.score == 10.0 and b.mode == "normal"
    for _ in range(30):
        b.interact(positive=True)
    assert b.score > 40
    assert b.mode == "warming"


def test_bond_nudge_intimacy_and_milestone():
    b = Bond("u1", "char1")
    b.nudge("intimacy")
    assert b.score == 15.0
    assert b.intimacy_moments == 1
    assert b.milestones == []  # 分数太低，未达新称号，不记录里程碑

    # 连续亲密，直到分数跨过「熟悉」，记录里程碑
    for _ in range(5):
        b.nudge("intimacy")
    assert b.score >= 30
    assert any(m["title"] == "熟悉" for m in b.milestones)


def test_bond_nudge_refusing_and_hurt():
    b = Bond("u1", "char1")
    b.nudge("refusing")
    assert b.mode == "refusing"
    b.nudge("hurt")
    assert b.mode == "hurt"
    assert b.score < 10.0


def test_bond_nudge_apology_recovers():
    b = Bond("u1", "char1")
    b.nudge("hurt")
    assert b.mode == "hurt"
    b.nudge("apology")
    assert b.mode == "normal"


def test_bond_serialization_roundtrip():
    b = Bond("u1", "char1")
    b.nudge("intimacy")
    b.nudge("intimacy")
    d = b.to_dict()
    b2 = Bond.from_dict(d)
    assert b2.user_id == "u1"
    assert b2.char_id == "char1"
    assert b2.score == b.score
    assert b2.intimacy_moments == b.intimacy_moments
    assert b2.milestones == b.milestones
