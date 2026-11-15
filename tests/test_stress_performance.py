"""
tests/test_stress_performance.py -- 综合压力与性能测试
========================================================
对核心模块进行压力测试和性能基准测试：
  1. 高并发下的 KV 存储读写压力
  2. 大量用户数据下的记忆系统性能
  3. 暗盘计算的吞吐量测试
  4. 群黑话学习的批量处理性能
  5. 社交关系图谱的大规模查询性能

运行：python -m pytest tests/test_stress_performance.py -v --tb=short
"""
import asyncio
import json
import random
import time
from typing import List

import pytest

# ============================================================
# 1. KV 存储基类压力测试
# ============================================================

class TestKVStoreStress:
    """KV 存储基类的高并发压力测试。"""

    def _make_fake_plugin(self):
        """构造一个内存版 FakePlugin 用于压力测试。"""
        class FakePlugin:
            def __init__(self):
                self.kv = {}

            async def get_kv_data(self, key, default=None):
                await asyncio.sleep(0.001)  # 模拟 IO 延迟
                return self.kv.get(key, default)

            async def put_kv_data(self, key, value):
                await asyncio.sleep(0.001)
                self.kv[key] = value

        return FakePlugin()

    def test_sequential_write_1000_keys(self):
        """顺序写入 1000 个 KV 键，确保不崩溃。"""
        from kv_store import KVStoreBase
        plugin = self._make_fake_plugin()
        store = KVStoreBase(plugin, "stress_test")

        async def run():
            for i in range(1000):
                key = f"{store.prefix}:key_{i}"
                data = {"index": i, "value": f"data_{i}", "nested": {"a": i * 2}}
                ok = await store._save_json(key, data)
                assert ok

        asyncio.run(run())

    def test_sequential_read_1000_keys(self):
        """顺序读取 1000 个 KV 键。"""
        from kv_store import KVStoreBase
        plugin = self._make_fake_plugin()
        store = KVStoreBase(plugin, "stress_test")

        async def setup():
            for i in range(1000):
                key = f"{store.prefix}:key_{i}"
                await store._save_json(key, {"index": i})

        async def run():
            for i in range(1000):
                key = f"{store.prefix}:key_{i}"
                data = await store._load_json(key)
                assert data is not None
                assert data["index"] == i

        asyncio.run(setup())
        asyncio.run(run())

    def test_concurrent_read_write_50_tasks(self):
        """50 个并发任务同时读写，验证数据一致性。"""
        from kv_store import KVStoreBase
        plugin = self._make_fake_plugin()
        store = KVStoreBase(plugin, "stress_concurrent")

        async def writer(task_id, n_writes):
            for i in range(n_writes):
                key = f"{store.prefix}:writer_{task_id}_key_{i}"
                data = {"task": task_id, "seq": i, "ts": time.time()}
                ok = await store._save_json(key, data)
                assert ok

        async def reader(task_id, n_reads, total_writers, writes_per):
            for _ in range(n_reads):
                wt = random.randint(0, total_writers - 1)
                seq = random.randint(0, writes_per - 1)
                key = f"{store.prefix}:writer_{wt}_key_{seq}"
                data = await store._load_json(key, default=None)
                assert data is None or isinstance(data, dict)

        async def run():
            n_writers = 10
            n_readers = 40
            writes_per = 20
            reads_per = 20

            tasks = []
            for i in range(n_writers):
                tasks.append(asyncio.create_task(writer(i, writes_per)))
            for i in range(n_readers):
                tasks.append(asyncio.create_task(reader(i, reads_per, n_writers, writes_per)))

            await asyncio.gather(*tasks)

        asyncio.run(run())

    def test_large_json_payload(self):
        """大 JSON payload 的写入读取测试。"""
        from kv_store import KVStoreBase
        plugin = self._make_fake_plugin()
        store = KVStoreBase(plugin, "stress_large")

        async def run():
            large_data = {
                "items": [
                    {"id": i, "name": f"item_{i}", "tags": [f"tag_{j}" for j in range(10)]}
                    for i in range(500)
                ]
            }
            key = f"{store.prefix}:large_payload"
            ok = await store._save_json(key, large_data)
            assert ok

            loaded = await store._load_json(key)
            assert loaded is not None
            assert len(loaded["items"]) == 500
            assert loaded["items"][499]["id"] == 499

        asyncio.run(run())


