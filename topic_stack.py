from __future__ import annotations

import re
import time

from store import Store

_STOPWORDS = {
    "今天", "现在", "这个", "那个", "什么", "怎么", "就是", "然后", "还是",
    "可以", "感觉", "一下", "有点", "其实", "真的", "已经", "可能", "应该",
    "我们", "你们", "他们", "自己", "时候", "问题", "事情", "东西", "地方",
    "知道", "觉得", "不要", "没有", "不是", "这样", "那样", "但是", "因为",
}

_TOKEN_RE = re.compile(r"[\u4e00-\u9fa5]{2,6}|[A-Za-z][A-Za-z0-9_\-]{1,15}")
_MAX_TOPICS = 6


class TopicStack:
    def __init__(self, store: Store):
        self._store = store

    @staticmethod
    def extract(text: str) -> list[str]:
        if not text:
            return []
        tokens = _TOKEN_RE.findall(text)
        out: list[str] = []
        for t in tokens:
            if t in _STOPWORDS:
                continue
            if t not in out:
                out.append(t)
        return out[:5]

    async def load(self, umo: str) -> dict:
        raw = await self._store.load(umo, "topic_stack", None)
        if not isinstance(raw, dict):
            return {"current": None, "stack": [], "updated_at": 0.0}
        raw.setdefault("current", None)
        raw.setdefault("stack", [])
        raw.setdefault("updated_at", 0.0)
        return raw

    @staticmethod
    def _topic_overlap(tokens: list[str], topic: str) -> bool:
        """双向子串 + 3 字片段匹配，弥补正则切片不一致导致的漏匹配。"""
        if not topic:
            return False
        for t in tokens:
            if t in topic or topic in t:
                return True
        # 3 字片段匹配：跳过停用词，避免"今天/现在"等高频词误命中
        for t in tokens:
            for i in range(len(t) - 2):
                frag = t[i:i+3]
                if frag in _STOPWORDS:
                    continue
                if frag in topic:
                    return True
        return False

    async def observe(self, umo: str, text: str) -> str | None:
        """观察一条用户消息，更新话题栈并返回当前主话题。"""
        state = await self.load(umo)
        tokens = self.extract(text)
        if not tokens:
            return state.get("current")

        current = state.get("current")
        stack: list[str] = state.get("stack", [])

        # 1. 当前话题延续
        if current and self._topic_overlap(tokens, current):
            state["updated_at"] = time.time()
            await self._store.save(umo, "topic_stack", state)
            return current

        # 2. 从栈中弹出一个被重新提及的旧话题
        for idx in range(len(stack) - 1, -1, -1):
            old = stack[idx]
            if self._topic_overlap(tokens, old):
                if current:
                    stack.append(current)
                state["current"] = old
                stack.pop(idx)
                state["stack"] = stack[-_MAX_TOPICS:]
                state["updated_at"] = time.time()
                await self._store.save(umo, "topic_stack", state)
                await self._store.append_timeline(umo, "topic_resume", {"topic": old})
                return old

        # 3. 新话题入栈
        new_topic = " / ".join(tokens[:2])
        if current:
            stack.append(current)
        state["current"] = new_topic
        state["stack"] = stack[-_MAX_TOPICS:]
        state["updated_at"] = time.time()
        await self._store.save(umo, "topic_stack", state)
        await self._store.append_timeline(umo, "topic_new", {"topic": new_topic})
        return new_topic

    async def current(self, umo: str) -> str | None:
        state = await self.load(umo)
        return state.get("current")

    @staticmethod
    def matches(text: str, topic: str | None) -> bool:
        if not topic:
            return False
        return any(t in text for t in topic.split(" / ") if t)
