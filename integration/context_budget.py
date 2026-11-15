"""integration/context_budget.py -- 动态上下文 token 预算管理。

2.0 第一阶段任务 4：给各增强模块可贡献的上下文量设建议预算（软上限），
避免把所有记忆/人格/情绪无脑拼接撑爆上下文窗口（对应方案 2.3）。

设计原则：
- 数字是「建议 token 预算」而非强制配额；最终由相关性、优先级与实际
  窗口动态分配。
- 预算以「估算 token 数」为单位：中文按字符数近似（1 字符 ≈ 1 token 的
  保守上界），避免引入 tokenizer 依赖。
- 截断在语义边界（按行/条目）而非硬切字符，避免把一句话切半。

本模块不 import core，纯函数无状态，可独立测试。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List

# 各来源的建议 token 预算（对应方案 2.3 的 CONTEXT_BUDGET）。
# key 与 FeatureFlags 能力名对齐，便于统一门控。
DEFAULT_BUDGET: Dict[str, int] = {
    "safety": 500,        # 危机/安全最高优先，预算最大
    "memory": 800,        # 长期记忆，核心价值
    "persona": 300,       # 人格状态
    "relationship": 250,  # 关系脉络
    "character": 300,     # 角色配置
    "emotion": 200,       # 情绪底色
}

# 单条记忆注入的字符上限（超过截断，防单条超长撑爆预算）
MAX_ITEM_CHARS = 120


def estimate_tokens(text: str) -> int:
    """估算文本 token 数（中文按字符保守上界，英文按空白词近似）。

    无 tokenizer 依赖：中文 1 字符 ≈ 1 token（偏保守，实际常更省），
    连续 ASCII 段按空格分词计。够用于「是否超预算」的粗判。
    """
    if not text:
        return 0
    cjk = sum(1 for ch in text if '\u4e00' <= ch <= '\u9fff')
    ascii_words = sum(1 for w in text.split() if w.isascii())
    return cjk + ascii_words


@dataclass
class BudgetReport:
    """一次裁剪的结果报告（供审计/可观测）。"""
    source: str
    original_items: int
    kept_items: int
    estimated_tokens: int
    budget: int

    @property
    def over_budget(self) -> bool:
        return self.estimated_tokens > self.budget


def clip_to_budget(items: List[str], budget: int,
                   source: str = "generic",
                   max_item_chars: int = MAX_ITEM_CHARS,
                   ) -> tuple[List[str], BudgetReport]:
    """把条目列表裁剪到 token 预算内，返回 (保留条目, 报告)。

    - 逐条先做字符截断（语义近似：按句末标点断，避免硬切）。
    - 按顺序累加，超出 budget 即停止（保留前缀，优先级由调用方排序决定）。
    - 单条超预算的极端情况：至少保留截断后的首条，避免完全丢失该来源。
    """
    kept: List[str] = []
    used = 0
    for item in items:
        if not item:
            continue
        clipped = _clip_item(item, max_item_chars)
        cost = estimate_tokens(clipped)
        if kept and used + cost > budget:
            break
        kept.append(clipped)
        used += cost
    report = BudgetReport(
        source=source,
        original_items=len([i for i in items if i]),
        kept_items=len(kept),
        estimated_tokens=used,
        budget=budget,
    )
    return kept, report


def _clip_item(text: str, max_chars: int) -> str:
    """按语义边界截断单条：优先在句末标点断开，其次硬切。"""
    if len(text) <= max_chars:
        return text
    window = text[:max_chars]
    for punct in ("。", "！", "？", "；", ".", "!", "?", ";"):
        idx = window.rfind(punct)
        if idx >= max_chars // 2:  # 断点太靠前则放弃，宁硬切
            return window[:idx + 1]
    return window
