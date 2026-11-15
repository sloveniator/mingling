"""2.0 第二阶段：统一身份识别（Identity Resolver）回归测试。

核心安全契约：
- 不同平台用户默认绝不自动合并（unified_user_id 按 platform 隔离）。
- 仅显式绑定（KV 映射）才跨平台共享长期记忆。
- 群聊/私聊解析为不同 ConversationIdentity。
- 所有解析安全降级，字段缺失不抛异常。
"""
import asyncio

import pytest

from integration.identity_resolver import (
    CONV_GROUP,
    CONV_PRIVATE,
    CONV_UNKNOWN,
    bind_identity,
    default_unified_id,
    extract_platform,
    resolve_conversation,
    resolve_user,
)


class _FakeEvent:
    def __init__(self, sender_id="12345", origin="qq:FriendMessage:12345",
                 msg_type="FriendMessage", group_id=None):
        self._sender_id = sender_id
        self.unified_msg_origin = origin
        self.session_id = origin
        self.message_obj = _FakeMsgObj(msg_type, group_id)

    def get_sender_id(self):
        return self._sender_id


class _FakeMsgObj:
    def __init__(self, msg_type, group_id):
        self.type = msg_type
        self.group_id = group_id


def _run(coro):
    return asyncio.run(coro)


def test_extract_platform():
    assert extract_platform("qq:FriendMessage:123") == "qq"
    assert extract_platform("telegram:GroupMessage:abc") == "telegram"
    assert extract_platform("") == ""
    assert extract_platform("weird") == "weird"  # 无冒号时整串返回


def test_default_unified_id_isolates_by_platform():
    """不同平台同号 → 不同 unified_user_id（默认不合并）。"""
    assert default_unified_id("qq", "123") == "qq:123"
    assert default_unified_id("wechat", "123") == "wechat:123"
    assert default_unified_id("qq", "123") != default_unified_id("wechat", "123")


def test_resolve_user_default_isolation():
    """默认（无 kv_get）按平台隔离，is_bound=False。"""
    ev = _FakeEvent(sender_id="12345", origin="qq:FriendMessage:12345")
    u = _run(resolve_user(ev))
    assert u.platform == "qq"
    assert u.platform_user_id == "12345"
    assert u.unified_user_id == "qq:12345"
    assert u.is_bound is False


def test_resolve_user_explicit_binding_merges():
    """显式绑定（KV 映射）→ 跨平台合并到同一 unified_user_id。"""
    async def kv_get(key, default=None):
        if key.endswith("wxid_abc"):
            return "qq:12345"  # 微信账号已绑定到 QQ 身份
        return default

    ev = _FakeEvent(sender_id="wxid_abc", origin="wechat:FriendMessage:wxid_abc")
    u = _run(resolve_user(ev, kv_get=kv_get))
    assert u.platform == "wechat"
    assert u.unified_user_id == "qq:12345"  # 合并到绑定的主身份
    assert u.is_bound is True


def test_resolve_user_no_binding_keeps_isolated():
    """未绑定（kv 查不到）→ 保持平台隔离。"""
    async def kv_get(key, default=None):
        return default  # 无任何绑定

    ev = _FakeEvent(sender_id="wxid_x", origin="wechat:FriendMessage:wxid_x")
    u = _run(resolve_user(ev, kv_get=kv_get))
    assert u.unified_user_id == "wechat:wxid_x"
    assert u.is_bound is False


def test_resolve_user_missing_sender_degrades():
    """取不到 sender_id → 安全降级，不抛异常。"""
    class _NoSender:
        unified_msg_origin = "qq:FriendMessage:x"

        def get_sender_id(self):
            return None

    u = _run(resolve_user(_NoSender()))
    assert u.platform_user_id == ""
    assert u.platform == "qq"


def test_resolve_conversation_private_vs_group():
    """群聊/私聊解析为不同会话类型。"""
    priv = _run(resolve_conversation(
        _FakeEvent(origin="qq:FriendMessage:12345",
                   msg_type="FriendMessage", group_id=None)))
    grp = _run(resolve_conversation(
        _FakeEvent(origin="qq:GroupMessage:888",
                   msg_type="GroupMessage", group_id="g001")))

    assert priv.conversation_type == CONV_PRIVATE
    assert grp.conversation_type == CONV_GROUP
    assert grp.group_id == "g001"
    assert priv.session_id != grp.session_id


def test_resolve_conversation_unknown_type():
    """未知消息类型 → unknown，不抛异常。"""
    ev = _FakeEvent(msg_type="WeirdType", group_id=None)
    conv = _run(resolve_conversation(ev))
    assert conv.conversation_type == CONV_UNKNOWN


# ---- bind_identity（2.0 第三阶段：补齐显式绑定写入能力）----

class _KVStore:
    """最小 KV 存取：get/put 落到同一字典，支持读写闭环验证。"""

    def __init__(self):
        self.data = {}

    async def get(self, key, default=None):
        return self.data.get(key, default)

    async def put(self, key, value):
        self.data[key] = value


def test_bind_then_resolve_roundtrip():
    """绑定写入 → resolve_user 读到 → 跨平台合并（读写闭环）。"""
    kv = _KVStore()

    # 1) 默认未绑定：微信独立
    ev_wx = _FakeEvent(sender_id="wxid_abc", origin="wechat:FriendMessage:wxid_abc")
    u0 = _run(resolve_user(ev_wx, kv_get=kv.get))
    assert u0.unified_user_id == "wechat:wxid_abc"
    assert u0.is_bound is False

    # 2) 写绑定：wxid_abc → qq:12345
    ok = _run(bind_identity(kv.put, "wxid_abc", "qq:12345"))
    assert ok is True

    # 3) 再解析：合并到 QQ 主身份
    u1 = _run(resolve_user(ev_wx, kv_get=kv.get))
    assert u1.unified_user_id == "qq:12345"
    assert u1.is_bound is True


def test_bind_empty_target_unbinds():
    """空目标 = 解绑：resolve_user 读到空串后回到平台隔离。"""
    kv = _KVStore()
    _run(bind_identity(kv.put, "wxid_x", "qq:12345"))  # 先绑
    ev = _FakeEvent(sender_id="wxid_x", origin="wechat:FriendMessage:wxid_x")
    bound_u = _run(resolve_user(ev, kv_get=kv.get))
    assert bound_u.is_bound is True

    _run(bind_identity(kv.put, "wxid_x", ""))  # 解绑
    u = _run(resolve_user(ev, kv_get=kv.get))
    assert u.is_bound is False
    assert u.unified_user_id == "wechat:wxid_x"


def test_bind_missing_platform_user_id_returns_false():
    """platform_user_id 为空 → 不写入，返回 False。"""
    kv = _KVStore()
    assert _run(bind_identity(kv.put, "", "qq:12345")) is False
    assert kv.data == {}  # 未落任何键


def test_bind_kv_put_exception_degrades():
    """kv_put 抛异常 → 安全降级返回 False，绝不抛出。"""
    async def bad_put(key, value):
        raise RuntimeError("kv down")

    assert _run(bind_identity(bad_put, "wxid_x", "qq:12345")) is False


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