# ============================================================
# 2. 记忆系统性能测试
# ============================================================

class TestMemoryPerformance:
    """记忆系统的大数据量性能测试。"""

    def _make_fake_plugin(self):
        class FakePlugin:
            def __init__(self):
                self.kv = {}

            async def get_kv_data(self, key, default=None):
                return self.kv.get(key, default)

            async def put_kv_data(self, key, value):
                self.kv[key] = value

        return FakePlugin()

    def test_1000_memories_add_and_recall(self):
        """添加 1000 条记忆并测试召回性能。"""
        from memory_core import EMOTIONAL, EPISODIC, SEMANTIC, MemoryCore
        plugin = self._make_fake_plugin()
        mem = MemoryCore(plugin)

        async def run():
            types = [EPISODIC, SEMANTIC, EMOTIONAL]
            for i in range(1000):
                mtype = types[i % 3]
                importance = random.uniform(0.1, 0.9)
                emo = random.uniform(-0.5, 0.5)
                await mem.add(
                    user_id="stress_user",
                    mtype=mtype,
                    content=f"记忆内容 #{i}: 这是一条测试记忆，用于验证大规模记忆系统的性能表现。",
                    source="stress_test",
                    importance=importance,
                    emotional_weight=emo,
                    tags=[f"tag_{i % 20}"],
                )

            important = await mem.recall_important("stress_user", n=20)
            assert len(important) <= 20
            assert len(important) > 0

            recent = await mem.recall_recent("stress_user", n=50)
            assert len(recent) <= 50

            emotional = await mem.recall_emotional("stress_user", n=10)
            assert len(emotional) <= 10

        asyncio.run(run())

    def test_memory_decay_1000_items(self):
        """1000 条记忆的衰减计算性能（注意：系统有 400 条上限保护）。"""
        from memory_core import EPISODIC, MemoryCore
        plugin = self._make_fake_plugin()
        mem = MemoryCore(plugin)

        async def run():
            for i in range(1000):
                await mem.add(
                    user_id="decay_user",
                    mtype=EPISODIC,
                    content=f"衰减测试 {i}",
                    importance=random.uniform(0.2, 0.8),
                )

            # 先确认上限保护生效（最多 400 条）
            all_mems = await mem.recall_recent("decay_user", n=1000)
            n_before = len(all_mems)
            assert n_before <= 400

            start = time.time()
            n_decayed = await mem.decay_all("decay_user", days=30.0)
            elapsed = time.time() - start
            assert n_decayed <= 400
            assert n_decayed > 0
            assert elapsed < 5.0, f"衰减计算太慢: {elapsed:.2f}s for {n_decayed} items"

        asyncio.run(run())

    def test_multi_user_memory_isolation(self):
        """多用户记忆隔离测试。"""
        from memory_core import EPISODIC, MemoryCore
        plugin = self._make_fake_plugin()
        mem = MemoryCore(plugin)

        async def run():
            n_users = 50
            n_per_user = 20

            for uid in range(n_users):
                for j in range(n_per_user):
                    await mem.add(
                        user_id=f"user_{uid}",
                        mtype=EPISODIC,
                        content=f"用户 {uid} 的记忆 {j}",
                    )

            for uid in range(n_users):
                ms = await mem.recall_recent(f"user_{uid}", n=100)
                assert len(ms) == n_per_user, f"用户 {uid} 记忆数不对: {len(ms)}"
                for m in ms:
                    assert f"用户 {uid}" in m.content

        asyncio.run(run())


# ============================================================
# 3. 暗盘计算吞吐量测试
# ============================================================

