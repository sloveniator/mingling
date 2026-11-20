"""cron 注册兼容性：AstrBot 4.29 的异步 cron_expression 与旧版 cron。"""
import asyncio

from main import MinglingPlugin


def test_add_cron_job_async_cron_expression():
    calls = {}

    class Manager:
        async def add_basic_job(self, *, name, cron_expression, handler, description, timezone=None):
            calls.update(
                name=name, expression=cron_expression,
                description=description, timezone=timezone,
            )

    plugin = object.__new__(MinglingPlugin)

    async def handler():
        return None

    asyncio.run(plugin._add_cron_job(
        Manager(), name="j1", expression="0 * * * *",
        handler=handler, description="d",
    ))
    assert calls["name"] == "j1"
    assert calls["expression"] == "0 * * * *"
    assert calls["timezone"] == "Asia/Shanghai"


def test_add_cron_job_sync_legacy_cron():
    calls = {}

    class Manager:
        def add_basic_job(self, *, name, cron, handler, description):
            calls.update(name=name, expression=cron)

    plugin = object.__new__(MinglingPlugin)

    async def handler():
        return None

    asyncio.run(plugin._add_cron_job(
        Manager(), name="j2", expression="0 3 * * 1",
        handler=handler, description="d",
    ))
    assert calls == {"name": "j2", "expression": "0 3 * * 1"}


def test_add_cron_job_deletes_same_name_duplicates():
    from types import SimpleNamespace

    deleted = []
    added = []

    class Manager:
        async def list_jobs(self):
            return [
                SimpleNamespace(name="j3", job_id="old-1"),
                SimpleNamespace(name="other", job_id="keep"),
                SimpleNamespace(name="j3", job_id="old-2"),
            ]

        async def delete_job(self, job_id):
            deleted.append(job_id)

        async def add_basic_job(self, *, name, cron_expression, handler, description, timezone=None):
            added.append(name)

    plugin = object.__new__(MinglingPlugin)

    async def handler():
        return None

    asyncio.run(plugin._add_cron_job(
        Manager(), name="j3", expression="0 * * * *",
        handler=handler, description="d",
    ))
    assert deleted == ["old-1", "old-2"]
    assert added == ["j3"]
