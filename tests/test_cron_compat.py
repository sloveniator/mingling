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
