"""
tests/test_social.py -- 群组节点 & 关系网冒烟测试
================================================
用内存 KV 的 FakePlugin 验证 SocialGraph：共同成员、群组索引、关系网提示。
"""
import asyncio

from social import SocialGraph


class FakePlugin:
    def __init__(self):
        self.kv = {}
        self.active = []

    async def _active_uids(self):
        return list(self.active)

    async def get_kv_data(self, key, default=None):
        return self.kv.get(key, default)

    async def put_kv_data(self, key, value):
        self.kv[key] = value


def test_social_touch_and_co_members():
    p = FakePlugin()
    p.active = ["u1", "u2", "u3"]
    g = SocialGraph(p)

    async def go():
        await g.touch_group("g1", "u1", "群一")
        await g.touch_group("g1", "u2", "群一")
        await g.touch_group("g2", "u1", "群二")
        await g.touch_group("g2", "u3", "群二")

        # u1 与 u2(群一)、u3(群二) 同圈；u2 只与 u1 同圈
        co1 = await g.co_members("u1")
        assert "u2" in co1 and "u3" in co1
        co2 = await g.co_members("u2")
        assert co2 == ["u1"]

        # 群组索引
        assert set(await g.groups_of("u1")) == {"g1", "g2"}
        assert await g.groups_of("u3") == ["g2"]

        # 共享群
        assert await g.shared_groups("u1", "u2") == ["g1"]
        assert await g.shared_groups("u1", "u3") == ["g2"]

        # 关系网提示（有共同圈子 => 非空；无圈子 => 空）
        assert await g.relation_hint("u1")
        assert await g.relation_hint("uX") == ""

    asyncio.run(go())


def test_social_list_groups():
    p = FakePlugin()
    p.active = ["a", "b"]
    g = SocialGraph(p)

    async def go():
        await g.touch_group("gA", "a", "群A")
        await g.touch_group("gA", "b", "群A")
        lst = await g.list_groups()
        assert len(lst) == 1
        assert lst[0]["name"] == "群A"
        assert lst[0]["member_count"] == 2

    asyncio.run(go())
