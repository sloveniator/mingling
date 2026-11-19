from __future__ import annotations

from astrbot.api.event import AstrMessageEvent

_PRIVATE_TYPES = {"private", "friend", "private_message", "friend_message"}
_GROUP_TYPES = {"group", "guild", "group_message"}


def is_private(event: AstrMessageEvent) -> bool:
    """
    私聊判定。多级兜底，**拿不到类型时返回 False**，确保群聊零副作用。
    """
    fn = getattr(event, "is_private_chat", None)
    if callable(fn):
        try:
            return bool(fn())
        except Exception:
            pass

    for attr in ("message_type", "type"):
        val = getattr(event, attr, None)
        if isinstance(val, str):
            low = val.lower()
            if low in _PRIVATE_TYPES:
                return True
            if low in _GROUP_TYPES:
                return False

    obj = getattr(event, "message_obj", None)
    obj_type = getattr(obj, "type", None) if obj is not None else None
    if isinstance(obj_type, str):
        return obj_type.lower() in _PRIVATE_TYPES

    return False


def companion_scope(cfg, event: AstrMessageEvent) -> bool:
    """命灵唯一生效域：总开关打开 + 私聊。"""
    return bool(cfg.enabled) and is_private(event)
