"""
stt.py -- 语音转文字（STT）辅助
================================
检测消息链中的语音组件（Record），用 AstrBot 的 STT provider 转写为文本。
所有失败路径都优雅降级（返回 None），不会中断主对话流程。
"""
import inspect
from typing import Any, List, Optional

from astrbot.api import logger

from .provider_router import get_stt_provider

try:
    from astrbot.api.message_components import Record as _Record
except Exception:
    _Record = None


def is_stt_enabled(cfg) -> bool:
    """判断语音转写(STT)在当前配置下是否启用。

    STT 完全走 AstrBot STT provider，因此只有 ``stt_enabled`` 显式为 true
    才启用；显式为 false 强制关闭；未设置时默认关闭（需用户在面板打开，
    并在 AstrBot 主设置配好 STT provider）。

    Args:
        cfg: 插件配置字典（``_config``）。

    Returns:
        bool：是否应转写语音。
    """
    if not isinstance(cfg, dict):
        return False
    return cfg.get("stt_enabled") is True


def collect_record_audio_urls(chain) -> List[str]:
    """从消息链收集语音（Record）组件的音频地址（url / file）。"""
    urls: List[str] = []
    if not chain:
        return urls
    for comp in chain:
        is_record = getattr(comp, "type", "") == "Record"
        if _Record and isinstance(comp, _Record):
            is_record = True
        if not is_record:
            continue
        url = getattr(comp, "url", "") or getattr(comp, "file", "")
        if not url and hasattr(comp, "convert_to_file_path"):
            try:
                p = comp.convert_to_file_path()
                if not inspect.isawaitable(p):
                    url = p
            except Exception:
                url = ""
        if url:
            urls.append(url)
    return urls


def strip_record_components(chain) -> "List[Any]":
    """从消息链移除全部语音（Record）组件，返回新列表。

    语音转写成功后调用：音频已转成文字，若 Record 仍留在链里，
    AstrBot 给多模态主模型组装请求时会带 audio 分片，而 SiliconFlow
    等网关只放行 text+image_url → 主模型 400、被迫每轮落 fallback。

    Args:
        chain: AstrBot 消息链（``msg_obj.message``，list）。

    Returns:
        移除 Record 后的新 list；非 list / 空则原样返回。
    """
    if not isinstance(chain, list):
        return chain
    kept = [
        c for c in chain
        if not (isinstance(c, _Record) or getattr(c, "type", "") == "Record")
    ]
    return kept


async def transcribe_audio(ctx, audio_url: str, provider_id: str = "") -> Optional[str]:
    """用 AstrBot 的 STT provider 把一段音频转写为文本。失败返回 None。

    STT 完全走 AstrBot STT provider，插件不持有任何凭据，也不做直连调用。
    配置项只需 ``stt_enabled``（总开关）与 ``provider_stt``（可选指定 ID）。
    """
    provider = await get_stt_provider(ctx, provider_id)
    if not provider or not hasattr(provider, "get_text"):
        return None
    try:
        res = provider.get_text(audio_url)
        if inspect.isawaitable(res):
            res = await res
        return (res or "").strip() or None
    except Exception as e:
        logger.debug(f"[mingbu] STT 转写失败(忽略): {e}")
        return None


async def transcribe_event_voice(event, ctx, provider_id: str = "") -> Optional[str]:
    """转写事件中全部语音组件，返回合并后的文本（走 AstrBot STT provider）。"""
    msg_obj = getattr(event, "message_obj", None)
    chain = getattr(msg_obj, "message", None)
    urls = collect_record_audio_urls(chain)
    if not urls:
        return None
    parts: List[str] = []
    for u in urls:
        t = await transcribe_audio(ctx, u, provider_id)
        if t:
            parts.append(t)
    return "\n".join(parts).strip() if parts else None
