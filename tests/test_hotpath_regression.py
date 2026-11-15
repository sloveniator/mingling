"""
tests/test_hotpath_regression.py -- 热路径性能回归护栏
=======================================================
优化前，每写一条对话/记忆都要把该用户的**全量**数据重新序列化并写回 KV，
写入量随记录数呈 O(N²) 增长。600 条消息实测：

    ConversationInspector.log   18.76s / 240MB 序列化
    MemoryCore.add               1.20s /  48.7MB

改为「写回缓存 + 防抖落盘」后降到 0.35s / 0.14s。

这类优化极易在后续维护中被无意还原——比如有人为了"保证数据不丢"
在 log() 末尾补一句 `await self._save(uid)`，功能测试全绿，性能却退回原点。
本文件用**计数**而非计时设卡（计时在 CI 上不稳定），阈值放得很宽，
只拦截"每次写入都落盘"这一类结构性退化。
"""
import asyncio

import pytest

from conversation_inspector import ConversationInspector
from memory_core import MemoryCore

MSG = "今天面试完了，说实话有点焦虑，我一直很想去这家公司，但又怕自己不够好"


class CountingPlugin:
    """统计 KV 读写次数与序列化字节数。"""

    def __init__(self):
        self.kv = {}
        self.reads = 0
        self.writes = 0
        self.write_bytes = 0

    async def get_kv_data(self, key, default=None):
        self.reads += 1
        return self.kv.get(key, default)

    async def put_kv_data(self, key, value):
        self.writes += 1
        if isinstance(value, str):
            self.write_bytes += len(value)
        self.kv[key] = value


N = 300  # 单用户消息数，足以让 O(N²) 行为显现


class TestConversationHotPath:
    @pytest.fixture()
    def result(self):
        async def _run():
            p = CountingPlugin()
            p.memory = MemoryCore(p)
            insp = ConversationInspector(p)
            for i in range(N):
                await insp.log(uid="u1", content=f"{MSG}#{i}", msg_type="user")
            await insp.flush_all()
            return p
        return asyncio.run(_run())

    def test_writes_are_debounced(self, result):
        """N 次写入不得触发 N 次落盘。"""
        assert result.writes < N / 4, (
            f"{N} 条消息触发了 {result.writes} 次 KV 写入，"
            f"防抖落盘疑似失效（退化为每次写入都落盘）"
        )

    def test_reads_do_not_scale_with_writes(self, result):
        """缓存命中后不应反复回读 KV。"""
        assert result.reads < N / 4, (
            f"{N} 条消息触发了 {result.reads} 次 KV 读取，写回缓存疑似失效"
        )

    def test_serialized_volume_is_not_quadratic(self, result):
        """全量重序列化会让写入字节数按 O(N²) 膨胀。

        单条记录约 200B，线性写入总量约 N*200；平方级则接近 N²*200/2。
        取 8 倍线性量作为分界，足够宽松且能拦住平方级退化。
        """
        linear = N * 200
        assert result.write_bytes < linear * 8, (
            f"写入 {result.write_bytes / 1e6:.1f}MB，"
            f"远超线性预期 {linear / 1e6:.2f}MB，疑似恢复了全量重序列化"
        )

    def test_data_is_actually_persisted(self, result):
        """性能优化不能以丢数据为代价。"""
        async def _verify():
            p2 = CountingPlugin()
            p2.kv = dict(result.kv)
            p2.memory = MemoryCore(p2)
            insp2 = ConversationInspector(p2)
            return await insp2.list_conversations(uid="u1", limit=N + 10)
        data = asyncio.run(_verify())
        assert data["total"] == N, f"落盘后只剩 {data['total']}/{N} 条"


