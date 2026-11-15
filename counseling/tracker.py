"""counseling/tracker.py -- 情绪信号记录与状态机

EmotionalSignal / UserCounselingState / EmotionalTracker：
逐用户追踪情绪信号，驱动朋友式关心钩子与探索 / 祝福状态机。
"""

import time
from collections import Counter
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from astrbot.api import logger

from .emotion_lexicon import (
    EMOTION_INTENSIFIERS,
    EMOTION_INTENSITY,
    EMOTION_KEYWORDS,
    EMOTION_LABELS,
    POSITIVE_KEYWORDS,
)


@dataclass
class EmotionalSignal:
    """单条情绪信号"""
    type: str          # stress/sadness/uncertainty/worry/hope/self_doubt
    text: str          # 用户原话（截断至 100 字）
    timestamp: float   # Unix 时间戳

    def age_hours(self) -> float:
        """信号距今的小时数"""
        return (time.time() - self.timestamp) / 3600

    def is_fresh(self, hours: float = 24) -> bool:
        """是否在指定小时内"""
        return self.age_hours() <= hours


@dataclass
class UserCounselingState:
    """单个用户的咨询状态"""
    signals: List[EmotionalSignal] = field(default_factory=list)
    phase: str = "idle"           # idle/noticed/hooked/exploring/prophesied
    last_hook_at: float = 0       # 上次抛出钩子的时间
    last_counseling_at: float = 0 # 上次完成咨询的时间
    divination_cache: dict = field(default_factory=dict)  # 最近的占卜结果
    blessing_count: int = 0       # 已收到的祝福次数（防滥发）

    def prune(self, max_age_hours: float = 24):
        """清除过期信号"""
        cutoff = time.time() - max_age_hours * 3600
        self.signals = [s for s in self.signals if s.timestamp > cutoff]

    def recent_signals(self, hours: float = 4) -> List[EmotionalSignal]:
        """获取最近 N 小时内的信号"""
        return [s for s in self.signals if s.is_fresh(hours)]

    def dominant_emotion(self) -> Optional[str]:
        """当前最主要的情绪类型"""
        recent = self.recent_signals(4)
        if not recent:
            recent = self.recent_signals(24)
        if not recent:
            return None
        types = [s.type for s in recent]
        counter = Counter(types)
        return counter.most_common(1)[0][0]

    def emotion_summary(self) -> str:
        """情绪摘要文本"""
        recent = self.recent_signals(6)
        if not recent:
            return ""
        types = [s.type for s in recent]
        counter = Counter(types)
        parts = []
        for t, cnt in counter.most_common(3):
            label = EMOTION_LABELS.get(t, t)
            parts.append(f"{label}({cnt}次)")
        return "、".join(parts)


# ============================================================
# 情绪检测与状态管理
# ============================================================

