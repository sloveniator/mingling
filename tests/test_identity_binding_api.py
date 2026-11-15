"""2.0 第四阶段：跨平台账号绑定 Web API 回归测试。

对应路由：/mingbu/identity/binding（GET 查）、/mingbu/identity/bind（POST 写/解）。
绑定是跨平台共享记忆的唯一入口。默认各平台身份隔离，仅显式绑定才归并。

安全契约：管理面板属站长信任区（本插件无 admin 鉴权层），故此处允许指定任意
platform_user_id。聊天侧只提供只读查询，不允许普通用户改绑定（防身份劫持）。
"""
import pytest

from test_admin_api import build_star, call, payload_of


def _seed(store):
    return store


def test_binding_unbound_by_default():
    """默认未绑定：GET binding 返回 bound=False，按平台隔离。"""
    star = build_star()
    resp = call(star.api_identity_binding,
                query={"platform_user_id": "wxid_x"})
    data = payload_of(resp)
    assert data["ok"] is True
    assert data["bound"] is False
    assert data["unified_user_id"] == ""


def test_bind_then_query_roundtrip():
    """POST 绑定 → GET 查询读到 → 跨平台共享记忆（读写闭环）。"""
    star = build_star()
    # 绑定 wxid_x → qq:12345
    r1 = payload_of(call(star.api_identity_bind, method="POST",
                         body={"platform_user_id": "wxid_x",
                               "unified_user_id": "qq:12345"}))
    assert r1["ok"] is True
    assert r1["action"] == "已绑定到 qq:12345"

    # GET 查询读到绑定
    data = payload_of(call(star.api_identity_binding,
                           query={"platform_user_id": "wxid_x"}))
    assert data["bound"] is True
    assert data["unified_user_id"] == "qq:12345"

    # 真实落到 KV（bind_identity 用 put_kv_data）
    store = star._kv_store
    assert store.get("mingbu.identity.binding:wxid_x") == "qq:12345"


def test_unbind_clears_binding():
    """空 unified_user_id = 解绑 → 恢复按平台隔离。"""
    star = build_star()
    call(star.api_identity_bind, method="POST",
         body={"platform_user_id": "wxid_x", "unified_user_id": "qq:12345"})
    r = payload_of(call(star.api_identity_bind, method="POST",
                        body={"platform_user_id": "wxid_x",
                              "unified_user_id": ""}))
    assert r["ok"] is True
    assert "解绑" in r["action"]
    data = payload_of(call(star.api_identity_binding,
                           query={"platform_user_id": "wxid_x"}))
    assert data["bound"] is False


def test_bind_missing_platform_user_id_rejected():
    """缺 platform_user_id → 400 错误响应，不写入。"""
    star = build_star()
    resp = call(star.api_identity_bind, method="POST", body={})
    # require_json 装饰器：缺字段返回 error_response
    payload = payload_of(resp)
    assert "缺少 platform_user_id" in str(payload)


def test_routes_registered_in_specs():
    """绑定路由必须出现在 _route_specs 路由表里（否则面板不可见）。"""
    star = build_star()
    routes = [r[0] for r in star._route_specs()]
    assert any(r.endswith("/identity/binding") for r in routes)
    assert any(r.endswith("/identity/bind") for r in routes)


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
