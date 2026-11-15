"""
disguise_guard.py -- 暗盘伪装护栏（防破功）
==========================================
从 private_companion 的「日记质量校验」思想提炼而来：它的 _daily_diary_quality_issues
会检测「后台术语泄露 / 客服腔 / 把未确认计划写成已完成」并自动改写。

命灵的铁律是——暗盘洞察绝不以 命盘/八字/紫微/卦象 等术语现形，也绝不能
用客服口吻说话（"请问还有什么可以帮您"）。本模块提供纯函数检测，用于：
  - 暗盘注入前的防御性兜底（万一翻译表被改坏，自动丢弃含术语的洞察）；
  - 对 Bot  outgoing 回复做可观测性检查（_on_llm_response 里记 warning）。

纯逻辑、无 astrbot 依赖、可单测。
"""

from typing import Dict, List

# 任何出现在用户可见文本里都会"破功"的命理术语
LEAK_TERMS: List[str] = [
    "命盘", "八字", "四柱", "紫微", "紫微斗数", "斗数", "卦象", "排盘",
    "占卜", "占算", "六爻", "奇门", "塔罗", "大运", "流年", "十神",
    "星曜", "宫位", "命宫", "福德宫", "夫妻宫", "官禄宫", "财帛宫",
    "身旺", "身弱", "日主", "用神", "喜神", "忌神",
]

# 客服 / AI 助手腔调标记（出现即不像"朋友"）
CUSTOMER_TONE_PATTERNS: List[str] = [
    "请问还有什么可以帮您", "请问还有什么需要", "作为AI助手", "作为人工智能助手",
    "作为您的AI", "如果您有任何问题", "如果您还有其他问题", "需要我为您",
    "亲，", "您好！我是", "您好，我是您的", "祝您生活愉快", "感谢您的咨询",
    "如有疑问", "随时为您服务", "请问您",
]


def check_disguise_leak(text: str) -> Dict[str, object]:
    """检测文本是否泄露命理术语或带有客服腔。

    Returns:
        {
            "safe": bool,          # 无泄露且无客服腔
            "leak_terms": List[str],  # 命中的命理术语
            "customer_tone": bool,    # 是否检测到客服腔
        }
    """
    if not text:
        return {"safe": True, "leak_terms": [], "customer_tone": False}

    t = text
    hit_terms = [term for term in LEAK_TERMS if term in t]
    customer_tone = any(p in t for p in CUSTOMER_TONE_PATTERNS)
    return {
        "safe": (len(hit_terms) == 0 and not customer_tone),
        "leak_terms": hit_terms,
        "customer_tone": customer_tone,
    }


def strip_leak_terms(text: str, mask: str = "◇") -> str:
    """将泄露的命理术语替换成中性掩码（兜底用，不破坏语句结构）。"""
    out = text
    for term in LEAK_TERMS:
        if term in out:
            out = out.replace(term, mask * len(term))
    return out
