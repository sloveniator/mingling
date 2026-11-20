"""全局成长反思：跨用户筛选、去重、预算截断、单份反思提示词。"""
from __future__ import annotations

import re

from state_layer import estimate_tokens

TOKEN_METHOD = "estimate: CJK/1.6 + other/4 + 1"


def _normalize(text: str) -> str:
    return re.sub(r"[\s\W_]+", "", (text or "").lower())


def _grams(text: str) -> set[str]:
    s = _normalize(text)
    if len(s) < 2:
        return {s} if s else set()
    return {s[i:i + 2] for i in range(len(s) - 1)}


def _similar(a: str, b: str, threshold: float) -> bool:
    ga, gb = _grams(a), _grams(b)
    if not ga or not gb:
        return False
    inter = len(ga & gb)
    union = len(ga | gb)
    return union > 0 and inter / union >= threshold


def build_growth_pack(
    signals: list[dict],
    *,
    min_score: float = 0.55,
    max_items: int = 60,
    token_budget: int = 3000,
    dedupe_threshold: float = 0.82,
) -> dict:
    """按权重筛选、去重、截断，返回可打包的条目 + 统计。"""
    before = [s for s in signals if isinstance(s, dict) and (s.get("text") or "").strip()]
    before_count = len(before)
    before_tokens = sum(estimate_tokens(s.get("text", "")) for s in before)

    # 1. 权重阈值
    filtered = [s for s in before if float(s.get("score", 0.0)) >= min_score]
    filtered_count = before_count - len(filtered)

    # 2. 高分优先去重（近似文本合并，保留高分条目）
    filtered.sort(key=lambda s: (float(s.get("score", 0.0)), float(s.get("ts", 0.0))),
                  reverse=True)
    deduped: list[dict] = []
    dedupe_dropped = 0
    for sig in filtered:
        if any(_similar(sig.get("text", ""), kept.get("text", ""), dedupe_threshold)
               for kept in deduped):
            dedupe_dropped += 1
            continue
        deduped.append(sig)

    # 3. 条数上限
    count_truncated = max(0, len(deduped) - max_items)
    packed = deduped[:max_items]

    # 4. token 预算（按权重顺序截断；首条超预算时裁剪文本）
    kept: list[dict] = []
    used = 0
    token_truncated = 0
    for sig in packed:
        text = str(sig.get("text", ""))
        cost = estimate_tokens(text)
        if used + cost <= token_budget:
            kept.append(sig)
            used += cost
            continue
        remaining = token_budget - used
        if remaining > 20 and not kept:
            # 至少保留一条，按预算比例裁剪
            ratio = max(0.1, remaining / max(1, cost))
            clipped = dict(sig)
            clipped["text"] = text[: max(40, int(len(text) * ratio))]
            kept.append(clipped)
            used += estimate_tokens(clipped["text"])
        token_truncated += 1

    after_tokens = sum(estimate_tokens(s.get("text", "")) for s in kept)
    stats = {
        "before_count": before_count,
        "after_filter_count": len(filtered),
        "filtered_count": filtered_count,
        "dedupe_dropped": dedupe_dropped,
        "count_truncated": count_truncated,
        "token_truncated": token_truncated,
        "after_count": len(kept),
        "before_tokens": before_tokens,
        "after_tokens": after_tokens,
        "token_method": TOKEN_METHOD,
        "min_score": min_score,
        "max_items": max_items,
        "token_budget": token_budget,
        "dedupe_threshold": dedupe_threshold,
    }
    return {"items": kept, "stats": stats}


def build_global_growth_prompt(pack: dict) -> str:
    """构建“只输出一份整体反思”的提示词，不按用户分节。"""
    lines = []
    for i, item in enumerate(pack.get("items", []), 1):
        tags = ",".join(item.get("tags", []) or [])
        lines.append(f"{i}. [{tags or 'general'}] {item.get('text', '')}")
    body = "\n".join(lines) or "（本次没有足够有价值的片段）"
    return (
        "你是命灵。下面是跨用户筛选后的高价值对话片段（已去重、按价值排序）。"
        "请只写一份**整体成长反思**，不要按用户分节，不要列用户 ID，不要复述原文。\n"
        "结构：第一段写你作为陪伴者观察到的共同主题；第二段写你学到的一个具体觉察；"
        "第三段写一句温和、不油腻的陪伴承诺。总长 120-220 字。\n"
        "不要编造片段中没有的事实；如果片段不足以支撑判断，就写“这次还看不清”。\n\n"
        f"片段：\n{body}"
    )
