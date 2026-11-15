"""provider 列表拉取诊断与连通性测试的回归测试。"""
import asyncio
from unittest.mock import MagicMock

import pytest

from provider_router import list_llm_providers, list_llm_providers_with_diagnostics


class FakeProvider:
    text_chat = None  # 模拟 LLM provider，用于 provider_insts 过滤

    def __init__(self, pid, name=None):
        self.id = pid
        self.name = name or pid


class LegacyProvider:
    """模拟 AstrBot 旧版或结构异的 provider 实例。"""

    def __init__(self, provider_id):
        self.provider_id = provider_id


class NoIdProvider:
    """缺少任何 ID 属性的 provider。"""

    def __init__(self):
        self.text_chat = lambda **kw: None


class _FakePM:
    pass


def _ctx_with_pm(**pm_attrs):
    ctx = MagicMock()
    pm = _FakePM()
    for k, v in pm_attrs.items():
        setattr(pm, k, v)
    ctx.provider_manager = pm
    return ctx


def _run(coro):
    return asyncio.run(coro)


def test_list_providers_empty_when_no_context():
    result = _run(list_llm_providers_with_diagnostics(None))
    assert result["providers"] == []
    assert result["diagnostics"]["root_cause"] == "context_missing"
    assert "未注入" in result["diagnostics"]["message"]


def test_list_providers_empty_when_no_provider_manager():
    ctx = MagicMock()
    ctx.provider_manager = None
    result = _run(list_llm_providers_with_diagnostics(ctx))
    assert result["providers"] == []
    assert result["diagnostics"]["root_cause"] == "manager_missing"


def test_list_providers_from_llm_provider_insts():
    ctx = _ctx_with_pm(llm_provider_insts=[FakeProvider("p1", "Prov1"), FakeProvider("p2")])
    result = _run(list_llm_providers_with_diagnostics(ctx))
    assert result["providers"] == [{"id": "p1", "name": "Prov1"}, {"id": "p2", "name": "p2"}]
    assert result["diagnostics"]["root_cause"] == "none"
    assert result["diagnostics"]["provider_sample"]["type"] == "FakeProvider"


def test_list_providers_from_provider_insts_filtered_by_text_chat():
    chat = FakeProvider("chat1")

    class TtsProvider:
        pass

    ctx = _ctx_with_pm(provider_insts=[chat, TtsProvider()])
    result = _run(list_llm_providers_with_diagnostics(ctx))
    assert result["providers"] == [{"id": "chat1", "name": "chat1"}]


def test_list_providers_from_method_call():
    ctx = _ctx_with_pm()
    ctx.provider_manager.get_all_llm_providers = lambda: [FakeProvider("m1")]
    result = _run(list_llm_providers_with_diagnostics(ctx))
    assert result["providers"] == [{"id": "m1", "name": "m1"}]
    strat = next(s for s in result["diagnostics"]["strategies"] if "get_all_llm_providers" in s["name"])
    assert strat["found"] == 1


def test_list_providers_legacy_provider_id_attribute():
    ctx = _ctx_with_pm(llm_provider_insts=[LegacyProvider("legacy-id")])
    result = _run(list_llm_providers_with_diagnostics(ctx))
    assert result["providers"] == [{"id": "legacy-id", "name": "legacy-id"}]


def test_list_providers_id_attribute_missing():
    ctx = _ctx_with_pm(llm_provider_insts=[NoIdProvider()])
    result = _run(list_llm_providers_with_diagnostics(ctx))
    assert result["providers"] == []
    assert result["diagnostics"]["root_cause"] == "id_attribute_missing"


def test_list_providers_method_exception_surfaces_in_strategies():
    ctx = _ctx_with_pm()

    def boom():
        raise RuntimeError("auth failed")

    ctx.provider_manager.get_all_llm_providers = boom
    ctx.provider_manager.get_providers = lambda: []
    result = _run(list_llm_providers_with_diagnostics(ctx))
    assert result["providers"] == []
    assert result["diagnostics"]["root_cause"] == "no_providers_configured"
    strat = next(s for s in result["diagnostics"]["strategies"] if "get_all_llm_providers" in s["name"])
    assert "auth failed" in strat["error"]


def test_list_llm_providers_backward_compatible():
    """list_llm_providers 应保持原返回 list 的签名。"""
    ctx = _ctx_with_pm(llm_provider_insts=[FakeProvider("p1")])
    providers = _run(list_llm_providers(ctx))
    assert providers == [{"id": "p1", "name": "p1"}]


# ── AstrBot Dashboard 地址解析（修复默认端口 61805→6185 + 裸地址补协议）──
def test_dashboard_default_port_is_6185():
    """留空 astrbot_dashboard_url 时，默认端口必须是 6185（不是历史笔误 61805）。"""
    from provider_router import _resolve_astrbot_dashboard

    base, key, ssl_skip = _resolve_astrbot_dashboard({})
    assert base == "http://127.0.0.1:6185"
    assert key == ""
    assert ssl_skip is False


def test_dashboard_bare_ip_gets_http_prefix():
    """裸「IP:端口」地址应自动补 http://，避免 aiohttp 抛 No scheme supplied。"""
    from provider_router import _resolve_astrbot_dashboard

    base, key, ssl_skip = _resolve_astrbot_dashboard(
        {"astrbot_dashboard_url": "192.168.2.77:6185"}
    )
    assert base == "http://192.168.2.77:6185"
    assert ssl_skip is True


def test_dashboard_full_url_unchanged():
    """带协议前缀的地址保持不变（https 不被误改）。"""
    from provider_router import _resolve_astrbot_dashboard

    base, _, _ = _resolve_astrbot_dashboard(
        {"astrbot_dashboard_url": "https://ab.example.com:4456/"}
    )
    assert base == "https://ab.example.com:4456"


def test_http_api_failure_surfaces_error_in_message():
    """HTTP API 拉取失败且实例枚举也失败时，诊断 message 应带具体 Dashboard 错误。"""
    import provider_router as pr

    async def boom(*a, **k):
        raise ValueError("Invalid URL '192.168.2.77:6899/api/v1/providers': No scheme supplied")

    # 让 _resolve_astrbot_dashboard 命中「有 url」分支 → 走 HTTP API → 抛异常
    ctx = _ctx_with_pm()  # provider_manager 存在但无实例 → 回退枚举也为空
    # 关键：直接打桩 _fetch_llm_providers_via_api，避免依赖真实网络
    orig = pr._fetch_llm_providers_via_api
    pr._fetch_llm_providers_via_api = boom
    try:
        result = _run(list_llm_providers_with_diagnostics(
            ctx, {"astrbot_dashboard_url": "192.168.2.77:6899"}
        ))
    finally:
        pr._fetch_llm_providers_via_api = orig

    assert result["providers"] == []
    assert "No scheme supplied" in result["diagnostics"]["message"]

