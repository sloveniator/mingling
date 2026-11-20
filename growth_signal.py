"""对话中的成长价值打分（规则/启发式，零模型调用）。

每条私聊消息在 `_observe_user_message` 中调用一次，结果写入
`growth_signals`（每用户最多 N 条）。全局反思打包时按分数筛选。
"""
from __future__ import annotations

import re
import time

# 价值信号分类。命中越多的类别，分数越高。
_CATEGORIES: dict[str, tuple[str, ...]] = {
    "decision": (
        "决定", "选择", "打算", "计划", "目标", "准备", "报名", "辞职", "离职",
        "换工作", "搬家", "结婚", "分手", "开始", "结束", "答应", "拒绝",
    ),
    "event": (
        "面试", "考试", "毕业", "入职", "升职", "加薪", "找工作", "生病", "住院", "手术",
        "家人", "妈妈", "爸爸", "朋友", "恋爱", "吵架", "道歉", "旅行", "搬家",
        "第一次", "终于", "成功", "失败", "通过", "没通过",
    ),
    "reflection": (
        "明白", "意识到", "学到", "反思", "总结", "发现", "原来", "成长",
        "改变", "以后", "下次", "应该", "后悔", "值得",
    ),
    "emotion": (
        "难过", "焦虑", "压力", "害怕", "担心", "开心", "幸福", "委屈",
        "崩溃", "孤独", "轻松", "踏实", "失望",
    ),
    "conclusion": (
        "所以", "因此", "结论", "最终", "结果", "搞定", "解决", "确认",
    ),
}

_LOW_VALUE = (
    "你好", "在吗", "嗯", "哦", "哈哈", "晚安", "早安", "谢谢", "收到",
    "好的", "可以", "没事", "行", "好", "是的",
)

_TIME_HINT = re.compile(r"\d|今天|明天|昨天|下周|下个月|今年|明年|以后|之前|后来")
_CAUSAL = ("因为", "所以", "但是", "如果", "虽然", "结果", "于是", "不过")


def _clip(x: float, lo: float = 0.05, hi: float = 0.98) -> float:
    return max(lo, min(hi, x))


def score_growth_signal(text: str, *, mood: str | None = None,
                        topic: str | None = None) -> dict:
    """给一条用户消息打成长价值分。

    返回值：{"text", "score": 0.05~0.98, "tags": [...], "ts", "topic"}
    规则是可解释的启发式，不调用模型；分数仅为筛选依据，不等同事实判断。
    """
    raw = (text or "").strip()
    tags: list[str] = []
    score = 0.15

    for name, words in _CATEGORIES.items():
        if any(w in raw for w in words):
            tags.append(name)
            score += 0.13

    if mood:
        tags.append(f"mood:{mood}")
        score += 0.18
    if topic:
        tags.append("topic")

    # 信息密度：长度贡献最多 0.15
    score += min(0.15, len(raw) / 200.0 * 0.15)
    if _TIME_HINT.search(raw):
        tags.append("time")
        score += 0.06
    if any(w in raw for w in _CAUSAL):
        tags.append("causal")
        score += 0.06

    # 低价值：短应声、寒暄
    if len(raw) < 6:
        score -= 0.20
    if len(raw) <= 12 and any(w in raw for w in _LOW_VALUE):
        score -= 0.25

    return {
        "text": raw[:500],
        "score": round(_clip(score), 3),
        "tags": tags,
        "ts": time.time(),
        "topic": (topic or "")[:80],
    }
