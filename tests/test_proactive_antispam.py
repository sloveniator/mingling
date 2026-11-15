import asyncio
from datetime import datetime, timedelta

from proactive import ProactiveEngine


class _FakeRel:
    score = 100
    mode = "warm"


class _FakePersona:
    name = "灵犀"
    mood = 60
    study_topic = None

    def pending_surface_thought(self):
        return None

    def pending_curiosity(self):
        return None

    def pending_melon(self):
        return None


class _FakeContext:
    def __init__(self):
        self.sent = False

    async def send_message(self, sid, chain):
        self.sent = True


class _FakePlugin:
    def __init__(self):
        self._config = {}
        self.context = _FakeContext()
        self.persona = _FakePersona()
        self.dream = None
        self.memory = None
        self.kv = {}

    async def _get_relationship(self, uid):
        return _FakeRel()

    async def _llm_text(self, prompt, task_type="general"):
        return ""

    async def get_kv_data(self, k, d=None):
        return self.kv.get(k, d)

    async def put_kv_data(self, k, v):
        self.kv[k] = v


def test_grace_skip_when_user_recently_active():
    pe = ProactiveEngine(_FakePlugin())
    meta = {"uid": "u", "kind": "private", "last_msg": datetime.now().isoformat()}
    asyncio.run(pe._maybe_message("s", meta, False))
    assert pe.plugin.context.sent is False


def test_cap_skip_when_unanswered_exceeded():
    pe = ProactiveEngine(_FakePlugin())
    meta = {
        "uid": "u", "kind": "private", "unanswered": 5,
        "last_msg": (datetime.now() - timedelta(days=1)).isoformat(),
    }
    asyncio.run(pe._maybe_message("s", meta, False))
    assert pe.plugin.context.sent is False


def test_track_session_resets_unanswered():
    pe = ProactiveEngine(_FakePlugin())
    asyncio.run(pe.track_session("u", "s", kind="private"))
    sessions = asyncio.run(pe._ensure_sessions())
    assert sessions["s"]["unanswered"] == 0


def test_skip_wecom_platform():
    """企微会话（platform='wecom'）且无 open_kfid → 主动消息降级跳过，不 send。

    v4.8.118 起企微解锁直连：带 open_kfid 且窗口内会走 kf_message 直连
    （见 test_proactive_wecom_direct.py）；缺 open_kfid（老 KV/未补录）的
    会话仍安全跳过，避免直连报错与过期打扰。
    """
    pe = ProactiveEngine(_FakePlugin())
    meta = {
        "uid": "u", "kind": "private", "platform": "wecom",
        "last_msg": (datetime.now() - timedelta(days=1)).isoformat(),
    }
    asyncio.run(pe._maybe_message("s", meta, False))
    assert pe.plugin.context.sent is False


def test_skip_weixin_variant():
    """平台名含 wechat / weixin 同样视为企微；无 open_kfid 时跳过。"""
    pe = ProactiveEngine(_FakePlugin())
    for plat in ("wechat", "Weixin", "some-wecom-adapter"):
        meta = {
            "uid": "u", "kind": "private", "platform": plat,
            "last_msg": (datetime.now() - timedelta(days=1)).isoformat(),
        }
        asyncio.run(pe._maybe_message("s", meta, False))
        assert pe.plugin.context.sent is False


def test_track_session_stores_platform():
    """track_session 落库 platform 字段，供后续发送前特判。"""
    pe = ProactiveEngine(_FakePlugin())
    asyncio.run(pe.track_session("u", "s", kind="private", platform="wecom"))
    sessions = asyncio.run(pe._ensure_sessions())
    assert sessions["s"]["platform"] == "wecom"
    # 未传 platform 时不写该字段（向后兼容旧 KV）
    pe2 = ProactiveEngine(_FakePlugin())
    asyncio.run(pe2.track_session("u", "s2", kind="private"))
    sessions2 = asyncio.run(pe2._ensure_sessions())
    assert "platform" not in sessions2["s2"]
