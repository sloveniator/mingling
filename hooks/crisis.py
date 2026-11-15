"""
hooks/crisis.py -- 危机支持注入（最高优先级）
"""
from ..counseling import (
    crisis_support_prompt,
    detect_crisis,
    technical_guidance_prompt,
)
from ..util import inject_text_part
from .base import HookProcessor


class CrisisHook(HookProcessor):
    """危机支持注入（最高优先级）。

    危机检测已在 _on_llm_request_body 中完成并通过 kwargs["crisis"] 传入，
    这里只负责注入，避免重复扫描与重复 append。若调用方未传（例如单独复用
    本钩子），则回退为自行检测一次。
    """
    async def process(self, core, event, req, **kwargs):
        is_private = kwargs.get("is_private")
        if not is_private or not core._config.get("counseling_crisis_enabled", True):
            return True

        crisis = kwargs.get("crisis")
        if crisis is None and "crisis" not in kwargs:
            crisis = detect_crisis(kwargs.get("message_text"))
        if not crisis:
            return True

        level = crisis.get("level", "ideation")
        prompt_fn = crisis_support_prompt if level == "action" else technical_guidance_prompt
        inject_text_part(req, prompt_fn(crisis.get("keyword", "")))
        return True
