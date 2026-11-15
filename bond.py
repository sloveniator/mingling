"""bond.py -- 角色羁绊（每用户 × 每角色一份）
==================================================
「羁绊管理」的落地数据模型。与 ``persona.Relationship``（用户 ↔ 整个 AI
这个存在的整体关系，供 proactive 等跨角色场景使用）不同，``Bond`` 表达的是
**用户与某一个具体角色**之间的羁绊——亲密度、关系模式、羁绊称号与里程碑。

配合「角色取代人格」：当前角色是谁，注入的就是用户与该角色的羁绊；
切换角色，羁绊随之切换，语义清晰、多角色各自独立。

数据模型：
  - 存储 key  ``mingbu.bond:{uid}:{char_id}``
  - 字段：score（亲密度 0-100）、mode（关系模式）、intimacy_moments（亲密时刻数）、
    milestones（羁绊里程碑，如「首次达到挚友」）、last_interaction
"""
from __future__ import annotations

from typing import List, Optional, Tuple

from .bj_time import now_bj

# 羁绊事件词库：用户的一句话 → 一种羁绊事件（→ Bond.nudge 的 kind）
# 按优先级检测（apology 最高、intimacy 最低），避免「对不起…别生气」误判。
BOND_KEYWORDS: dict = {
    "apology": [
        "对不起", "抱歉", "我错了", "是我的错", "原谅我", "别生气",
        "我道歉", "对不住", "别怪我",
    ],
    "refusing": [
        "别理我", "想静静", "让我一个人", "别烦我", "不想说话",
        "离我远点", "别管我", "安静一下",
    ],
    "hurt": [
        "冷落", "敷衍", "你变了", "失望", "讨厌你", "不在乎",
        "不理人", "把我晾", "好冷淡",
    ],
    "comfort": [
        "辛苦了", "谢谢你", "别难过", "心疼你", "有我在",
        "抱抱", "没事的", "我陪你", "加油",
    ],
    "intimacy": [
        "爱你", "想你", "喜欢你", "离不开你", "有你真好",
        "么么哒", "好喜欢你", "你最懂我", "庆幸有你",
    ],
}

# 羁绊事件检测优先级：道歉 > 想静静 > 受伤 > 安慰 > 亲密
_BOND_EVENT_ORDER = ("apology", "refusing", "hurt", "comfort", "intimacy")

# 羁绊称号（按亲密度阈值降序）：(阈值, 称号, 描述)
BOND_TITLES: List[Tuple[float, str, str]] = [
    (80, "羁绊", "你们之间已经有种说不清道不明的默契，很多话不用说完就懂"),
    (55, "挚友", "你们已经是能互怼的铁哥们/闺蜜了，可以很直接"),
    (30, "熟悉", "你们越来越熟，开始把彼此当朋友"),
    (0, "初识", "你们刚认识，还在互相试探"),
]


def detect_bond_event(text: str) -> Optional[str]:
    """从用户消息检测羁绊事件，返回 nudge kind（apology/refusing/hurt/comfort/intimacy）。

    无命中返回 None。按优先级返回首个命中，避免复合语义误判。
    """
    if not text:
        return None
    for kind in _BOND_EVENT_ORDER:
        if any(kw in text for kw in BOND_KEYWORDS.get(kind, [])):
            return kind
    return None


def bond_title(score: float, intimacy_moments: int) -> Tuple[str, str]:
    """由亲密度 + 亲密时刻数推导羁绊称号，返回 (称号, 描述)。"""
    if score >= 80 or intimacy_moments >= 5:
        return BOND_TITLES[0][1], BOND_TITLES[0][2]
    for threshold, title, desc in BOND_TITLES:
        if score >= threshold:
            return title, desc
    return "初识", BOND_TITLES[-1][2]


class Bond:
    """用户与某个角色的羁绊。"""

    MODE_NORMAL = "normal"
    MODE_WARMING = "warming"
    MODE_ATTACHED = "attached"
    MODE_REFUSING = "refusing"
    MODE_HURT = "hurt"

    def __init__(self, user_id: str, char_id: str):
        self.user_id = user_id
        self.char_id = char_id
        self.score: float = 10.0
        self.mode: str = self.MODE_NORMAL
        self.intimacy_moments: int = 0
        self.milestones: List[dict] = []
        self.last_interaction: str = now_bj().isoformat()

    def to_dict(self) -> dict:
        return {
            "user_id": self.user_id,
            "char_id": self.char_id,
            "score": self.score,
            "mode": self.mode,
            "intimacy_moments": self.intimacy_moments,
            "milestones": self.milestones[-20:],
            "last_interaction": self.last_interaction,
        }

    _FIELD_DEFAULTS = {
        "score": 10.0, "mode": "normal", "intimacy_moments": 0,
        "milestones": list,
    }

    @classmethod
    def from_dict(cls, d: dict) -> "Bond":
        b = cls(d.get("user_id", ""), d.get("char_id", ""))
        for field, default in cls._FIELD_DEFAULTS.items():
            val = d.get(field)
            if val is None:
                val = [] if default is list else default
            setattr(b, field, val)
        b.last_interaction = d.get("last_interaction", now_bj().isoformat())
        return b

    def title(self) -> Tuple[str, str]:
        return bond_title(self.score, self.intimacy_moments)

    def _recompute_mode(self):
        if self.score >= 70:
            self.mode = self.MODE_ATTACHED
        elif self.score >= 40:
            self.mode = self.MODE_WARMING
        else:
            self.mode = self.MODE_NORMAL

    def interact(self, positive: bool = True):
        """普通互动：轻微涨/跌亲密度（每条消息一次）。受伤/拒绝态保持粘性。"""
        self.score = max(0.0, min(100.0, self.score + (1.2 if positive else -1.5)))
        self.last_interaction = now_bj().isoformat()
        if self.mode not in (self.MODE_HURT, self.MODE_REFUSING):
            self._recompute_mode()

    def nudge(self, kind: str):
        """事件级羁绊变化：intimacy / refusing / hurt / comfort / apology。"""
        if kind == "intimacy":
            self.score = min(100.0, self.score + 5)
            self.intimacy_moments += 1
            self._maybe_milestone()
            self._recompute_mode()
        elif kind == "refusing":
            # 用户想静静：进入拒绝态（粘性，不被分数回弹）
            self.mode = self.MODE_REFUSING
            self.score = max(0.0, self.score - 5)
        elif kind == "hurt":
            self.mode = self.MODE_HURT
            self.score = max(0.0, self.score - 8)
        elif kind == "comfort":
            self.score = min(100.0, self.score + 3)
            # 安慰逐步解除受伤/拒绝态
            if self.mode in (self.MODE_HURT, self.MODE_REFUSING):
                if self.score >= 35:
                    self.mode = self.MODE_NORMAL
            else:
                self._recompute_mode()
        elif kind == "apology":
            if self.mode in (self.MODE_HURT, self.MODE_REFUSING):
                self.mode = self.MODE_NORMAL
            self.score = min(100.0, self.score + 2)
            self._recompute_mode()
        self.last_interaction = now_bj().isoformat()

    def _maybe_milestone(self):
        """首次达到某羁绊称号时，记录一条里程碑。「初识」是初始态，不计。"""
        title, _ = self.title()
        if title == "初识":
            return
        if not any(m.get("title") == title for m in self.milestones):
            self.milestones.append({
                "ts": now_bj().isoformat(),
                "title": title,
            })
