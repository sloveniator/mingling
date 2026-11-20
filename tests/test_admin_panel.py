"""v5 管理面板 API：总览、配置读写、用户、时间线。"""
import asyncio

from conftest import StubRequest

from _version import VERSION
from admin_timeline import AdminTimelineAPI
from config import CompanionConfig


class FakeStore:
    def __init__(self):
        self.data = {
            ("u1", "relationship"): {"stage": "warming", "intimacy": 0.5},
            ("u1", "emotion"): {"pleasantness": 0.2, "tension": 0.1},
            ("u1", "topic_stack"): {"current": "工作"},
            ("u1", "open_loops"): [{"topic": "面试"}],
            ("u1", "last_user_msg_at"): 1700000000,
            ("u1", "last_mood"): "anxious",
        }

    async def list_known_umos(self):
        return ["u1"]

    async def load(self, umo, suffix, default):
        return self.data.get((umo, suffix), default)

    def timeline_stats(self):
        return {"files": 1, "bytes": 128, "last_modified": 1700000000}

    async def read_timeline(self, umo, limit=500):
        return [
            {"ts": 1, "kind": "emotion", "payload": {"delta": {"tension": 0.2}}},
            {"ts": 2, "kind": "state_layer", "payload": {"body": "关系阶段：warming"}},
        ][:limit]


class FakeConfig(dict):
    def __init__(self, data):
        super().__init__(data)
        self.saved = 0

    def save_config(self):
        self.saved += 1


class FakeCron:
    async def list_jobs(self):
        from types import SimpleNamespace
        return [
            SimpleNamespace(name="mingling_proactive_tick", cron_expression="0 * * * *",
                            timezone="Asia/Shanghai", enabled=True, next_run_time=None),
            SimpleNamespace(name="other", cron_expression="* * * * *",
                            timezone="UTC", enabled=True, next_run_time=None),
        ]


class FakePlugin:
    def __init__(self, cfg):
        self._cfg = cfg
        self._raw_config = {"mingbu_api_url": "http://127.0.0.1:2963",
                            "mingbu_interpret_api_key": "secret"}
        self.config = FakeConfig(dict(self._raw_config))
        self.context = type("Ctx", (), {"cron_manager": FakeCron()})()
        self._client = None
        self._loops = type("Loops", (), {"_window_sec": 7 * 86400})()


def _api():
    cfg = CompanionConfig.from_raw({"enabled": True})
    plugin = FakePlugin(cfg)
    return AdminTimelineAPI(FakeStore(), cfg, plugin), plugin


def _payload(res):
    if isinstance(res, tuple):
        return res[0]
    return res


def test_overview_returns_v5_stats_and_cron():
    api, _ = _api()
    data = _payload(asyncio.run(api._overview_handler(StubRequest())))
    assert data["ok"] is True
    assert data["version"] == VERSION
    assert data["users"] == 1
    assert data["timeline"]["files"] == 1
    assert len(data["cron"]) == 1
    assert data["cron"][0]["name"] == "mingling_proactive_tick"


def test_config_get_only_returns_active_keys():
    api, _ = _api()
    data = _payload(asyncio.run(api._config_handler(StubRequest())))
    assert data["ok"] is True
    assert data["config"]["mingbu_api_url"] == "http://127.0.0.1:2963"
    assert "mingbu_interpret_api_key" not in data["config"]
    assert "enabled" in data["editable"]


def test_config_post_updates_runtime_and_persists():
    api, plugin = _api()
    req = StubRequest(method="POST", body={
        "enabled": False,
        "semantic_router_confidence": 0.8,
        "proactive_max_per_day": 3,
    })
    data = _payload(asyncio.run(api._config_handler(req)))
    assert data["ok"] is True
    assert plugin._cfg.enabled is False
    assert plugin._cfg.semantic_router_confidence == 0.8
    assert plugin._cfg.proactive_max_per_day == 3
    assert plugin.config.saved == 1


def test_config_post_rejects_unknown_key():
    api, _ = _api()
    req = StubRequest(method="POST", body={"evil_key": 1})
    res = asyncio.run(api._config_handler(req))
    assert isinstance(res, tuple) and res[1] == 400
    assert "不支持的配置键" in res[0]["error"]


def test_users_summary_and_timeline_filter():
    api, _ = _api()
    users = _payload(asyncio.run(api._users_handler(StubRequest())))
    assert users["users"][0]["umo"] == "u1"
    assert users["users"][0]["stage"] == "warming"
    assert users["users"][0]["open_loops"] == 1

    req = StubRequest(query={"umo": "u1", "kind": "emotion", "limit": "10"})
    timeline = _payload(asyncio.run(api._timeline_handler(req)))
    assert timeline["count"] == 1
    assert timeline["records"][0]["kind"] == "emotion"
