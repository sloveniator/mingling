"""命灵 v5 命令辅助：出生信息解析、命盘/卦象提示词与摘要格式化。"""
from __future__ import annotations

import json
import re
from typing import Any

_BIRTH_RE = re.compile(
    r"(?P<year>(?:19|20)\d{2})\s*[年\-/.]\s*"
    r"(?P<month>\d{1,2})\s*[月\-/.]\s*"
    r"(?P<day>\d{1,2})\s*[日号]?"
)
_TIME_RE = re.compile(
    r"(?P<period>凌晨|早上|上午|中午|下午|傍晚|晚上|夜里)?\s*"
    r"(?P<hour>\d{1,2})\s*(?:[点时]|:)\s*(?P<minute>\d{1,2})?\s*分?"
)
_GENDER_RE = re.compile(r"(?P<gender>男|女|男性|女性)")


def parse_birth(text: str) -> dict[str, Any] | None:
    """从自然语言中解析公历出生信息；解析不出返回 None。"""
    if not text:
        return None
    m = _BIRTH_RE.search(text)
    if not m:
        return None
    year, month, day = int(m["year"]), int(m["month"]), int(m["day"])

    hour = 12
    tm = _TIME_RE.search(text[m.end():])
    if tm:
        hour = int(tm["hour"])
        period = tm["period"] or ""
        if period in ("下午", "傍晚", "晚上", "夜里") and hour < 12:
            hour += 12
        elif period == "中午" and hour < 11:
            hour += 12
        elif period == "凌晨" and hour == 12:
            hour = 0
        hour = max(0, min(23, hour))

    gm = _GENDER_RE.search(text)
    gender = "male"
    if gm:
        gender = "female" if gm["gender"].startswith("女") else "male"

    return {"year": year, "month": month, "day": day, "hour": hour, "gender": gender}


def clock_to_shichen(hour: int) -> int:
    """钟点 → 时辰地支索引（0=子 … 11=亥）。"""
    return ((int(hour) + 1) // 2) % 12


def format_birth(birth: dict[str, Any]) -> str:
    gender = "女" if birth.get("gender") == "female" else "男"
    return (
        f"{birth.get('year')}年{birth.get('month')}月{birth.get('day')}日 "
        f"{int(birth.get('hour', 0)):02d} 时（{gender}）"
    )


def _clip_json(data: Any, limit: int = 6000) -> str:
    raw = json.dumps(data, ensure_ascii=False, default=str)
    if len(raw) <= limit:
        return raw
    return raw[:limit] + "…（已截断）"


def build_chart_prompt(kind: str, birth: dict[str, Any], data: Any, question: str = "") -> str:
    q = question.strip() or "请概括当前命盘最值得注意的两三点，并给一个可执行的小建议。"
    return (
        f"你是命灵，一个温和、口语化的陪伴者。下面是用户用 {kind} 排出的命盘数据，"
        "以及用户想问的问题。请用朋友聊天的口吻解读：先接住问题，再借命盘说人话，"
        "不要堆术语，不要宿命论，不要吓人；结尾给一个具体、可执行的小建议。\n\n"
        f"用户问题：{q}\n\n命盘数据（JSON）：\n{_clip_json(data)}"
    )


def build_gua_prompt(question: str, data: Any) -> str:
    return (
        "你是命灵，一个温和、口语化的陪伴者。下面是六爻起卦结果。请围绕用户的问题，"
        "用朋友聊天的口吻解读：先接住问题与情绪，再借卦象说清当下的处境、可能的阻力"
        "与可用的一步；不要宿命论，不要绝对化断言，结尾给一个具体可执行的小建议。\n\n"
        f"用户问题：{question}\n\n卦象数据（JSON）：\n{_clip_json(data)}"
    )


def build_tarot_prompt(question: str, spread: dict, base_prompt: str) -> str:
    return (
        f"用户问题：{question.strip() or '（未指定，就当下状态给予映照）'}\n\n"
        f"牌阵数据（JSON）：\n{_clip_json(spread, 2000)}\n\n{base_prompt}"
    )


def build_health_prompt(note: str, plan: dict | None = None) -> str:
    plan_text = f"\n\n结构化建议（JSON）：\n{_clip_json(plan, 2000)}" if plan else ""
    return (
        "你是命灵，一个温和、口语化的陪伴者。请根据用户的健康/作息描述，给出"
        "不超过 4 条可执行建议：先共情，再讲清为什么，最后给最小行动；不要医疗诊断，"
        "不要制造焦虑，必要时提醒对方咨询专业医生。\n\n"
        f"用户描述：{note}{plan_text}"
    )


def build_growth_prompt(memories: list[dict], timeline: list[dict]) -> str:
    mem_lines = "\n".join(
        f"- {m.get('topic', '')}：{m.get('content', '')}" for m in memories[:12]
    ) or "（暂无关系记忆）"
    tl_lines = "\n".join(
        f"- {r.get('kind', '')}：{_clip_json(r.get('payload', {}), 200)}"
        for r in timeline[-20:]
    ) or "（暂无时间线）"
    return (
        "你是命灵。请基于下面这段关系记忆与近期时间线，写一段 2-3 句的「成长反思」："
        "第一句写你逐渐理解了的这个人是谁（只写事实观察，不评价）；第二句写你作为"
        "陪伴者学到的一个具体觉察；第三句给一句温和、不油腻的陪伴承诺。"
        "不要编造未出现的事实。\n\n"
        f"关系记忆：\n{mem_lines}\n\n近期时间线：\n{tl_lines}"
    )
