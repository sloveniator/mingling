"""
tests/test_group_slang.py -- 群黑话学习与管理冒烟测试
======================================================
用内存 KV 的 FakePlugin 验证 GroupSlangManager：学习、列表、更新、删除、注入。
"""
import asyncio

from group_slang import GroupSlangManager


class FakePlugin:
    def __init__(self):
        self.kv = {}

    async def get_kv_data(self, key, default=None):
        return self.kv.get(key, default)

    async def put_kv_data(self, key, value):
        self.kv[key] = value


def test_slang_learn_and_list():
    p = FakePlugin()
    mgr = GroupSlangManager(p)

    async def go():
        await mgr.learn("g1", "蚌埠住了", "今天真的蚌埠住了", confidence=0.6)
        await mgr.learn("g1", "绝绝子", "这操作绝绝子")
        await mgr.learn("g2", "yyds", "永远的神", confidence=0.8)

        g1 = await mgr.list_slang("g1")
        assert len(g1) == 2
        assert g1[0]["word"] == "蚌埠住了"  # confidence 0.6 > 0.5

        all_items = await mgr.list_slang()
        assert len(all_items) == 3

    asyncio.run(go())


def test_slang_update_delete_and_inject():
    p = FakePlugin()
    mgr = GroupSlangManager(p)

    async def go():
        await mgr.learn("g1", "蚌埠住了", "今天真的蚌埠住了")
        key = "g1:蚌埠住了"

        ok = await mgr.update(key, meaning="绷不住了，形容忍不住笑/哭", injected=True)
        assert ok

        items = await mgr.list_slang("g1")
        assert items[0]["meaning"] == "绷不住了，形容忍不住笑/哭"
        assert items[0]["injected"] is True
        assert items[0]["manual"] is True

        injected = await mgr.get_injected_words("g1")
        assert len(injected) == 1
        assert "绷不住了" in injected[0]

        ok = await mgr.delete(key)
        assert ok
        assert len(await mgr.list_slang("g1")) == 0

    asyncio.run(go())


def test_slang_learn_candidates():
    p = FakePlugin()
    mgr = GroupSlangManager(p)

    async def go():
        await mgr.learn_candidates(
            "g1", "今天真的「蚌埠住了」，这操作 yyds，2333"
        )
        items = await mgr.list_slang("g1")
        words = {it["word"] for it in items}
        assert "蚌埠住了" in words
        assert "yyds" in words

    asyncio.run(go())
