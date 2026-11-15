"""
hooks/persona.py -- 人格 + 记忆 + 关系块注入
"""
from ..util import inject_text_part
from .base import HookProcessor


class PersonaHook(HookProcessor):
    """人格 + 记忆 + 关系块注入。"""
    async def process(self, core, event, req, **kwargs):
        inject = kwargs.get("inject_persona")
        uid = event.get_sender_id()
        if inject and core.persona:
            block = await core._build_persona_block(
                uid, kwargs.get("rel"), in_group=kwargs.get("is_group"),
                rerank_query=kwargs.get("message_text", "")
            )
            if block:
                inject_text_part(req, block)
        return True
