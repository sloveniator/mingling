"""integration/identity_resolver.py -- 统一身份与会话识别（2.0 第二阶段）。

职责（对应方案 3.3）：把 AstrBot 的消息事件解析成稳定的身份/会话标识，
供记忆、人格、关系等服务使用，避免直接在各处裸取 get_sender_id()。

核心安全原则：
- **不同平台的用户默认绝不自动合并**。platform_user_id 是平台内 id
  （QQ 号、微信 wxid、企微 wm/wo...），跨平台同名/同号不代表同一人。
- unified_user_id 默认 = f"{platform}:{platform_user_id}"，即按平台隔离。
  只有显式绑定（用户主动关联账号，写入 KV 映射）才共享长期记忆。
- 群聊与私聊是不同 ConversationIdentity（session_id 不同），群聊记忆
  隔离交由 Memory.scope（第一阶段任务 3）处理，本模块只负责标识。

本模块不 import core，鸭子类型解耦，可独立单测。所有解析均安全降级：
字段缺失返回空串/None，绝不抛异常。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from astrbot.api import logger

# 显式身份绑定映射的 KV 前缀（binding[platform_user_id] -> unified_user_id）
KV_BINDING_PREFIX = "mingbu.identity.binding"

# 会话类型
CONV_PRIVATE = "private"
CONV_GROUP = "group"
CONV_UNKNOWN = "unknown"


@dataclass(frozen=True)
class UserIdentity:
    """用户身份。unified_user_id 是跨平台归一后的唯一键。"""
    platform: str
    platform_user_id: str
    unified_user_id: str
    # 是否来自显式绑定（True=跨平台共享记忆，False=仅本平台隔离）
    is_bound: bool = False


@dataclass(frozen=True)
class ConversationIdentity:
    """会话身份。区分群聊/私聊，session_id 是 AstrBot 的 unified_msg_origin。"""
    platform: str
    session_id: str
    conversation_type: str
    group_id: Optional[str] = None


def extract_platform(origin: str) -> str:
    """从 unified_msg_origin（'platform:type:uid'）提取平台名，安全降级。"""
    if not origin:
        return ""
    try:
        return origin.split(":", 1)[0].strip().lower()
    except Exception:
        return ""


def default_unified_id(platform: str, platform_user_id: str) -> str:
    """默认统一 id：按平台隔离，绝不跨平台自动合并。"""
    if platform and platform_user_id:
        return f"{platform}:{platform_user_id}"
    return platform_user_id or platform or ""


async def resolve_user(event, kv_get=None) -> UserIdentity:
    """从事件解析用户身份。

    Args:
        event: AstrBot 消息事件（需 get_sender_id / unified_msg_origin）。
        kv_get: 可选的 ``async get_kv_data(key, default)``，用于查显式绑定。
            传入时才启用跨平台绑定查询；不传则严格按平台隔离（更保守）。

    Returns:
        UserIdentity。platform_user_id 取不到时 unified_user_id 退化为空串，
        调用方据此跳过个人记忆读写（不能把不同人混到一个桶）。
    """
    try:
        platform_user_id = ""
        getter = getattr(event, "get_sender_id", None)
        if callable(getter):
            platform_user_id = str(getter() or "")
    except Exception as e:  # noqa: BLE001
        logger.debug(f"[mingbu.identity] 取 sender_id 失败(忽略): {e}")

    origin = getattr(event, "unified_msg_origin", None) or \
        getattr(event, "session_id", None) or ""
    platform = extract_platform(origin if isinstance(origin, str) else "")

    unified = default_unified_id(platform, platform_user_id)

    # 显式绑定查询：仅当 kv_get 提供时才尝试跨平台合并
    is_bound = False
    if kv_get is not None and platform_user_id:
        try:
            bound = await kv_get(f"{KV_BINDING_PREFIX}:{platform_user_id}", None)
            if bound and isinstance(bound, str) and bound.strip():
                unified = bound.strip()
                is_bound = True
        except Exception as e:  # noqa: BLE001
            logger.debug(f"[mingbu.identity] 查绑定失败(忽略): {e}")

    return UserIdentity(
        platform=platform,
        platform_user_id=platform_user_id,
        unified_user_id=unified,
        is_bound=is_bound,
    )


async def resolve_conversation(event) -> ConversationIdentity:
    """从事件解析会话身份（群聊/私聊区分）。"""
    origin = getattr(event, "unified_msg_origin", None) or \
        getattr(event, "session_id", None) or ""
    origin = origin if isinstance(origin, str) else ""
    platform = extract_platform(origin)

    msg_obj = getattr(event, "message_obj", None)
    group_id = getattr(msg_obj, "group_id", None) or None
    # 会话类型：有 group_id 视为群聊；否则看 message_obj.type
    if group_id:
        conv_type = CONV_GROUP
    else:
        msg_type = getattr(msg_obj, "type", None)
        type_str = str(msg_type) if msg_type is not None else ""
        if "group" in type_str.lower():
            conv_type = CONV_GROUP
        elif "friend" in type_str.lower() or "private" in type_str.lower():
            conv_type = CONV_PRIVATE
        else:
            conv_type = CONV_UNKNOWN

    return ConversationIdentity(
        platform=platform,
        session_id=origin,
        conversation_type=conv_type,
        group_id=group_id,
    )


async def bind_identity(kv_put, platform_user_id: str, unified_user_id: str) -> bool:
    """写入显式身份绑定：把某平台的 platform_user_id 归并到 unified_user_id。

    这是「跨平台共享记忆」的唯一入口——默认各平台身份彼此隔离（见
    default_unified_id），只有用户主动关联账号（经此函数落一份 KV 映射）后，
    resolve_user 才会把它们归并到同一 unified_user_id。

    Args:
        kv_put: ``async put_kv_data(key, value)``。
        platform_user_id: 从属身份的平台内 id（如微信 wxid）。
        unified_user_id: 目标主身份（如 ``qq:12345``）。空串视为解绑。

    Returns:
        是否写入成功。任一步异常返回 False（安全降级，绝不抛出）。
    """
    if not platform_user_id:
        return False
    key = f"{KV_BINDING_PREFIX}:{platform_user_id}"
    try:
        val = (unified_user_id or "").strip()
        if val:
            await kv_put(key, val)
        else:
            # 空目标 = 解绑：写空串让 resolve_user 读到后不启用绑定
            await kv_put(key, "")
        return True
    except Exception as e:  # noqa: BLE001
        logger.debug(f"[mingbu.identity] 写绑定失败(忽略): {e}")
        return False
