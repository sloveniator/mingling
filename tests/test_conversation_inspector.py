"""
tests/test_conversation_inspector.py -- 对话检视回归测试
=========================================================

覆盖本轮修复的确定性缺陷：
  1. list_conversations 空循环（永远返回 0 条），以及被忽略的筛选参数
  2. log_id -> uid 还原对含下划线 uid（wxid_xxx / tg_123）的截断
  3. ConversationLog.id 的哈希在进程重启后不稳定（hash() 随机化）
  4. 写回缓存 + 防抖落盘的正确性（读命中零 IO / flush 后可见 / 上限裁剪）
  5. extract_high_weight_to_memory 的 N+1 重复加载
"""
import asyncio
import json
import subprocess
import sys

import pytest

from conversation_inspector import (
    HIGH_WEIGHT_THRESHOLD,
    MAX_LOGS_PER_USER,
    ConversationInspector,
    ConversationLog,
    uid_from_log_id,
)


class FakePlugin:
    """内存版 KV 插件桩，同时统计读写次数以验证 IO 节流。"""

    def __init__(self):
        self.kv = {}
        self.reads = 0
        self.writes = 0

    async def get_kv_data(self, key, default=None):
        self.reads += 1
        return self.kv.get(key, default)

    async def put_kv_data(self, key, value):
        self.writes += 1
        self.kv[key] = value


def _mk(plugin=None):
    plugin = plugin or FakePlugin()
    return ConversationInspector(plugin), plugin


# ============================================================
# 1. uid 还原
# ============================================================

class TestUidFromLogId:
    def test_plain_uid(self):
        assert uid_from_log_id("12345_20260809225500_a1b2c3") == "12345"

    @pytest.mark.parametrize("uid", ["wxid_abc123", "tg_998877", "a_b_c_d"])
    def test_uid_with_underscores(self, uid):
        """含下划线的 uid 不能被截断——否则操作会作用到别人头上。"""
        log_id = f"{uid}_20260809225500_a1b2c3"
        assert uid_from_log_id(log_id) == uid

    def test_malformed_returns_empty(self):
        assert uid_from_log_id("") == ""
        assert uid_from_log_id("noseparator") == ""
        assert uid_from_log_id("only_two") == ""


# ============================================================
# 2. id 稳定性
# ============================================================

class TestStableId:
    def test_hash_suffix_stable_within_process(self):
        a = ConversationLog("u1", "今天很开心")
        b = ConversationLog("u1", "今天很开心")
        assert a.id.rsplit("_", 1)[-1] == b.id.rsplit("_", 1)[-1]

    def test_hash_suffix_stable_across_processes(self):
        """关键回归：内置 hash() 受 PYTHONHASHSEED 影响，重启后 id 会变。"""
        code = (
            "import zlib;"
            "print(f\"{zlib.crc32('今天很开心'.encode('utf-8')) & 0xFFFFFF:06x}\")"
        )
        outs = set()
        for seed in ("0", "1", "random"):
            r = subprocess.run(
                [sys.executable, "-c", code],
                capture_output=True, text=True,
                env={"PYTHONHASHSEED": seed, "PYTHONIOENCODING": "utf-8",
                     "SYSTEMROOT": "C:\\Windows", "PATH": ""},
            )
            outs.add(r.stdout.strip())
        assert len(outs) == 1, f"id 后缀在不同 hash seed 下不一致: {outs}"
        assert ConversationLog("u", "今天很开心").id.rsplit("_", 1)[-1] in outs


# ============================================================
# 3. list_conversations
# ============================================================

class TestListConversations:
    def test_returns_logged_items(self):
        """回归：原实现循环体丢弃结果，total 恒为 0。"""
        insp, _ = _mk()

        async def run():
            await insp.log("u1", "我今天很难过，压力好大")
            await insp.log("u1", "吃了个饭")
            return await insp.list_conversations(uid="u1")

        res = asyncio.run(run())
        assert res["total"] == 2
        assert len(res["items"]) == 2

    def test_search_filter(self):
        insp, _ = _mk()

        async def run():
            await insp.log("u1", "我想去北京看看")
            await insp.log("u1", "上海的天气不错")
            return await insp.list_conversations(uid="u1", search="北京")

        res = asyncio.run(run())
        assert res["total"] == 1
        assert "北京" in res["items"][0]["content"]

    def test_msg_type_and_learned_filter(self):
        insp, _ = _mk()

        async def run():
            await insp.log("u1", "用户说的话")
            await insp.log_bot_reply("u1", "机器人的回复内容够长了吧")
            only_bot = await insp.list_conversations(uid="u1", msg_type="bot")
            only_learned = await insp.list_conversations(uid="u1", learned_only=True)
            return only_bot, only_learned

        only_bot, only_learned = asyncio.run(run())
        assert only_bot["total"] == 1
        assert only_bot["items"][0]["msg_type"] == "bot"
        assert only_learned["total"] == 0

    def test_pagination_and_desc_order(self):
        insp, _ = _mk()

        async def run():
            for i in range(5):
                await insp.log("u1", f"第 {i} 条消息内容")
            page1 = await insp.list_conversations(uid="u1", limit=2, offset=0)
            page2 = await insp.list_conversations(uid="u1", limit=2, offset=2)
            return page1, page2

        p1, p2 = asyncio.run(run())
        assert p1["total"] == 5 and len(p1["items"]) == 2
        assert len(p2["items"]) == 2
        assert p1["items"][0]["timestamp"] >= p1["items"][-1]["timestamp"]
        assert {x["id"] for x in p1["items"]} & {x["id"] for x in p2["items"]} == set()


