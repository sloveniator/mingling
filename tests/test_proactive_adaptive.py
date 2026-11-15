"""命灵·主动关怀频率自适应回归测试。

需求：主动发送频率根据与用户的聊天频次动态调整——高频用户更像密友
（间隔短、概率高），低频用户更克制（间隔长、概率低）。档位由近 3 天
用户消息数决定（_activity_profile）；老会话无统计字段维持现状（6h ×1.0）；
proactive_adaptive_enabled=false 整体回退固定 6h。
企微官方 48h 窗口 + 每窗口条数上限不受本机制影响（见 test_proactive_wecom_direct.py）。
"""
import asyncio
from datetime import datetime, timedelta
from unittest import mock

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


def _recent(n, hours_ago=1):
    ts = (datetime.now() - timedelta(hours=hours_ago)).isoformat()
    return [ts] * n


def _run_maybe(pe, meta):
    """跑到发送分支：概率必过、无打字延迟。"""
    with mock.patch("random.random", return_value=0.0), \
         mock.patch("random.uniform", return_value=0.0), \
         mock.patch.object(pe, "_compose", mock.AsyncMock(return_value="在忙吗？")):
        asyncio.run(pe._maybe_message("s", meta, False))


# ---- 分档判定 ----

def test_profile_high_frequency():
    gap, factor = ProactiveEngine._activity_profile({"recent_msgs": _recent(6)})
    assert gap == 3.0 and abs(factor - 1.4) < 1e-9


def test_profile_normal():
    gap, factor = ProactiveEngine._activity_profile({"recent_msgs": _recent(3)})
    assert gap == 6.0 and factor == 1.0


def test_profile_low_frequency():
    gap, factor = ProactiveEngine._activity_profile({"recent_msgs": _recent(1)})
    assert gap == 10.0 and abs(factor - 0.6) < 1e-9


def test_profile_legacy_session_defaults_normal():
    """老会话（无字段/空列表）→ 维持现状，不误判为低频。"""
    assert ProactiveEngine._activity_profile({}) == (6.0, 1.0)
    assert ProactiveEngine._activity_profile({"recent_msgs": []}) == (6.0, 1.0)


def test_profile_counts_only_recent_window():
    """5 天前的 10 条不计入近 3 天 → 低频档。"""
    old = (datetime.now() - timedelta(days=5)).isoformat()
    gap, factor = ProactiveEngine._activity_profile({"recent_msgs": [old] * 10})
    assert gap == 10.0 and abs(factor - 0.6) < 1e-9


# ---- track_session 频次记录 ----

def test_track_session_records_recent_msgs():
    pe = ProactiveEngine(_FakePlugin())
    asyncio.run(pe.track_session("u", "s"))
    sessions = asyncio.run(pe._ensure_sessions())
    assert len(sessions["s"]["recent_msgs"]) == 1


def test_track_session_prunes_old_timestamps():
    pe = ProactiveEngine(_FakePlugin())
    sessions = asyncio.run(pe._ensure_sessions())
    old = (datetime.now() - timedelta(days=20)).isoformat()
    sessions["s"] = {"uid": "u", "kind": "private", "recent_msgs": [old, old]}
    asyncio.run(pe.track_session("u", "s"))
    sessions = asyncio.run(pe._ensure_sessions())
    assert len(sessions["s"]["recent_msgs"]) == 1  # 20 天前的被剪掉


# ---- _maybe_message 动态行为 ----

def test_high_frequency_shortens_gap():
    """高频用户：距上次主动 4h（<旧固定 6h）也应放行发送。"""
    pe = ProactiveEngine(_FakePlugin())
    meta = {
        "uid": "u", "kind": "private",
        "last_msg": (datetime.now() - timedelta(hours=6)).isoformat(),
        "last_proactive": (datetime.now() - timedelta(hours=4)).isoformat(),
        "recent_msgs": _recent(8),
    }
    _run_maybe(pe, meta)
    assert pe.plugin.context.sent is True


def test_low_frequency_lengthens_gap():
    """低频用户：距上次主动 8h（>旧固定 6h）也应被 10h 新冷却拦住。"""
    pe = ProactiveEngine(_FakePlugin())
    meta = {
        "uid": "u", "kind": "private",
        "last_msg": (datetime.now() - timedelta(hours=30)).isoformat(),
        "last_proactive": (datetime.now() - timedelta(hours=8)).isoformat(),
        "recent_msgs": _recent(1),
    }
    _run_maybe(pe, meta)
    assert pe.plugin.context.sent is False


def test_adaptive_disabled_falls_back_to_fixed_gap():
    """开关关闭：高频用户也回到固定 6h → 4h 前主动过则跳过。"""
    plugin = _FakePlugin()
    plugin._config = {"proactive_adaptive_enabled": False}
    pe = ProactiveEngine(plugin)
    meta = {
        "uid": "u", "kind": "private",
        "last_msg": (datetime.now() - timedelta(hours=6)).isoformat(),
        "last_proactive": (datetime.now() - timedelta(hours=4)).isoformat(),
        "recent_msgs": _recent(8),
    }
    _run_maybe(pe, meta)
    assert plugin.context.sent is False
