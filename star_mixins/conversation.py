"""
star_mixins/conversation.py -- 聊天上下文管理：结构化轮次记录 + 话题追踪
=============================================================
聊天上下文管理：结构化轮次记录 + 话题追踪。

本模块由 ``MingbuStar`` 混入。方法体内的 ``self.<engine>`` 访问依赖
``MingbuStar.__getattr__`` 代理到 ``MingbuCore``，因此 mixin 本身不 import
``core``，也就不产生循环依赖。
"""
import json
from typing import List

from astrbot.api import logger

from ..bj_time import now_bj
from ..constants import KV_CONTEXT


class ConversationContextMixin:
    """聊天上下文管理：结构化轮次记录 + 话题追踪。"""

    TURN_HISTORY = 10  # 保留的近期轮次上限（支持多轮对话连贯）

    async def _append_turn(self, uid: str, role: str, text: str):
        """记录一条带角色与时间戳的对话轮次（用户/本机）。"""
        try:
            raw = await self.get_kv_data(f"{KV_CONTEXT}:{uid}", None)
            turns = json.loads(raw) if raw else []
            if not isinstance(turns, list):
                turns = []
            turns.append({"role": role, "text": text, "ts": now_bj().isoformat()})
            if len(turns) > self.TURN_HISTORY:
                turns = turns[-self.TURN_HISTORY:]
            await self.put_kv_data(f"{KV_CONTEXT}:{uid}", json.dumps(turns, ensure_ascii=False))
        except Exception as e:
            logger.debug(f"[mingbu] 追加上下文轮次失败(已忽略): {e}")

    async def _get_turns(self, uid: str, n: int = TURN_HISTORY) -> List[dict]:
        """读取结构化轮次（[{role,text,ts}, ...]）。"""
        try:
            raw = await self.get_kv_data(f"{KV_CONTEXT}:{uid}", None)
            turns = json.loads(raw) if raw else []
            if not isinstance(turns, list):
                return []
            return turns[-n:]
        except Exception as e:
            logger.debug(f"[mingbu] 读取上下文轮次失败(已忽略): {e}")
            return []

    async def _append_context(self, uid: str, text: str):
        await self._append_turn(uid, "user", text)

    async def _get_context(self, uid: str) -> List[str]:
        """兼容旧接口：返回最近轮次的纯文本列表（不分角色）。"""
        turns = await self._get_turns(uid)
        return [t.get("text", "") for t in turns if t.get("role") == "user"]

    async def _set_topic(self, uid: str, topic: str):
        try:
            await self.put_kv_data(f"mingbu.topic:{uid}", topic)
        except Exception as e:
            logger.debug(f"[mingbu] 话题记录失败(已忽略): {e}")

    async def _get_topic(self, uid: str) -> str:
        try:
            return await self.get_kv_data(f"mingbu.topic:{uid}", "") or ""
        except Exception:
            return ""
