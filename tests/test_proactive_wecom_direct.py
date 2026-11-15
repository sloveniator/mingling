"""命灵·企微客服主动推送直连回归测试（v4.8.118 解锁语义）。

v4.8.117 曾整段跳过企微（context.send_message 走 send_by_session 失败）。
v4.8.118 起改用官方 kf/send_msg 直连（client.kf_message.send_text）主动下发，
但必须自守官方限制：客户来消息后 48h 窗口内、每窗口条数上限（默认 2 条）。
本文件锁定的正是这套解锁 + 守卫语义：

  1. 企微会话带 open_kfid + 窗口内 + 未达上限 → 直连发送（不碰 context.send_message）
  2. 企微会话缺 open_kfid（老 KV/未补录）→ 安全降级跳过
  3. 越过 48h 窗口（last_msg 太久）→ 跳过
  4. 已达窗口条数上限（kf_sent >= max）→ 跳过
  5. 找不到企微平台实例/无 kf_message 客户端 → 不崩溃、不误发
 6. quiet hours 时段（quiet=True）→ 一律不推（含企微直连）
"""
import asyncio
from datetime import datetime, timedelta
from unittest import mock

from proactive import ProactiveEngine

_UID = "wmTEST123"  # 企微外部联系人 uid 以 wm/wo 开头
_SID = "s"


class _FakeRel:
    score = 100
    mode = "warm"


class _FakePersona:
    name = "灵犀"
    mood = 60
    study_topic = None

    def pending_surface_thought(self):
        # 命中"自身念头"分支，保证 _compose 返回非空文本且不触碰 dream/memory
        return "想你了，今天过得怎么样？"

    def pending_curiosity(self):
        return None

    def pending_melon(self):
        return None


class _FakeKfMessage:
    def __init__(self):
        self.calls = []

    def send_text(self, uid, open_kfid, text):
        self.calls.append((uid, open_kfid, text))
        return {"errcode": 0}


class _FakeClient:
    def __init__(self):
        self.kf_message = _FakeKfMessage()


class _FakePlatform:
    name = "爱丽丝"

    def __init__(self):
        self.client = _FakeClient()

    def get_client(self):
        return self.client


class _FakeContext:
    def __init__(self, platforms=None):
        self.sent = False
        self.platform_insts = platforms or []

    async def send_message(self, sid, chain):
        self.sent = True

    def get_platform_inst(self, name):
        for p in self.platform_insts:
            if getattr(p, "name", "") == name:
                return p
        return None


class _FakePlugin:
    def __init__(self, ctx=None):
        self._config = {}
        self.context = ctx or _FakeContext()
        self.persona = _FakePersona()
        self.dream = None
        self.memory = None
        self.growth = None
        self.kv = {}

    async def _get_relationship(self, uid):
        return _FakeRel()

    async def _llm_text(self, prompt, task_type="general"):
        return ""

    async def get_kv_data(self, k, d=None):
        return self.kv.get(k, d)

    async def put_kv_data(self, k, v):
        self.kv[k] = v


def _engine(platforms=None, open_kfid="wkTEST", last_msg_hours=1):
    """构造已 track 的企微会话引擎，返回 (pe, ctx, meta)。

    track_session 把 meta 写入 _sessions_cache 并 flush 到 fake KV；
    随后把 last_msg 拨回窗口内（1h 前）、last_proactive 拨到 7h 前
    （跳过 6h 冷却），使 _maybe_message 能走到发送路径。
    """
    ctx = _FakeContext(platforms=platforms)
    plugin = _FakePlugin(ctx)
    pe = ProactiveEngine(plugin)
    asyncio.run(pe.track_session(
        _UID, _SID, kind="private", platform="爱丽丝", open_kfid=open_kfid))
    meta = asyncio.run(pe._ensure_sessions())[_SID]
    meta["last_msg"] = (datetime.now() - timedelta(hours=last_msg_hours)).isoformat()
    meta["last_proactive"] = (datetime.now() - timedelta(hours=7)).isoformat()
    # 频率自适应：track_session 只记 1 条会被判低频档（10h 冷却），拦住本文件要验证的
    # 企微直连语义；这里模拟「常规活跃」用户（近 3 天 3 条 → 6h 冷却，7h 前可放行）。
    meta["recent_msgs"] = [datetime.now().isoformat()] * 3
    return pe, ctx, meta