class EmotionalTracker:
    """全局情绪追踪器。

    每个用户独立追踪，线程不安全（AstrBot 是单线程异步事件循环）。
    """

    def __init__(self):
        self._users: Dict[str, UserCounselingState] = {}

    def _get(self, uid: str) -> UserCounselingState:
        """获取或创建用户状态"""
        if uid not in self._users:
            self._users[uid] = UserCounselingState()
        return self._users[uid]

    # ---- 检测 ----

    @staticmethod
    def detect_emotions(text: str) -> List[str]:
        """检测文本中的情绪类型（可返回多个）"""
        found = set()
        for etype, keywords in EMOTION_KEYWORDS.items():
            if any(kw in text for kw in keywords):
                found.add(etype)
        return list(found)

    @staticmethod
    def detect_positive(text: str) -> bool:
        """检测是否含有积极信号"""
        return any(kw in text for kw in POSITIVE_KEYWORDS)

    @staticmethod
    def detect_intensifiers(text: str) -> List[str]:
        """检测严重度加重词（'崩溃''撑不住'等）。"""
        return [w for w in EMOTION_INTENSIFIERS if w in text]

    def severity_level(self, uid: str) -> int:
        """返回情绪严重度 0-3。

        0=无信号；1=轻微；2=中等（主导情绪基线）；
        3=强烈或近期含加重词（绷不住了）。
        """
        state = self._get(uid)
        recent = state.recent_signals(6)
        if not recent:
            return 0
        dom = state.dominant_emotion()
        base = EMOTION_INTENSITY.get(dom or "", 1)
        boosted = any(self.detect_intensifiers(s.text) for s in recent)
        return min(3, base + (1 if boosted else 0))

    # ---- 记录 ----

    def record(self, uid: str, text: str):
        """记录一条消息的情绪信号"""
        state = self._get(uid)
        state.prune()

        emotions = self.detect_emotions(text)
        for etype in emotions:
            state.signals.append(EmotionalSignal(
                type=etype,
                text=text[:100],
                timestamp=time.time(),
            ))

        # 如果有积极信号，考虑降级/重置部分状态
        if self.detect_positive(text) and emotions:
            # 用户表达了积极情绪，可以降级 phase
            if state.phase in ("noticed", "hooked"):
                # 不立即重置，让当前的钩子或探索自然结束
                logger.debug(f"[mingbu.counsel] {uid} 积极信号期间暂不重置状态")

    # ---- 状态机 ----

    def should_hook(self, uid: str) -> bool:
        """判断是否应该抛出心理咨询钩子。

        条件：
        1. 最近 3 小时内有 >= 2 条情绪信号
        2. 距离上次钩子 >= 1 小时（避免骚扰）
        3. 当前状态 != exploring（不在探索中）
        """
        state = self._get(uid)

        # 频率限制：1 小时内不重复抛钩
        if time.time() - state.last_hook_at < 3600:
            return False

        # 如果正在探索中，不打断
        if state.phase == "exploring":
            return False

        # 需要足够的情绪信号
        recent = state.recent_signals(3)
        if len(recent) < 2:
            return False

        # 进入 noticed 状态
        state.phase = "noticed"
        return True

    def hook_presented(self, uid: str):
        """标记钩子已抛出"""
        state = self._get(uid)
        state.phase = "hooked"
        state.last_hook_at = time.time()

    def exploration_started(self, uid: str):
        """标记用户已开始探索"""
        state = self._get(uid)
        state.phase = "exploring"

    def exploration_completed(self, uid: str, divination_result: Optional[dict] = None):
        """标记探索完成"""
        state = self._get(uid)
        state.phase = "idle"
        state.last_counseling_at = time.time()
        if divination_result:
            state.divination_cache = divination_result

    def should_bless(self, uid: str) -> bool:
        """是否应该发送预言式祝福。

        条件：
        1. 刚完成探索
        2. 占卜/命盘中有积极信号
        3. 用户表达了 hope 情绪
        4. 祝福次数未超标（每用户每日最多 3 次）
        """
        state = self._get(uid)

        # 频率限制
        if state.blessing_count >= 3:
            return False

        # 需要有最近的情绪信号（用户活跃中）
        recent = state.recent_signals(6)
        if not recent:
            return False

        # 需要有 hope 类型的信号
        hope_signals = [s for s in recent if s.type == "hope"]
        if not hope_signals:
            return False

        return True

    def blessing_sent(self, uid: str):
        """标记已发送祝福"""
        state = self._get(uid)
        state.blessing_count += 1
        state.phase = "idle"

    def reset_daily(self):
        """每日重置祝福计数（建议通过定时任务调用）"""
        for state in self._users.values():
            state.blessing_count = 0

    # ---- 查询 ----

    def get_state(self, uid: str) -> UserCounselingState:
        """获取用户咨询状态"""
        return self._get(uid)

    def get_emotion_summary(self, uid: str) -> str:
        """获取用户情绪摘要"""
        state = self._get(uid)
        return state.emotion_summary()

    def get_dominant_emotion(self, uid: str) -> Optional[str]:
        """获取用户主要情绪"""
        state = self._get(uid)
        return state.dominant_emotion()