class TestMemoryHotPath:
    @pytest.fixture()
    def result(self):
        async def _run():
            p = CountingPlugin()
            mem = MemoryCore(p)
            for i in range(N):
                await mem.add_episode("u1", f"{MSG}#{i}")
            await mem.flush_all()
            return p
        return asyncio.run(_run())

    def test_writes_are_debounced(self, result):
        assert result.writes < N / 4, (
            f"{N} 次记忆写入触发了 {result.writes} 次 KV 落盘"
        )

    def test_serialized_volume_is_not_quadratic(self, result):
        linear = N * 250
        assert result.write_bytes < linear * 8, (
            f"写入 {result.write_bytes / 1e6:.1f}MB，疑似恢复了全量重序列化"
        )

    def test_memory_cap_enforced(self):
        """单用户记忆条数需有上限，否则长期运行会无限膨胀。"""
        from memory_core import MemoryCore

        async def _run():
            p = CountingPlugin()
            mem = MemoryCore(p)
            for i in range(MemoryCore.MAX_MEMORIES_PER_USER + 60):
                await mem.add_episode("u1", f"记忆条目 {i}")
            await mem.flush_all()
            return await mem.recall_recent("u1", n=10_000)

        items = asyncio.run(_run())
        assert len(items) <= MemoryCore.MAX_MEMORIES_PER_USER


