"""管理面板 /list_providers 与 /connectivity 诊断增强的回归测试。"""
import asyncio

import astrbot_plugin_mingbu.http_client as hc
import pytest
from astrbot_plugin_mingbu.provider_router import _match_provider_by_name
from conftest import StubRequest, reset_current_request, set_current_request

from tests.test_admin_api import FakeContext, build_star, call, payload_of


class FakeProvider:
    def __init__(self, pid, name=None):
        self.id = pid
        self.name = name or pid

    async def text_chat(self, **kw):
        return "pong"


class FakeAstrBotProvider:
    """模拟真实 AstrBot Provider：不暴露 id 属性，id 在 provider_config 中。"""

    def __init__(self, pid, provider_type="openai", model="gpt-4o"):
        self.provider_config = {"id": pid, "type": provider_type, "model": model}
        self.provider_settings = {}
        self.model_name = model

    async def text_chat(self, **kw):
        return "pong"

    def meta(self):
        class Meta:
            id = self.provider_config["id"]
            model = self.model_name
            type = self.provider_config["type"]
            provider_type = "chat_completion"
        return Meta()


class FakeProviderManager:
    def __init__(self, insts):
        self.llm_provider_insts = insts


@pytest.fixture()
def star():
    s = build_star()
    asyncio.run(s.initialize())
    return s


class TestListProvidersDiagnostics:
    def test_returns_diagnostics_when_no_provider_manager(self, star):
        data = payload_of(call(star.api_list_providers))
        assert data["ok"] is True
        assert data["providers"] == []
        diag = data["diagnostics"]
        assert diag["context_available"] is True
        assert diag["provider_manager_available"] is False
        assert diag["root_cause"] == "manager_missing"

    def test_returns_providers_and_diagnostics(self, star):
        star.context.provider_manager = FakeProviderManager([
            FakeProvider("openai-gpt-4o"),
            FakeProvider("deepseek", "DeepSeek"),
        ])
        data = payload_of(call(star.api_list_providers))
        assert data["ok"] is True
        ids = [p["id"] for p in data["providers"]]
        assert ids == ["openai-gpt-4o", "deepseek"]
        diag = data["diagnostics"]
        assert diag["root_cause"] == "none"
        assert diag["provider_manager_available"] is True
        assert any("llm_provider_insts" in s["name"] for s in diag["strategies"])

    def test_extracts_id_from_provider_config(self, star):
        """AstrBot 真实 Provider 实例不直接暴露 id，须从 provider_config 读取。"""
        star.context.provider_manager = FakeProviderManager([
            FakeAstrBotProvider("qwen-32b", provider_type="qwen", model="qwen-32b-chat"),
            FakeAstrBotProvider("deepseek-chat", provider_type="deepseek", model="deepseek-chat"),
        ])
        data = payload_of(call(star.api_list_providers))
        assert data["ok"] is True
        ids = [p["id"] for p in data["providers"]]
        assert ids == ["qwen-32b", "deepseek-chat"]
        by_id = {p["id"]: p["name"] for p in data["providers"]}
        assert "qwen" in by_id["qwen-32b"] and "qwen-32b-chat" in by_id["qwen-32b"]
        diag = data["diagnostics"]
        assert diag["root_cause"] == "none"
        assert "id_attribute_missing" not in diag["message"]

    def test_http_api_failure_falls_back_without_panic_message(self, star, monkeypatch):
        """Dashboard HTTP API 失败但 provider_manager 枚举成功时，不应把熔断/连接
        错误拼到主 message 里吓人；实例枚举成功就是成功。"""
        async def fake_request_json(*args, **kwargs):
            raise hc.ServiceHttpError("Mingbu 外部服务熔断中（连续失败过多），请稍后重试")

        monkeypatch.setattr(hc, "request_json", fake_request_json)
        star._config["astrbot_dashboard_url"] = "http://127.0.0.1:6185"
        star.context.provider_manager = FakeProviderManager([
            FakeProvider("local-provider", "本地模型"),
        ])
        data = payload_of(call(star.api_list_providers))
        assert data["ok"] is True
        assert [p["id"] for p in data["providers"]] == ["local-provider"]
        diag = data["diagnostics"]
        assert diag["root_cause"] == "none"
        assert "熔断" not in diag["message"]
        assert "Dashboard 拉取失败" not in diag["message"]
        # 详细错误仍保留在 diagnostics 中供排查
        assert "熔断" in (diag.get("http_api_error") or "")


class TestMatchProviderByName:
    def test_matches_real_astrbot_provider_by_config_id(self):
        """配置填 Provider ID 时，即使实例没有 id 属性也能按 provider_config 匹配。"""
        pm = FakeProviderManager([FakeAstrBotProvider("qwen-32b")])
        ctx = type("Ctx", (), {"provider_manager": pm})()
        assert _match_provider_by_name(ctx, "qwen-32b") is not None

    def test_matches_by_name_attribute(self):
        pm = FakeProviderManager([FakeProvider("deepseek", "DeepSeek")])
        ctx = type("Ctx", (), {"provider_manager": pm})()
        assert _match_provider_by_name(ctx, "DeepSeek") is not None


class TestConnectivityWithProviderId:
    def test_resolve_by_provider_id(self, star):
        pm = FakeProviderManager([])
        pm.get_provider_by_id = lambda pid: FakeProvider(pid, "Resolved") if pid == "ds1" else None
        star.context.provider_manager = pm
        data = payload_of(call(star.api_connectivity, method="POST", body={
            "service": "provider",
            "provider_id": "ds1",
        }))
        assert data["ok"] is True
        assert data["provider_id"] == "ds1"

    def test_missing_provider_id(self, star):
        pm = FakeProviderManager([])
        pm.get_provider_by_id = lambda pid: None
        star.context.provider_manager = pm
        data = payload_of(call(star.api_connectivity, method="POST", body={
            "service": "provider",
            "provider_id": "not-exist",
        }))
        assert data["ok"] is False
        assert "未找到" in data["error"]
