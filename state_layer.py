from __future__ import annotations

import re

from emotion_trace import EmotionTrace
from open_loops import OpenLoopManager
from relation_memory import RelationMemory
from relationship import RelationshipManager
from topic_stack import TopicStack


def estimate_tokens(text: str) -> int:
    """粗估 token：中文按 1.6 字/token，英文按 4 字符/token。"""
    if not text:
        return 0
    cjk = len(re.findall(r"[\u4e00-\u9fff]", text))
    other = len(text) - cjk
    return int(cjk / 1.6 + other / 4) + 1


class StateLayerBuilder:
    """
    构建命灵状态层。追加到 system_prompt 末尾，带边界标记。
    绝不修改 req.contexts，绝不替换 system_prompt。
    """

    OPEN = "<mingling_state>"
    CLOSE = "</mingling_state>"

    def __init__(
        self,
        cfg,
        emotion: EmotionTrace,
        relationship: RelationshipManager,
        topics: TopicStack,
        loops: OpenLoopManager,
        memory: RelationMemory,
    ):
        self._cfg = cfg
        self._emotion = emotion
        self._relationship = relationship
        self._topics = topics
        self._loops = loops
        self._memory = memory

    async def build(self, umo: str, user_text: str) -> str:
        if not self._cfg.state_layer_enabled:
            return ""

        parts: list[str] = []

        # 1. 情绪状态与归因
        if self._cfg.emotion_trace_enabled:
            st = await self._emotion.load(umo)
            cause = await self._emotion.recent_cause(umo)
            line = self._render_emotion(st, cause)
            if line:
                parts.append(line)

        # 2. 关系阶段
        if self._cfg.relationship_event_enabled:
            rel = await self._relationship.get(umo)
            hint = self._relationship.stage_hint(rel.stage)
            if hint:
                parts.append(f"关系阶段：{rel.stage}。{hint}")

        # 3. 主话题 + 未闭环话题
        current_topic = None
        if self._cfg.topic_stack_enabled:
            current_topic = await self._topics.observe(umo, user_text)
            if current_topic:
                parts.append(f"当前主话题：{current_topic}")

        if self._cfg.open_loop_enabled:
            loops = await self._loops.list_open(umo)
            if loops:
                latest = loops[-1]
                parts.append(f"未闭环话题：{latest.topic}（尚未有结果）")

        # 4. 关系记忆锚点
        if self._cfg.relation_memory_enabled:
            anchors = await self._memory.anchors_for(
                umo, current_topic, limit=self._cfg.state_layer_max_anchors
            )
            for a in anchors:
                tag = "（锚点）" if a.get("anchor") else ""
                parts.append(f"记得：{a['topic']} —— {a['content']}{tag}")

        if not parts:
            return ""

        # 预算裁剪：从末尾（低优先级）裁剪
        budget = self._cfg.state_layer_max_tokens
        while parts and estimate_tokens("\n".join(parts)) > budget:
            parts.pop()

        return "\n".join(parts)

    def wrap(self, body: str) -> str:
        if not body:
            return ""
        return f"\n{self.OPEN}\n{body}\n{self.CLOSE}\n"

    @staticmethod
    def _render_emotion(st, cause: str | None) -> str:
        labels = []
        if st.pleasantness <= -0.3:
            labels.append("低落")
        elif st.pleasantness >= 0.3:
            labels.append("愉快")
        if st.tension >= 0.3:
            labels.append("紧张")
        if st.arousal >= 0.5:
            labels.append("情绪波动大")
        if st.certainty <= -0.3:
            labels.append("不确定")
        if not labels:
            return ""
        base = "你此刻的心情：" + "、".join(labels)
        if cause:
            base += f"（因为：{cause}）"
        return base