def _run_maybe(pe, meta, quiet=False):
    # random.random → 0.0 保证命中概率分支；random.uniform → 0.0 免去打字延迟
    with mock.patch("proactive.random.random", return_value=0.0), \
            mock.patch("proactive.random.uniform", return_value=0.0):
        asyncio.run(pe._maybe_message(_SID, meta, quiet))


def test_wecom_with_open_kfid_sends_direct():
    """窗口内 + 有 open_kfid + 未达上限 → 走 kf_message 直连，计数 +1。"""
    platform = _FakePlatform()
    pe, ctx, meta = _engine(platforms=[platform])
    kf = platform.client.kf_message
    _run_maybe(pe, meta)
    assert len(kf.calls) == 1, "应直连发送一次"
    assert kf.calls[0][0] == _UID
    assert kf.calls[0][1] == "wkTEST"
    assert kf.calls[0][2]  # 文本非空
    assert ctx.sent is False, "企微不应走 context.send_message"
    cache = asyncio.run(pe._ensure_sessions())
    assert cache[_SID]["kf_sent"] == 1


def test_wecom_without_open_kfid_skips():
    """企微会话缺 open_kfid（老 KV / 尚未补录）→ 安全降级跳过。"""
    platform = _FakePlatform()
    pe, ctx, meta = _engine(platforms=[platform], open_kfid="")
    kf = platform.client.kf_message
    _run_maybe(pe, meta)
    assert kf.calls == []
    assert ctx.sent is False


def test_wecom_expired_window_skips():
    """越过 48h 窗口（last_msg 3 天前）→ 跳过，不直连不误发。"""
    platform = _FakePlatform()
    pe, ctx, meta = _engine(platforms=[platform], last_msg_hours=24 * 3)
    kf = platform.client.kf_message
    _run_maybe(pe, meta)
    assert kf.calls == []
    assert ctx.sent is False


def test_wecom_quota_reached_skips():
    """窗口内但 kf_sent 已达上限（默认 2）→ 跳过。"""
    platform = _FakePlatform()
    pe, ctx, meta = _engine(platforms=[platform])
    meta["kf_win_start"] = (datetime.now() - timedelta(hours=1)).isoformat()
    meta["kf_sent"] = 2
    kf = platform.client.kf_message
    _run_maybe(pe, meta)
    assert kf.calls == []
    assert ctx.sent is False


def test_wecom_platform_missing_no_crash():
    """找不到企微平台实例 → 不崩溃、不误发（context.send_message 也不碰）。"""
    pe, ctx, meta = _engine(platforms=[], open_kfid="wkTEST")
    _run_maybe(pe, meta)
    assert ctx.sent is False


def test_wecom_send_success_records_last_proactive():
    """直连成功后落库 last_proactive 与 unanswered 递增（与普通渠道一致）。"""
    platform = _FakePlatform()
    pe, ctx, meta = _engine(platforms=[platform])
    _run_maybe(pe, meta)
    cache = asyncio.run(pe._ensure_sessions())
    assert cache[_SID].get("last_proactive")
    assert cache[_SID].get("unanswered", 0) == 1


def test_wecom_quiet_hours_skips():
    """quiet=True（quiet hours）→ 不直连不计数（收紧项：夜间不打扰）。"""
    platform = _FakePlatform()
    pe, ctx, meta = _engine(platforms=[platform])
    kf = platform.client.kf_message
    _run_maybe(pe, meta, quiet=True)
    assert kf.calls == [], "quiet hours 不应直连发送"
    assert ctx.sent is False, "quiet hours 也不应走普通通道"
    cache = asyncio.run(pe._ensure_sessions())
    assert not cache[_SID].get("kf_sent"), "quiet hours 不应累计 kf_sent"
