"""自然语言意图识别：不依赖斜杠命令，斜杠文本也按普通语义处理。"""
from __future__ import annotations

import re

# 顺序即优先级：先具体功能，后泛化功能。
_TASK_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("status", re.compile(r"命灵状态|关系(状态|阶段|进展|怎么样|到哪)|亲密度|我们.*(关系|进展)")),
    ("my_chart", re.compile(r"(我的|我存的|已有的|之前的).{0,6}(命盘|出生信息|档案)|命盘档案")),
    ("set_chart", re.compile(r"设置命盘|(记|存|填|设置).{0,6}出生|出生信息.{0,4}(设置|保存)|我(是|在)?\s*(19|20)\d{2}\s*年")),
    ("method", re.compile(r"命理方法|测算方法|用(紫微|八字|六爻|奇门|梅花)|换成(紫微|八字|六爻|奇门|梅花)")),
    ("chart", re.compile(r"排盘|排个盘|(看|算|查|推).{0,4}(八字|紫微|命盘|合参)|八字|紫微(斗数)?|合参")),
    ("divination", re.compile(r"卜卦|起卦|算一卦|占卜|六爻|奇门|梅花易数|测一测|测测")),
    ("tarot", re.compile(r"塔罗|抽牌|抽三张")),
    ("health", re.compile(r"健康|作息|睡眠|失眠|饮食|运动|锻炼|减肥|养生")),
    ("growth", re.compile(r"成长|反思|回顾|复盘|你(学到|改变)|我们.*相处")),
    ("mood", re.compile(r"心情|情绪(状态|怎么样)|我最近(状态|怎么样)")),
]

_DISTRESS = re.compile(
    r"想聊|聊聊|倾诉|说说心里|难受|难过|伤心|委屈|焦虑|压力|烦|崩溃|孤独|寂寞|"
    r"安慰|陪我|心里(不|堵)|撑不住|累|想哭|郁闷|低落|心情不好"
)

_EXIT = re.compile(
    r"好多了|没事了|好一些了|谢谢.{0,4}够|不想聊|不想说|换个话题|说点别的|聊点别的|"
    r"到此为止|先这样|结束了|可以了|不用了|我先忙|改天聊"
)

_SLASH = re.compile(r"^[／/]\s*")


def normalize(text: str) -> str:
    """去掉前导斜杠；斜杠文本仍按其中文语义识别。"""
    return _SLASH.sub("", (text or "").strip()).strip()


def detect_task(text: str) -> str | None:
    """返回命理/咨询/成长功能意图标签；无明确意图返回 None。"""
    raw = normalize(text)
    if not raw:
        return None
    for name, pattern in _TASK_PATTERNS:
        if pattern.search(raw):
            return name
    return None


def is_distress(text: str) -> bool:
    return bool(_DISTRESS.search(normalize(text)))


def should_start_counseling(text: str) -> bool:
    """情绪困扰、想倾诉、需要安慰时进入开解。"""
    return is_distress(text)


def should_exit_counseling(text: str) -> bool:
    """自然收束/明确结束/转向无关话题时退出开解。"""
    return bool(_EXIT.search(normalize(text)))


def is_ambiguous(text: str) -> bool:
    """只含模糊指代、没有具体内容时，值得问一个澄清问题。"""
    raw = normalize(text)
    return raw in {"那个", "这个", "怎么办", "然后呢", "你说呢", "嗯", "哦"} or len(raw) <= 1
