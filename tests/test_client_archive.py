"""客户建档（ensure_client）接线：ProfileMixin._archive_client 契约测试。

后端 /api/clients/ensure 的契约要点：
- ``birth.hour`` 是 **0–23 时钟小时**（与 /api/bazi 的 0–11 地支不同）；
- ``gender`` 必须是 ``male`` / ``female``；
- 请求头 ``x-ziwei-user-id`` 是「匿名 UUID」，用于按用户隔离（稳定、匿名）。

因此 _archive_client 必须：
1. 用原始 birth_hour（0–23）而不是 _birth_params 折算后的地支索引；
2. 归一化性别（男/女 → male/female）；
3. 为每个用户生成一个稳定的匿名 UUID（uuid4，持久化到 profile.client_uuid），
   两次调用复用同一 UUID；
4. 任何异常都静默（仅 debug），不向排盘主流程上抛。
"""
import asyncio

from star_mixins.profile import ProfileMixin


class _FakeArchiveApi:
    def __init__(self):
        self.calls = []
        self.fail = False

    async def ensure_client(self, payload, user_id=None):
        if self.fail:
            raise RuntimeError("backend down")
        self.calls.append((payload, user_id))
        return {"client": {"id": "c1"}}


class _FakeArchiveSelf:
    def __init__(self, api):
        self.api = api
        self.profiles = {}
        self._config = {}

    async def _get_profile(self, uid):
        return self.profiles.get(uid, {})

    async def _save_profile(self, uid, profile):
        self.profiles[uid] = dict(profile)


def test_archive_client_clock_hour_and_stable_uuid():
    api = _FakeArchiveApi()
    self = _FakeArchiveSelf(api)

    async def _run():
        # 14 点（下午 2 点）——若误用折算后的地支索引会是 7，这里必须仍是 14
        await ProfileMixin._archive_client(self, "u1", 1990, 6, 15, 14, "男", "张三")
        await ProfileMixin._archive_client(self, "u1", 1990, 6, 15, 14, "男", "张三")

    asyncio.run(_run())

    assert len(api.calls) == 2
    p1, uid1 = api.calls[0]
    p2, uid2 = api.calls[1]

    # 契约 1：hour 用 0–23 时钟小时（14），而非折算后的地支索引（7）
    assert p1["birth"]["hour"] == 14
    assert p1["birth"]["year"] == 1990
    # 契约 2：性别归一化为 male
    assert p1["birth"]["gender"] == "male"
    assert p1["name"] == "张三"

    # 契约 3：稳定匿名 UUID，两次调用复用同一值，并已持久化
    assert uid1 == uid2
    assert self.profiles["u1"]["client_uuid"] == uid1
    assert len(uid1) == 36  # 标准 UUID4 格式


def test_archive_client_normalizes_female():
    api = _FakeArchiveApi()
    self = _FakeArchiveSelf(api)

    async def _run():
        await ProfileMixin._archive_client(self, "u2", 1988, 3, 2, 8, "女", "")

    asyncio.run(_run())
    assert api.calls[0][0]["birth"]["gender"] == "female"


def test_archive_client_swallows_failure():
    api = _FakeArchiveApi()
    api.fail = True
    self = _FakeArchiveSelf(api)

    async def _run():
        await ProfileMixin._archive_client(self, "u3", 1990, 1, 1, 0, "male", "")

    # 后端不可用也不应抛异常（非关键、非阻塞）
    asyncio.run(_run())


def test_archive_client_skips_invalid_gender():
    api = _FakeArchiveApi()
    self = _FakeArchiveSelf(api)

    async def _run():
        await ProfileMixin._archive_client(self, "u4", 1990, 1, 1, 0, "未知", "")

    asyncio.run(_run())
    assert api.calls == []  # 性别不合法则跳过建档
