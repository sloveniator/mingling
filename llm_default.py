"""统一使用 AstrBot 当前默认对话模型（不读插件自有 API Key）。"""
from __future__ import annotations

import asyncio

from astrbot.api import logger
from astrbot.api.provider import ProviderRequest

DEFAULT_TIMEOUT = 60


async def ask_default(
    context,
    prompt: str,
    *,
    system: str = "",
    session_id: str = "mingling_cmd",
    timeout: int = DEFAULT_TIMEOUT,
) -> str:
    """用 AstrBot 默认对话服务商生成文本；失败返回空串，由调用方降级。"""
    if context is None or not prompt:
        return ""
    req = ProviderRequest(prompt=prompt, session_id=session_id)
    if system:
        try:
            req.system_prompt = system
        except Exception:
            pass
    try:
        from provider_router import default_llm_request

        resp = await asyncio.wait_for(
            default_llm_request(context, req), timeout=timeout
        )
        if resp is None:
            return ""
        text = (
            getattr(resp, "completion_text", None)
            or getattr(resp, "response_text", None)
            or ""
        )
        return str(text).strip()
    except asyncio.TimeoutError:
        logger.warning("[mingling] 默认模型调用超时")
        return ""
    except Exception as e:  # noqa: BLE001 - 命令降级，不阻断聊天
        logger.warning(f"[mingling] 默认模型调用失败(已降级): {e}")
        return ""
