"""命灵·记忆情感衰减单测（参考 iris_memory 的差异情感衰减）。"""
import pytest

from memory_core import Memory


def test_emotion_aware_decay_negative_faster():
    neg = Memory("u", "episodic", "x", importance=0.5, emotional_weight=-0.8)
    neu = Memory("u", "episodic", "x", importance=0.5, emotional_weight=0.0)
    pos = Memory("u", "episodic", "x", importance=0.5, emotional_weight=0.8)
    neg.decay(30)
    neu.decay(30)
    pos.decay(30)
    # 负面衰减最快（情感愈合），正面最慢（留住美好）
    assert neg.decay_score < neu.decay_score < pos.decay_score


def test_decay_zero_emotion_unchanged_behavior():
    m = Memory("u", "episodic", "x", importance=0.5, emotional_weight=0.0)
    before = m.decay_score
    m.decay(1)
    # 中性情绪：衰减率应与旧实现一致（0.04 * (1-0.5) = 0.02）
    assert before - m.decay_score == pytest.approx(0.02)


def test_serialization_keeps_emotional_weight():
    m = Memory("u", "episodic", "x", emotional_weight=-0.5)
    m2 = Memory.from_dict(m.to_dict())
    assert m2.emotional_weight == -0.5