# ============================================================
# 4. 缓存 / 防抖落盘
# ============================================================

class TestCacheAndFlush:
    def test_repeated_log_does_not_write_every_time(self):
        """回归：原实现每条消息全量序列化落盘，是最重的热点。"""
        insp, plugin = _mk()

        async def run():
            for i in range(50):
                await insp.log("u1", f"消息 {i}")

        asyncio.run(run())
        # 防抖窗口内不应产生 50 次写
        assert plugin.writes < 50, f"写次数未被节流: {plugin.writes}"

    def test_read_hits_cache(self):
        insp, plugin = _mk()

        async def run():
            await insp.log("u1", "第一条")
            before = plugin.reads
            for _ in range(20):
                await insp._load("u1")
            return plugin.reads - before

        assert asyncio.run(run()) == 0

    def test_flush_all_persists(self):
        insp, plugin = _mk()

        async def run():
            await insp.log("u1", "需要落盘的内容")
            await insp.flush_all()
            return plugin.kv.get(insp._kv_key("u1"))

        raw = asyncio.run(run())
        assert raw, "flush_all 后 KV 中应存在数据"
        assert json.loads(raw)[0]["content"] == "需要落盘的内容"

    def test_flush_all_survives_reload(self):
        """落盘后新实例应能读回（模拟插件重载）。"""
        plugin = FakePlugin()
        insp1 = ConversationInspector(plugin)

        async def run():
            await insp1.log("wxid_abc", "跨重载要保住的话")
            await insp1.flush_all()
            insp2 = ConversationInspector(plugin)
            return await insp2.list_conversations(uid="wxid_abc")

        res = asyncio.run(run())
        assert res["total"] == 1
        assert res["items"][0]["content"] == "跨重载要保住的话"

    def test_flush_trims_to_max(self):
        insp, plugin = _mk()

        async def run():
            logs = [ConversationLog("u1", f"c{i}") for i in range(MAX_LOGS_PER_USER + 30)]
            await insp._save("u1", logs)
            await insp.flush_all()
            return json.loads(plugin.kv[insp._kv_key("u1")])

        data = asyncio.run(run())
        assert len(data) == MAX_LOGS_PER_USER
        # 裁剪保留最新的
        assert data[-1]["content"] == f"c{MAX_LOGS_PER_USER + 29}"

    def test_debounced_flush_eventually_writes(self):
        insp, plugin = _mk()
        insp.FLUSH_DEBOUNCE_SEC = 0.05

        async def run():
            await insp.log("u1", "等防抖自己落盘")
            await asyncio.sleep(0.2)
            return plugin.kv.get(insp._kv_key("u1"))

        assert asyncio.run(run())


# ============================================================
# 5. 按 log_id 定位的写操作
# ============================================================

class TestLogIdScopedOps:
    def _seed(self, insp, uid, content):
        async def run():
            await insp.log(uid, content)
            logs = await insp._load(uid)
            return logs[0].id

        return asyncio.run(run())

    def test_learn_and_detail_with_underscore_uid(self):
        """回归：uid 含下划线时原实现定位失败/串用户。"""
        insp, _ = _mk()
        log_id = self._seed(insp, "wxid_abc123", "一条需要标注的重要对话")

        async def run():
            ok = await insp.learn_from_conversation(log_id, notes="备注", importance=0.8)
            detail = await insp.get_conversation_detail(log_id)
            return ok, detail

        ok, detail = asyncio.run(run())
        assert ok is True
        assert detail is not None
        assert detail["learned"] is True
        assert detail["notes"] == "备注"

    def test_unlearn_and_delete(self):
        insp, _ = _mk()
        log_id = self._seed(insp, "tg_9988", "另一条对话内容")

        async def run():
            await insp.learn_from_conversation(log_id)
            un = await insp.unlearn(log_id)
            deleted = await insp.delete_conversation(log_id)
            left = await insp.list_conversations(uid="tg_9988")
            return un, deleted, left["total"]

        un, deleted, left = asyncio.run(run())
        assert un is True and deleted is True and left == 0

    def test_ops_on_unknown_id_are_safe(self):
        insp, _ = _mk()

        async def run():
            return (
                await insp.get_conversation_detail("garbage"),
                await insp.learn_from_conversation("garbage"),
                await insp.unlearn("garbage"),
                await insp.delete_conversation("garbage"),
                await insp.mark_extracted("garbage"),
            )

        detail, learn, un, dele, mark = asyncio.run(run())
        assert detail is None
        assert learn is False and un is False and dele is False and mark is False


