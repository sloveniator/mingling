"""命灵·中文情绪识别单测（TTS 情绪音色路由 / 记忆情绪权重）。"""
from emotion_detect import (
    ANGRY,
    CALM,
    EXCITED,
    JOY,
    NEUTRAL,
    SAD,
    detect_emotion,
    emotion_to_weight,
)


def test_joy():
    assert detect_emotion("今天好开心好喜欢") == JOY


def test_sad():
    assert detect_emotion("我好难过好委屈想哭") == SAD


def test_angry():
    assert detect_emotion("真的好生气烦死了") == ANGRY


def test_excited():
    assert detect_emotion("天哪太牛了激动") == EXCITED


def test_neutral_default():
    assert detect_emotion("好的我看看") == NEUTRAL


def test_weight_mapping():
    assert emotion_to_weight(SAD) < 0
    assert emotion_to_weight(ANGRY) < 0
    assert emotion_to_weight(JOY) > 0
    assert emotion_to_weight(EXCITED) > 0
    assert emotion_to_weight(NEUTRAL) == 0