class TestDarkChannelThroughput:
    """暗盘计算的吞吐量测试。"""

    def test_da_yun_throughput_1000_calls(self):
        """大运计算 1000 次调用的吞吐量。"""
        from pathlib import Path

        import astrology as _ast

        fixtures_path = Path(__file__).parent.parent / "oracle_fixtures.json"
        with open(fixtures_path, encoding="utf-8") as f:
            fxs = json.load(f)

        if not fxs:
            pytest.skip("无 fixture 数据")

        yr = fxs[0]["currentYear"]
        _ast._current_year = lambda: yr

        fx = fxs[0]
        analysis = {
            "bazi": {"birthInfo": fx["birth"], "daYun": fx["daYun_fixture"]},
            "ziwei": fx["ziwei"],
        }

        start = time.time()
        n = 1000
        for _ in range(n):
            result = _ast.current_da_yun_liu_nian(analysis)
            assert isinstance(result, dict)
        elapsed = time.time() - start

        throughput = n / elapsed
        assert throughput > 100, f"大运计算吞吐量过低: {throughput:.0f} ops/s"

    def test_palace_extract_throughput_500_calls(self):
        """宫位提取 500 次调用的吞吐量。"""
        from pathlib import Path

        import astrology as _ast

        fixtures_path = Path(__file__).parent.parent / "oracle_fixtures.json"
        with open(fixtures_path, encoding="utf-8") as f:
            fxs = json.load(f)

        if not fxs:
            pytest.skip("无 fixture 数据")

        analysis = {"ziwei": fxs[0]["ziwei"]}

        start = time.time()
        n = 500
        for _ in range(n):
            result = _ast.extract_key_palaces(analysis)
            assert isinstance(result, dict)
            assert "命宫" in result
        elapsed = time.time() - start

        throughput = n / elapsed
        assert throughput > 200, f"宫位提取吞吐量过低: {throughput:.0f} ops/s"

    def test_insights_extract_throughput_200_calls(self):
        """洞察提取 200 次调用的吞吐量。"""
        from pathlib import Path

        from silent_divination import SilentDiviner

        fixtures_path = Path(__file__).parent.parent / "oracle_fixtures.json"
        with open(fixtures_path, encoding="utf-8") as f:
            fxs = json.load(f)

        if not fxs:
            pytest.skip("无 fixture 数据")

        diviner = SilentDiviner(api_client=None)

        analyses = []
        for fx in fxs:
            analyses.append({
                "bazi": {"birthInfo": fx["birth"], "daYun": fx["daYun_fixture"]},
                "ziwei": fx["ziwei"],
            })

        start = time.time()
        n = 200
        for i in range(n):
            analysis = analyses[i % len(analyses)]
            insights = diviner._extract_insights(analysis, "情绪平稳")
            assert isinstance(insights, str)
            assert len(insights) > 0
        elapsed = time.time() - start

        throughput = n / elapsed
        assert throughput > 50, f"洞察提取吞吐量过低: {throughput:.0f} ops/s"


# ============================================================
# 4. 群黑话批量学习性能
# ============================================================

class TestGroupSlangPerformance:
    """群黑话学习的批量处理性能测试。"""

    def _make_fake_plugin(self):
        class FakePlugin:
            def __init__(self):
                self.kv = {}

            async def get_kv_data(self, key, default=None):
                return self.kv.get(key, default)

            async def put_kv_data(self, key, value):
                self.kv[key] = value

        return FakePlugin()

    def test_batch_learn_500_words(self):
        """批量学习 500 个黑话词的性能。"""
        from group_slang import GroupSlangManager
        plugin = self._make_fake_plugin()
        mgr = GroupSlangManager(plugin)

        words = [
            f"yyds_{i}" for i in range(100)
        ] + [
            f"xswl_{i}" for i in range(100)
        ] + [
            f"awsl_{i}" for i in range(100)
        ] + [
            f"zqsg_{i}" for i in range(100)
        ] + [
            f"bdjw_{i}" for i in range(100)
        ]

        async def run():
            gid = "test_group"
            start = time.time()
            for word in words:
                await mgr.learn(gid, word, context=f"这是{word}的用法示例", confidence=0.5)
            elapsed = time.time() - start

            listed = await mgr.list_slang(gid)
            assert len(listed) == 500

            throughput = 500 / elapsed
            assert throughput > 50, f"黑话学习吞吐量过低: {throughput:.0f} ops/s"

        asyncio.run(run())

    def test_auto_learn_candidates_performance(self):
        """候选黑话自动提取的性能。"""
        from group_slang import GroupSlangManager
        plugin = self._make_fake_plugin()
        mgr = GroupSlangManager(plugin)

        text = (
            "今天看到一个 yyds 的东西，xswl 哈哈哈，awsl 太可爱了，"
            "zqsg 地觉得这个 bdjw 不对，u1s1 确实如此，"
            "「绝绝子」也太好笑了吧，【yyds】永远的神，"
            "还有「yygq」的也太明显了。" * 10
        )

        async def run():
            gid = "test_group_2"
            start = time.time()
            n = 100
            for _ in range(n):
                await mgr.learn_candidates(gid, text)
            elapsed = time.time() - start

            throughput = n / elapsed
            assert throughput > 20, f"候选提取吞吐量过低: {throughput:.0f} ops/s"

        asyncio.run(run())