# ============================================================
# 6. 权重索引与记忆抽取
# ============================================================

class TestWeightAndExtraction:
    def test_high_weight_enters_index(self):
        insp, _ = _mk()

        async def run():
            await insp.log("u1", "我一直很害怕失去你，从小就没人知道这个秘密")
            return await insp.get_weighted_conversations("u1")

        items = asyncio.run(run())
        assert items, "高权重对话应进入权重索引"
        assert items[0]["weight"] >= HIGH_WEIGHT_THRESHOLD

    def test_extract_loads_logs_once(self):
        """回归：原实现在循环内反复 _load，构成 N+1。"""
        insp, plugin = _mk()

        class FakeMemory:
            def __init__(self):
                self.added = []

            async def add(self, **kw):
                self.added.append(kw)

        mem = FakeMemory()
        plugin.memory = mem

        async def run():
            for i in range(4):
                await insp.log("u1", f"我很难过很孤独很焦虑，秘密{i}，从小就这样")
            # 清掉自动抽取留下的标记，制造多条 pending
            for entry in await insp._load_weight_index("u1"):
                entry["extracted_to_memory"] = False
            insp._memory_extracted_ids.clear()
            await insp.flush_all()          # 先落盘
            insp._cache.clear()             # 再清缓存，强制下次 _load 走 KV
            before = plugin.reads
            n = await insp.extract_high_weight_to_memory("u1", top_n=4)
            return n, plugin.reads - before

        n, reads = asyncio.run(run())
        assert n >= 2, "应抽取到多条 pending"
        assert reads <= 2, f"日志应只加载一次，实际读了 {reads} 次"

    def test_reweight_all_rebuilds_index(self):
        insp, _ = _mk()

        async def run():
            await insp.log("u1", "我好开心啊，今天和你在一起特别幸福")
            await insp.log("u1", "嗯")
            count = await insp.reweight_all("u1")
            return count, await insp.get_weighted_conversations("u1")

        count, items = asyncio.run(run())
        assert count == 2
        assert all(x["weight"] >= HIGH_WEIGHT_THRESHOLD for x in items)


# ============================================================
# 7. 统计 / 摘要
# ============================================================

class TestStatsAndSummary:
    def test_summary_counts(self):
        insp, _ = _mk()

        async def run():
            await insp.log("u1", "用户消息一")
            await insp.log("u1", "用户消息二")
            await insp.log_bot_reply("u1", "机器人回复一条足够长的内容")
            return await insp.get_user_conversation_summary("u1")

        s = asyncio.run(run())
        assert s["total"] == 3
        assert s["user_msgs"] == 2
        assert s["bot_msgs"] == 1

    def test_summary_empty_user(self):
        insp, _ = _mk()
        s = asyncio.run(insp.get_user_conversation_summary("nobody"))
        assert s["total"] == 0

    def test_get_stats(self):
        insp, plugin = _mk()
        from core import KV_USERS
        plugin.kv[KV_USERS] = json.dumps(["u1", "u2"])

        async def run():
            await insp.log("u1", "一条内容")
            await insp.log("u2", "另一条内容")
            return await insp.get_stats()

        st = asyncio.run(run())
        assert st["total_logs"] == 2
        assert len(st["user_stats"]) == 2


# ============================================================
# 8. 容错
# ============================================================

class TestResilience:
    def test_corrupted_kv_treated_as_empty(self):
        insp, plugin = _mk()
        plugin.kv[insp._kv_key("u1")] = "{not json"
        res = asyncio.run(insp.list_conversations(uid="u1"))
        assert res["total"] == 0

    def test_empty_content_ignored(self):
        insp, _ = _mk()

        async def run():
            await insp.log("u1", "")
            await insp.log("u1", "   ")
            return await insp.list_conversations(uid="u1")

        # 空串直接返回；全空白 strip 后长度为 0 也应被忽略
        assert asyncio.run(run())["total"] == 0

    def test_flush_without_cache_does_not_wipe(self):
        """缓存被清但脏标记仍在时，落盘不得用空列表覆盖已有数据。"""
        insp, plugin = _mk()

        async def run():
            await insp.log("u1", "已经落盘的宝贵内容")
            await insp.flush_all()
            insp._cache.clear()
            insp._weight_cache.clear()
            insp._dirty.add("u1")          # 模拟脏标记残留
            await insp.flush("u1")
            return plugin.kv.get(insp._kv_key("u1"))

        raw = asyncio.run(run())
        assert raw and json.loads(raw), "已有数据不应被空列表覆盖"

    def test_flush_failure_keeps_dirty(self):
        """落盘失败时不应丢掉脏标记，否则数据静默丢失。"""
        insp, plugin = _mk()

        async def boom(key, value):
            raise RuntimeError("kv down")

        async def run():
            await insp.log("u1", "重要内容")
            plugin.put_kv_data = boom
            await insp.flush("u1")
            return "u1" in insp._dirty

        assert asyncio.run(run()) is True
