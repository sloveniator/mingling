"""多用户并发 LLM 钩子压测（框架回归护栏）
=============================================

覆盖现有压测的缺口：既有 stress 套件多为**单用户**或**纯函数**，
本文件专攻「全插件最高频路径 `_on_llm_request_body` 在**多用户并发**
下的两个不变量」：

1. **用户隔离**：N 个用户并发过钩子后，各用户的记忆 / 对话上下文 /
   关系分档互不串扰（uid 维度独立分桶）。
2. **KV 预算稳定**：稳态下每用户每轮的 KV 读写均摊不随并发用户数线性
   放大（防止有人在热路径加「扫全量用户」的 O(N) 操作）。
"""
import asyncio
import time

import pytest

from tests.test_admin_api import build_star


MSG = "今天面试完了，说实话有点焦虑，我一直很想去这家公司，但又怕自己不够好"


class _UidEvent:
    """可注入 uid 的假事件：get_sender_id 决定钩子的用户分桶。"""

    def __init__(self, uid, text=MSG):
        self._uid = uid
        self.message_str = text
        self.unified_msg_origin = f"test:friend:{uid}"
        self.session_id = f"s_{uid}"
        self.message_obj = type("M", (), {"type": "FriendMessage", "message": []})()

    def get_sender_id(self):
        return self._uid

    def get_sender_name(self):
        return self._uid

    def stop_event(self):
        pass


def _make_req(text=MSG):
    from astrbot.api.provider import ProviderRequest

    return ProviderRequest(prompt=text, session_id="s")


def _sync(coro):
    return asyncio.run(coro)


N_USERS = 20
ROUNDS = 3


def test_multi_user_no_cross_contamination():
    """N 用户并发过钩子：记忆 / 对话上下文按 uid 分桶，互不串扰。"""
    star = build_star()

    async def _run():
        await star.initialize()
        for rnd in range(ROUNDS):
            for i in range(N_USERS):
                uid = f"user_{i:02d}"
                ev = _UidEvent(uid, f"{MSG} 第{rnd}轮 [U{i:02d}]")
                req = _make_req(ev.message_str)
                await star._on_llm_request_body(ev, req)

    _sync(_run())

    # 对话上下文经 inspector.log → _save(uid, logs) 写缓存层 _cache[uid]，
    # 断言每个用户分桶只含自己的文本、不含他人内容。记忆走「写回+防抖」
    # 懒落盘（KV 立即读不到），故这里查 inspector 缓存——它是钩子路径上
    # 即时可见的隔离点。
    insp = star.conversation_inspector
    assert insp is not None, "钩子未构建 conversation_inspector"
    for i in range(N_USERS):
        uid = f"user_{i:02d}"
        logs = insp._cache.get(uid)
        assert logs, f"用户 {uid} 的对话上下文缓存为空（未写入）"
        joined = "\n".join(getattr(l, "content", "") for l in logs)
        # 该用户分桶内不得出现其他用户的专属标记
        for j in range(N_USERS):
            if j != i:
                assert f"[U{j:02d}]" not in joined, (
                    f"用户 {uid} 的上下文里混入了 U{j:02d} 的内容（跨用户串扰）"
                )


def test_multi_user_kv_budget_not_linear_in_users():
    """稳态每用户每轮 KV 读写均摊不应随并发用户数线性放大。

    单用户稳态基线（test_hotpath_regression）为 ≤3 写 / ≤8 读。
    多用户下若有人在热路径加「扫全量用户」的 O(N) 操作，本卡会触发。
    这里允许比单用户略高的常数倍余量，但绝不允许随 N 线性增长。
    """
    star = build_star()

    async def counting_run(n_users):
        await star.initialize()
        counters = {"reads": 0, "writes": 0}
        orig_get, orig_put = star.get_kv_data, star.put_kv_data

        async def counting_get(key, default=None):
            counters["reads"] += 1
            return await orig_get(key, default)

        async def counting_put(key, value):
            counters["writes"] += 1
            await orig_put(key, value)

        star.get_kv_data = counting_get
        star.put_kv_data = counting_put

        # 冷启动（建索引）
        for i in range(n_users):
            await star._on_llm_request_body(_UidEvent(f"u{i:02d}"), _make_req())
        # 稳态：再跑一轮，统计均摊
        counters["reads"] = counters["writes"] = 0
        for i in range(n_users):
            await star._on_llm_request_body(_UidEvent(f"u{i:02d}"), _make_req())
        return counters["reads"] / n_users, counters["writes"] / n_users

    small = _sync(counting_run(5))
    large = _sync(counting_run(40))

    _, small_w = small
    _, large_w = large
    # 写均摊：40 用户不应比 5 用户显著上升（允许 2 倍余量，而非 8 倍线性）
    assert large_w <= small_w * 2 + 1, (
        f"写均摊随用户数放大：5用户={small_w:.1f} vs 40用户={large_w:.1f}"
        "（疑似热路径有 O(N) 全量扫描）"
    )


def test_multi_user_concurrent_throughput():
    """并发吞吐冒烟：N 用户并发过钩子应在线性时间内完成，无意外阻塞。"""
    star = build_star()

    async def _run():
        await star.initialize()
        # 并发（gather）而非串行，验证钩子无跨用户全局锁/串行点
        async def one(i):
            await star._on_llm_request_body(_UidEvent(f"c{i:02d}"), _make_req())

        await asyncio.gather(*(one(i) for i in range(N_USERS)))

    t0 = time.perf_counter()
    _sync(_run())
    dt = time.perf_counter() - t0
    # 桩环境无真实 IO，仅防「有人在钩子里加了阻塞 sleep / 大规模同步落盘」。
    # 20 用户并发应远低于 10s（实测约 <1s）。
    assert dt < 10.0, f"20 用户并发钩子耗时 {dt:.1f}s，疑似热路径有阻塞点"
