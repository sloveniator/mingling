"""
emotion_detect.py -- 中文情绪识别（零 LLM 成本）
================================================
参考 tts_emotion_router（情绪→音色路由）与 stealer 的「对 Bot 自身回复做情绪分析」
（小模型 + 本地关键词快速路径 + 缓存 + LLM 失败降级）。

命灵不引入英文关键词表（那套对中文聊天几乎失效），改用中文情绪词 + 标点增强的
启发式识别，覆盖 6 类：joy(喜) / sad(悲) / angry(怒) / excited(兴奋) /
calm(平静) / neutral(中性)。用于：
  - TTS 情绪音色路由（tts_emotion_routing_enabled 时）；
  - 记忆情绪权重回写（与 memory_core 的情感衰减联动，见 memory_core.Memory.decay）。

纯逻辑、无 astrbot 依赖、可单测。识别为启发式，不追求高准确率，只求"够用且不烧 token"。
"""

from typing import Dict, List

# 情绪类别
JOY = "joy"
SAD = "sad"
ANGRY = "angry"
EXCITED = "excited"
CALM = "calm"
NEUTRAL = "neutral"

EMOTIONS: List[str] = [JOY, SAD, ANGRY, EXCITED, CALM, NEUTRAL]

# 每类的中文关键词（词频计分）
KEYWORDS: Dict[str, List[str]] = {
    JOY: ["开心", "高兴", "快乐", "喜欢", "哈哈", "嘻嘻", "好开心", "太好了",
          "幸福", "满足", "笑", "可爱", "温柔", "暖", "爱了", "棒", "赞", "好耶"],
    SAD: ["难过", "伤心", "想哭", "委屈", "失落", "孤独", "空虚", "沮丧",
          "压抑", "崩溃", "累", "疲惫", "失眠", "遗憾", "心碎", "难受", "低落"],
    ANGRY: ["生气", "愤怒", "烦", "讨厌", "气死", "可恶", "滚", "混蛋",
            "凭什么", "恶心", "火大", "受不了", "恨", "无语", "恼火"],
    EXCITED: ["激动", "哇", "天哪", "绝了", "冲", "燃", "刺激", "惊呆",
              "不敢相信", "牛", "惊喜", "迫不及待", "兴奋", "太帅了"],
    CALM: ["平静", "安心", "舒服", "放松", "静静", "发呆", "冥想", "喝茶",
           "散步", "睡", "休息", "宁静", "清静", "缓缓"],
    NEUTRAL: [],
}

# 标点增强：感叹号/问号/省略号对情绪的微弱提示
_PUNCT_EXCLAIM = "！!!"
_PUNCT_ELLIPSIS = "……"


def detect_emotion(text: str, default: str = NEUTRAL) -> str:
    """启发式识别文本情绪，返回 EMOTIONS 之一。

    规则：中文关键词计分（命中即 +1/词，累加）；感叹号对 joy/angry/excited 轻微加权；
    省略号对 sad/calm 轻微加权。取最高分类；并列或全 0 时返回 default（默认 neutral）。
    """
    if not text:
        return default

    scores: Dict[str, int] = {e: 0 for e in EMOTIONS}
    for emo, words in KEYWORDS.items():
        if emo == NEUTRAL:
            continue
        for w in words:
            if w in text:
                scores[emo] += 1

    # 标点增强
    exclaims = sum(1 for ch in text if ch in _PUNCT_EXCLAIM)
    ellipsis = text.count("……") + text.count("...")
    if exclaims:
        scores[JOY] += min(exclaims, 3)
        scores[ANGRY] += min(exclaims, 2)
        scores[EXCITED] += min(exclaims, 2)
    if ellipsis:
        scores[SAD] += min(ellipsis, 2)
        scores[CALM] += min(ellipsis, 2)

    # 选最高分；全 0 → default
    best = max(scores, key=lambda k: scores[k])
    if scores[best] == 0:
        return default
    return best


def emotion_to_weight(emotion: str) -> float:
    """把情绪类别映射为 memory_core 的 emotional_weight(-1..1)。

    sad/angry → 负（衰减更快，情感愈合）；joy/excited → 正（衰减更慢，留住美好）；
    calm/neutral → 0。
    """
    return {
        JOY: 0.6,
        EXCITED: 0.8,
        SAD: -0.7,
        ANGRY: -0.5,
        CALM: 0.0,
        NEUTRAL: 0.0,
    }.get(emotion, 0.0)
