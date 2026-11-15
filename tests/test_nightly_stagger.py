"""夜任务 growth/dream 用户间串行延迟（错峰）回归测试。

背景
----
夜间批量任务 ``_nightly_dreams``（dream）与 ``_reflect_all``（growth）会遍历全部
活跃用户逐一调用 LLM。过去无间隔会导致瞬时并发打满 LLM 配额（如 Gemini 429/
rpm exhausted）。v4.8.85 起在每两个用户之间插入 4-5s 随机串行延迟错峰。

本测试锁死两条不变量：
  1. 每个活跃用户都被调用对应引擎（不漏用户）；
  2. asyncio.sleep 恰好被调用 ``用户数-1`` 次（最后一个用户后不 sleep）；
  3. 每次 sleep 落在区间 [4.0, 5.0]。
"""
import asyncio

import pytest
from astrbot_plugin_mingbu.main import MingbuPlugin


def _make_star(uids, call_log):
    """用 __new__ 绕过 __init__，注入最小可用依赖。"""
    star = MingbuPlugin.__new__(MingbuPlugin)

    async def fake_uids():
        return list(uids)

    star._active_uids = fake_uids

    class _FakeDream:
        async def nightly_consolidate(self, uid):
            call_log["dream"].append(uid)

    class _FakeGrowth:
        async def reflect(self, uid):
            call_log["growth"].append(uid)

    star.dream = _FakeDream()
    star.growth = _FakeGrowth()
    return star


@pytest.fixture
def sleep_log(monkeypatch):
    """替换 main 模块的 asyncio.sleep，记录每次延迟时长。"""
    calls = []

    async def fake_sleep(d):
        calls.append(d)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    return calls


def test_nightly_dreams_stagger(sleep_log):
    """dream 夜梦：3 用户 → 调用 3 次、sleep 2 次、区间正确。"""
    call_log = {"dream": []}
    star = _make_star(["u1", "u2", "u3"], call_log)

    asyncio.run(star._nightly_dreams())

    assert call_log["dream"] == ["u1", "u2", "u3"]
    assert len(sleep_log) == 2
    assert all(4.0 <= d <= 5.0 for d in sleep_log)


def test_reflect_all_stagger(sleep_log):
    """growth 反思：3 用户 → 调用 3 次、sleep 2 次、区间正确。"""
    call_log = {"growth": []}
    star = _make_star(["u1", "u2", "u3"], call_log)

    asyncio.run(star._reflect_all())

    assert call_log["growth"] == ["u1", "u2", "u3"]
    assert len(sleep_log) == 2
    assert all(4.0 <= d <= 5.0 for d in sleep_log)


def test_nightly_single_user_no_sleep(sleep_log):
    """单用户不应有任何 sleep（无需错峰）。"""
    call_log = {"dream": []}
    star = _make_star(["only"], call_log)

    asyncio.run(star._nightly_dreams())

    assert call_log["dream"] == ["only"]
    assert sleep_log == []
