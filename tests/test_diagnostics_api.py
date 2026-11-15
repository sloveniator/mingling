"""系统诊断、模块状态和敏感字段脱敏回归测试。"""
from test_admin_api import build_star, call, payload_of


def test_diagnostics_reports_capabilities_and_dependencies():
    star = build_star()
    import asyncio
    asyncio.run(star.initialize())
    data = payload_of(call(star.api_diagnostics))
    assert data["ok"] is True
    assert data["initialized"] is True
    assert data["capabilities"]
    names = {item["name"] for item in data["capabilities"]}
    assert "dream" in names
    assert data["dependencies"]["memory"] is True


def test_connectivity_configuration_check_masks_key(monkeypatch):
    star = build_star(tts_api_key="sk-secret-value", tts_api_base="https://api.example.com/v1", tts_model="tts-test")
    import asyncio
    asyncio.run(star.initialize())
    async def fake_speech(*args, **kwargs):
        return b"ok"
    monkeypatch.setattr("stars.apis.diagnostics._direct_openai_speech", fake_speech)
    data = payload_of(call(star.api_connectivity, "POST", body={"service": "tts"}))
    assert data["ok"] is True
    assert data["mode"] == "live_request"
    assert data["api_key"] == "sk-s...alue"
    assert "sk-secret-value" not in str(data)


def test_connectivity_missing_config_is_structured_error():
    star = build_star()
    import asyncio
    asyncio.run(star.initialize())
    data = payload_of(call(star.api_connectivity, "POST", body={"service": "rerank"}))
    assert data["ok"] is False
    assert "api_key" in data["error"]
    assert data["service"] == "rerank"


def test_config_get_returns_plain_api_key():
    """API Key 明文返回，方便面板直接展示与编辑（所见即所得）。"""
    star = build_star(tts_api_key="sk-secret-value")
    import asyncio
    asyncio.run(star.initialize())
    data = payload_of(call(star.api_config))
    assert data["tts_api_key"] == "sk-secret-value"


def test_config_post_plain_key_saves_and_empty_clears():
    """明文保存直接生效；留空=清空（所见即所得，无掩码/留空保留原值）。"""
    star = build_star(tts_api_key="sk-secret-value")
    import asyncio
    asyncio.run(star.initialize())
    # 明文覆盖保存
    payload_of(call(star.api_config, method="POST",
                    body={"tts_api_key": "sk-new-key", "proactive_interval_min": 42}))
    assert star._config["tts_api_key"] == "sk-new-key"
    assert star._config["proactive_interval_min"] == 42
    # 留空 = 清空该字段
    payload_of(call(star.api_config, method="POST", body={"tts_api_key": ""}))
    assert star._config["tts_api_key"] == ""
    # GET 返回明文（空）
    data = payload_of(call(star.api_config))
    assert data["tts_api_key"] == ""


def test_connectivity_live_tts_returns_structured_result(monkeypatch):
    star = build_star(tts_api_key="sk-test", tts_api_base="https://api.example.com/v1", tts_model="tts")
    import asyncio
    asyncio.run(star.initialize())
    async def fake_speech(*args, **kwargs):
        return b"fake-mp3"
    monkeypatch.setattr("stars.apis.diagnostics._direct_openai_speech", fake_speech)
    data = payload_of(call(star.api_connectivity, "POST", body={"service": "tts"}))
    assert data["ok"] is True
    assert data["mode"] == "live_request"
    assert data["bytes"] == 8


# ---- v4.8.112：能力服务商「自定义模式」直连测试 ----

def test_connectivity_provider_direct_connect(monkeypatch):
    """api_connectivity 带 api_key+api_base 时走直连测试，不经 Provider 解析。"""
    from test_admin_api import build_star, call, payload_of

    star = build_star()
    import asyncio
    asyncio.run(star.initialize())

    async def fake_direct(*args, **kwargs):
        return "直连测试回复"

    monkeypatch.setattr("stars.apis.diagnostics._direct_openai_chat", fake_direct)
    data = payload_of(call(star.api_connectivity, "POST", body={
        "service": "provider", "task_type": "main_reply",
        "api_key": "sk-test", "api_base": "https://direct.example.com/v1",
        "model": "Qwen/Qwen3.5-4B",
    }))
    assert data["ok"] is True
    assert data.get("using_direct") is True, "应标记为直连测试"
    assert data.get("response_preview") == "直连测试回复"


def test_connectivity_provider_direct_failure_reports_error(monkeypatch):
    """直连测试失败时返回结构化错误。"""
    from test_admin_api import build_star, call, payload_of

    star = build_star()
    import asyncio
    asyncio.run(star.initialize())

    async def fake_direct_fail(*args, **kwargs):
        raise RuntimeError("401 unauthorized")

    monkeypatch.setattr("stars.apis.diagnostics._direct_openai_chat", fake_direct_fail)
    data = payload_of(call(star.api_connectivity, "POST", body={
        "service": "provider", "task_type": "main_reply",
        "api_key": "sk-bad", "api_base": "https://bad.example.com/v1",
        "model": "m",
    }))
    assert data["ok"] is False
    assert "401" in str(data.get("error", ""))
