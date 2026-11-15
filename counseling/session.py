"""counseling/session.py -- 多轮心理疏导会话与管理

COUNSEL_STAGES / CounselingSession / CounselingSessionManager /
counseling_turn_prompt：保持多轮疏导连贯性，内存缓存 + KV 持久化。
"""

import json
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from astrbot.api import logger

from .prompts import CounselingPrompts
from .therapy import BOUNDARY_RULES

COUNSEL_STAGES = ["opening", "listening", "exploring", "reframing", "closing"]
COUNSEL_STAGE_LABEL = {
    "opening": "刚聊起来",
    "listening": "在听 TA 说",
    "exploring": "一起梳理",
    "reframing": "帮忙换个角度看",
    "closing": "收个尾、给点力量",
}


@dataclass
class CounselingSession:
    """单个用户的心理疏导会话（可跨多轮延续）。"""
    topic: str = ""
    stage: str = "opening"
    turns: List[Dict] = field(default_factory=list)   # {role, text, ts}
    reflections: List[str] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def add_turn(self, role: str, text: str):
        self.turns.append({"role": role, "text": text[:400], "ts": time.time()})
        # 只保留最近 12 轮，避免上下文爆炸
        if len(self.turns) > 12:
            self.turns = self.turns[-12:]
        self.updated_at = time.time()

    def advance(self, to_stage: Optional[str] = None):
        if to_stage and to_stage in COUNSEL_STAGES:
            self.stage = to_stage
            return
        i = COUNSEL_STAGES.index(self.stage) if self.stage in COUNSEL_STAGES else 0
        if i < len(COUNSEL_STAGES) - 1:
            self.stage = COUNSEL_STAGES[i + 1]

    def to_dict(self) -> dict:
        return {
            "topic": self.topic,
            "stage": self.stage,
            "turns": self.turns,
            "reflections": self.reflections,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "CounselingSession":
        s = cls(
            topic=d.get("topic", ""),
            stage=d.get("stage", "opening"),
            turns=d.get("turns", []),
            reflections=d.get("reflections", []),
            created_at=d.get("created_at", time.time()),
            updated_at=d.get("updated_at", time.time()),
        )
        return s


class CounselingSessionManager:
    """多轮疏导会话管理：内存缓存 + KV 持久化（mingbu.counsel:{uid}）。"""

    KV_KEY = "mingbu.counsel"

    def __init__(self, plugin=None):
        self.plugin = plugin
        self._cache: Dict[str, CounselingSession] = {}

    def _key(self, uid: str) -> str:
        return f"{self.KV_KEY}:{uid}"

    async def get(self, uid: str) -> Optional[CounselingSession]:
        if uid in self._cache:
            return self._cache[uid]
        if self.plugin is None:
            return None
        try:
            raw = await self.plugin.get_kv_data(self._key(uid), None)
        except Exception:
            return None
        if raw:
            try:
                sess = CounselingSession.from_dict(json.loads(raw))
                self._cache[uid] = sess
                return sess
            except Exception:
                return None
        return None

    async def save(self, uid: str, sess: CounselingSession):
        self._cache[uid] = sess
        if self.plugin is None:
            return
        try:
            await self.plugin.put_kv_data(
                self._key(uid), json.dumps(sess.to_dict(), ensure_ascii=False)
            )
        except Exception as e:
            logger.debug(f"[mingbu.counsel] 会话保存失败(忽略): {e}")

    async def end(self, uid: str):
        """结束会话（清理持久化）。"""
        self._cache.pop(uid, None)
        if self.plugin is None:
            return
        try:
            await self.plugin.put_kv_data(self._key(uid), "")
        except Exception:
            pass


def counseling_turn_prompt(
    session: CounselingSession,
    user_text: str,
    emotion_summary: str = "",
    dominant: Optional[str] = None,
) -> str:
    """构建多轮疏导的本轮提示（作为 llm_tool 返回内容）。"""
    # 把最近几轮整理成"对话纪要"交给 LLM
    recent = session.turns[-6:]
    transcript = "\n".join(
        f"  {'TA' if t['role'] == 'user' else '我'}: {t['text']}" for t in recent
    )
    therapy_guide = CounselingPrompts._therapeutic_guidance(dominant)
    stage_label = COUNSEL_STAGE_LABEL.get(session.stage, session.stage)

    return f"""[朋友·多轮心理疏导 · 第 {len(session.turns)} 轮]
当前话题：{session.topic or '（未限定，顺着 TA 说）'}
当前阶段：{stage_label}
近期对话纪要：
{transcript}

用户这一轮说了：{user_text}
{('TA 最近的情绪：' + emotion_summary) if emotion_summary else ''}

请像朋友一样，接着上面的对话继续陪 TA：
1. 先回应用户这一轮说的，别假装没看过前面的话（多轮连贯）
2. 顺着阶段自然推进，但永远以 TA 的节奏为准——TA 想停就停
3. 该倾听时倾听（重复 TA 的关键词、反映情绪），该提问时再轻轻问
4. 如果聊到卡住的点，用苏格拉底式提问帮 TA 自己想，不直接给答案
5. 绝不说教、绝不当"人生导师"，把选择权留在 TA 手里
6. 恰当时机帮 TA 换个温和的视角看问题（认知重构）

{therapy_guide}

{BOUNDARY_RULES}
"""