class TestLLMRequestHotPath:
    """``_on_llm_request_body`` 是全插件最高频的路径——每次 LLM 调用都要过。

    它串起「危机识别 → 情绪触发 → 休息闸门 → 记忆/关系/对话写入 → 钩子链」，
    任何人在里面顺手加一句同步全量落盘或阻塞 IO，功能测试都不会红，
    但每条消息的延迟会实打实地涨。这里对**单次调用**的 KV 读写次数设预算。
    """

    # 冷启动（首次调用）：要建索引、初始化关系/档案。实测 5 写 / 9 读。
    BUDGET_WRITES = 12
    BUDGET_READS = 40
    # 稳态（后续每轮均摊）：实测 1 写 / 3 读，各留 2 倍以上余量。
    PER_ROUND_WRITES = 3
    PER_ROUND_READS = 8
    # 均摊成本随轮次的允许放大倍数。稳定实现下 20 轮与 5 轮的均摊应基本相等；
    # 一旦有人在热路径里加"每轮扫一遍全量历史"，均摊会随 n 上升而触发此卡。
    GROWTH_TOLERANCE = 1.5

    @staticmethod
    def _make_event(text=MSG):
        from astrbot.api.event import AstrMessageEvent
        return AstrMessageEvent(
            message_str=text,
            unified_msg_origin="test:friend:u_hot",
            session_id="s_hot",
            msg_type="FriendMessage",
        )

    @staticmethod
    def _make_req():
        from astrbot.api.provider import ProviderRequest
        return ProviderRequest(prompt=MSG, session_id="s_hot")

    @classmethod
    def _run_hotpath(cls, rounds):
        """冷启动一次后再跑 rounds 轮，返回 (首次读写, 稳态每轮均摊读写)。"""
        from tests.test_admin_api import build_star

        async def _run():
            star = build_star()
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

            await star._on_llm_request_body(cls._make_event(), cls._make_req())
            first = dict(counters)

            counters["reads"] = counters["writes"] = 0
            for _ in range(rounds):
                await star._on_llm_request_body(cls._make_event(), cls._make_req())
            per_round = {
                "reads": counters["reads"] / rounds,
                "writes": counters["writes"] / rounds,
            }
            return first, per_round

        return asyncio.run(_run())

    @pytest.fixture(scope="class")
    @classmethod
    def measured(cls):
        """冷启动读写 + 5 轮/20 轮两档均摊读写。

        跑两档是为了把「绝对量超标」和「均摊随轮次增长」分开定位：
        前者说明单轮变重，后者说明有人引入了随历史长度扫描的逻辑。
        """
        first, short = cls._run_hotpath(rounds=5)
        _, long = cls._run_hotpath(rounds=20)
        return {"first": first, "short": short, "long": long}

    def test_single_call_write_budget(self, measured):
        first = measured["first"]
        assert first["writes"] <= self.BUDGET_WRITES, (
            f"单次 LLM 请求触发了 {first['writes']} 次 KV 写入，"
            f"预算 {self.BUDGET_WRITES}。热路径上疑似新增了同步落盘"
            f"（写回缓存被绕过，或有人在钩子里补了 await self._save(...)）"
        )

    def test_single_call_read_budget(self, measured):
        first = measured["first"]
        assert first["reads"] <= self.BUDGET_READS, (
            f"单次 LLM 请求触发了 {first['reads']} 次 KV 读取，"
            f"预算 {self.BUDGET_READS}。疑似有缓存失效或新增了全量扫描"
        )

    def test_steady_state_write_budget(self, measured):
        """稳态下每轮对话的写入次数要有上限。

        关系/档案/活跃索引都应命中内存镜像 + 防抖落盘。退化为
        「每个子系统每轮各落一次盘」时，均摊写入会成倍上涨。
        """
        long = measured["long"]
        assert long["writes"] <= self.PER_ROUND_WRITES, (
            f"稳态下每轮对话均摊 {long['writes']:.1f} 次 KV 写入，"
            f"预算 {self.PER_ROUND_WRITES}。防抖落盘/内存镜像疑似失效"
        )

    def test_steady_state_read_budget(self, measured):
        long = measured["long"]
        assert long["reads"] <= self.PER_ROUND_READS, (
            f"稳态下每轮对话均摊 {long['reads']:.1f} 次 KV 读取，"
            f"预算 {self.PER_ROUND_READS}。缓存疑似失效"
        )

    def test_cost_per_round_does_not_grow(self, measured):
        """均摊成本不得随轮次增长——这是抓 O(N²) 退化的关键一卡。

        绝对预算只能拦住"单轮变重"；若有人写了「每轮遍历该用户全部历史」，
        单轮预算可能仍然过关，但均摊成本会随对话变长持续上升。
        """
        short, long = measured["short"], measured["long"]
        for metric in ("reads", "writes"):
            base = short[metric]
            if base <= 0:
                continue
            ratio = long[metric] / base
            assert ratio <= self.GROWTH_TOLERANCE, (
                f"均摊 KV {metric}：5 轮时 {base:.1f}/轮，20 轮时 "
                f"{long[metric]:.1f}/轮（放大 {ratio:.1f}×）。"
                f"热路径疑似引入了随历史长度增长的扫描"
            )

    def test_hotpath_stays_non_blocking(self):
        """热路径不得在事件循环里跑阻塞调用。

        用一个极短的超时兜住：单次调用（内存 KV）远快于 2s，
        真有人塞了 time.sleep / requests.get 就会超时。
        """
        from tests.test_admin_api import build_star

        async def _run():
            star = build_star()
            await star.initialize()
            await asyncio.wait_for(
                star._on_llm_request_body(self._make_event(), self._make_req()),
                timeout=2.0,
            )

        asyncio.run(_run())


class TestActiveUserIndex:
    """_remember_user 每条消息都会走到，必须零 IO 命中。"""

    def test_repeat_user_causes_no_extra_kv_io(self):
        from tests.test_admin_api import build_star

        async def _run():
            star = build_star()
            await star.initialize()
            store_writes = []

            orig_put = star.put_kv_data

            async def counting_put(key, value):
                store_writes.append(key)
                await orig_put(key, value)

            star.put_kv_data = counting_put

            await star._remember_user("u1")       # 首次：应写一次索引
            first = len(store_writes)
            for _ in range(200):                  # 后续 200 次：应零写入
                await star._remember_user("u1")
            return first, len(store_writes)

        first, total = asyncio.run(_run())
        assert first == 1, f"首次注册应恰好写一次，实际 {first}"
        assert total == first, (
            f"重复用户又触发了 {total - first} 次 KV 写入，"
            f"活跃用户索引的内存镜像疑似失效"
        )