# ============================================================
# 5. 社交关系图谱性能
# ============================================================

class TestSocialGraphPerformance:
    """社交关系图谱的大规模查询性能。"""

    def _make_fake_plugin(self):
        class FakePlugin:
            def __init__(self):
                self.kv = {}

            async def get_kv_data(self, key, default=None):
                return self.kv.get(key, default)

            async def put_kv_data(self, key, value):
                self.kv[key] = value

        return FakePlugin()

    def test_large_social_graph_co_members(self):
        """大规模社交图的共同成员查询性能。"""
        from social import SocialGraph
        plugin = self._make_fake_plugin()
        graph = SocialGraph(plugin)

        n_groups = 20
        n_users = 100

        async def run():
            for g in range(n_groups):
                gid = f"group_{g}"
                for u in range(n_users):
                    uid = f"user_{u}"
                    if (u + g) % 3 != 0:
                        await graph.touch_group(gid, uid)

            start = time.time()
            n_queries = 50
            for i in range(n_queries):
                uid = f"user_{i % n_users}"
                co = await graph.co_members(uid)
                assert isinstance(co, list)
            elapsed = time.time() - start

            throughput = n_queries / elapsed
            assert throughput > 10, f"共同成员查询吞吐量过低: {throughput:.0f} qps"

        asyncio.run(run())

    def test_shared_groups_performance(self):
        """共同群组查询的性能。"""
        from social import SocialGraph
        plugin = self._make_fake_plugin()
        graph = SocialGraph(plugin)

        async def run():
            for g in range(30):
                gid = f"group_{g}"
                for u in range(50):
                    await graph.touch_group(gid, f"user_{u}")

            start = time.time()
            n_queries = 100
            for i in range(n_queries):
                u1 = f"user_{i % 50}"
                u2 = f"user_{(i + 1) % 50}"
                shared = await graph.shared_groups(u1, u2)
                assert isinstance(shared, list)
            elapsed = time.time() - start

            throughput = n_queries / elapsed
            assert throughput > 50, f"共同群组查询吞吐量过低: {throughput:.0f} qps"

        asyncio.run(run())


# ============================================================
# 6. 对话权重计算性能
# ============================================================

class TestConversationWeightPerformance:
    """对话权重计算的性能测试。"""

    def test_1000_conversations_weight_calc(self):
        """1000 条对话的权重计算性能。"""
        from conversation_inspector import ConversationLog

        contents = [
            "我今天好开心啊，遇到了很多有趣的事情",
            "最近压力好大，感觉快撑不住了",
            "我喜欢你，想和你在一起",
            "今天工作顺利吗？有没有什么想说的",
            "梦到了一些奇怪的事情，醒来还记得",
            "我们一起去看电影吧，怎么样",
            "最近考试压力很大，有点焦虑",
            "生日快乐呀，希望你今天开心",
            "分手了，心里好难受",
            "想你了，最近还好吗",
        ]

        start = time.time()
        n = 1000
        for i in range(n):
            log = ConversationLog(
                uid=f"user_{i % 10}",
                content=contents[i % len(contents)],
                is_group=(i % 3 == 0),
                msg_type="user" if i % 2 == 0 else "bot",
            )
            weight = log.calculate_weight()
            assert 0.0 <= weight <= 1.0
        elapsed = time.time() - start

        throughput = n / elapsed
        assert throughput > 500, f"权重计算吞吐量过低: {throughput:.0f} ops/s"
