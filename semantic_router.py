"""混合意图路由：正则快速路径之外，用 AstrBot 默认模型做语义兜底。

设计原则：
- 只做“意图分类”，不回答用户，不改变聊天内容；
- 正则已高置信命中时不调用模型，避免额外成本与延迟；
- 仅对“可能是功能请求/情绪表达”的候选消息调用；
- 模型失败、超时、JSON 非法、置信度不足时一律 fail-open，交回普通聊天。
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass

from astrbot.api import logger

from llm_default import ask_default

TASK_INTENTS = {
    "status", "my_chart", "set_chart", "method",
    "chart", "divination", "tarot", "health", "growth", "mood",
}
ALL_INTENTS = TASK_INTENTS | {"counsel", "none"}
COUNSEL_ACTIONS = {"enter", "continue", "exit", "none"}

_CUE = re.compile(
    r"[?？]|帮我|看看|说说|聊聊|算算|算一卦|测测|占卜|抽牌|"
    r"怎么(办|看|样)|难受|好累|好烦|心里|提不起|没劲|迷茫|焦虑|"
    r"失眠|睡眠|健康|成长|反思|命盘|八字|紫微|塔罗|卜卦|开解|"
    r"该不该|要不要|能不能|选哪个|怎么做"
)

_SHIFT = re.compile(
    r"算了|不想|不说|够了|停|结束|换|忙|改天|先这样|没事|好多了|可以了|"
    r"聊点|说点|别的|具体|帮我|怎么办"
)

_SYSTEM = """你是「命灵」插件的意图分类器，只输出一个 JSON 对象，不回答用户。
候选 intent：
- status：查看关系/亲密度/当前状态
- my_chart：查看已保存的命盘档案
- set_chart：设置/保存出生信息
- method：设置命理方法（紫微/八字/六爻/奇门/梅花/自动）
- chart：排盘/看八字/看紫微/合参
- divination：卜卦/起卦/六爻/奇门/梅花易数/占卜
- tarot：塔罗/抽牌
- health：健康/作息/睡眠/饮食/运动建议
- growth：成长反思/复盘关系
- mood：查看心情/情绪状态
- counsel：情绪倾诉、想聊聊、需要安慰
- none：普通聊天、信息不足、无法判断

counsel_action：
- enter：用户开始倾诉/需要安慰
- continue：已在开解中，继续倾诉
- exit：明确结束、好多了、不想聊了、换话题、转向具体任务
- none：不涉及开解

规则：
1. 只有明确想使用功能时才给对应 intent；普通闲聊返回 none。
2. 用户输入带斜杠也只按中文语义理解，不因斜杠强行分类。
3. 当前已在开解中时，结合上下文判断 continue/exit；转向具体任务时给对应 intent 且 counsel_action=exit。
4. confidence 为 0~1；不确定时给低置信度。
5. 只输出 JSON，不要 markdown、不要解释。

输出格式：
{"intent":"none","confidence":0.0,"counsel_action":"none"}"""


@dataclass
class SemanticDecision:
    intent: str
    confidence: float
    counsel_action: str


def parse_decision(raw: str) -> SemanticDecision | None:
    """解析模型输出；非法/越界返回 None。"""
    if not raw:
        return None
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.S)
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(text[start:end + 1])
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(data, dict):
        return None
    intent = str(data.get("intent", "none"))
    action = str(data.get("counsel_action", "none"))
    try:
        confidence = float(data.get("confidence", 0.0))
    except (TypeError, ValueError):
        return None
    if intent not in ALL_INTENTS or action not in COUNSEL_ACTIONS:
        return None
    return SemanticDecision(intent, max(0.0, min(1.0, confidence)), action)


def should_classify(text: str, active_counsel: bool, *, min_chars: int = 4) -> bool:
    """只在“可能是功能/情绪请求”时调用模型，避免每条闲聊都多打一次模型。"""
    raw = (text or "").strip()
    if len(raw) < min_chars or len(raw) > 300:
        return False
    if active_counsel:
        # 开解中只对“可能收束/转向”的消息做语义判断，短应声直接继续
        return len(raw) >= 6 and bool(_SHIFT.search(raw))
    return bool(_CUE.search(raw))


# 轻量缓存：同一文本+状态在 2 分钟内不重复分类
_CACHE: dict[tuple[str, bool], tuple[float, SemanticDecision | None]] = {}
_TTL = 120
_MAX_CACHE = 200


async def classify(
    context,
    text: str,
    active_counsel: bool,
    *,
    timeout: int = 8,
) -> SemanticDecision | None:
    """调用 AstrBot 默认模型做意图分类；失败返回 None（fail-open）。"""
    raw = (text or "").strip()
    if not raw:
        return None
    key = (raw[:200], active_counsel)
    now = time.time()
    cached = _CACHE.get(key)
    if cached and now - cached[0] <= _TTL:
        return cached[1]

    prompt = (
        f"当前是否在开解会话中：{'是' if active_counsel else '否'}\n"
        f"用户消息：{raw[:300]}\n"
        "请输出 JSON："
    )
    out = await ask_default(
        context,
        prompt,
        system=_SYSTEM,
        session_id="mingling_intent",
        timeout=timeout,
    )
    decision = parse_decision(out)
    if decision is not None:
        logger.debug(
            f"[mingling.intent] semantic intent={decision.intent} "
            f"conf={decision.confidence:.2f} action={decision.counsel_action}"
        )
    if len(_CACHE) >= _MAX_CACHE:
        _CACHE.clear()
    _CACHE[key] = (now, decision)
    return decision
