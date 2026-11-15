"""
tests/test_admin_api.py -- 管理面板 Web API 回归测试
=====================================================
`stars/admin.py` 的 31 个路由此前**零测试覆盖**——因为 `astrbot.api.web`
从未被打桩，这些 handler 在本地根本跑不起来。代价是一批只有打开面板才会
暴露的缺陷长期存活：

  * `api_user_detail` 引用了不存在的 `m.ts`         → 打开用户详情 500
  * `api_recent_activity` 先切片后排序             → 永远只显示同一批人
  * `api_recent_activity` 读 profile["name"] 兜底 u[:4] → "wxid_zhang_wei" 显示成 "wxid"
  * `api_collection_all` 忘了 await list_presets() → 角色收集页整页 500
  * `api_collection_all` 取 p["icon"]（模板字段其实叫 avatar） → 图标全是默认 🎭
  * `api_collection_all` 把所有预设写死 unlocked=True → 收集进度恒为满

本文件用内存 KV + 请求上下文桩把这些路由真正执行一遍。
"""
import asyncio
import json

import pytest
from conftest import StubRequest, StubResponse, reset_current_request, set_current_request

# ══════════════════════════════════════════════════════════════
# 测试脚手架
# ══════════════════════════════════════════════════════════════

class FakeContext:
    def __init__(self):
        self.routes = {}

    def register_web_api(self, route, handler, methods, desc=""):
        self.routes[route] = (handler, [m.upper() for m in methods], desc)

    def get_using_provider(self, *a, **k):
        return None

    def get_all_providers(self):
        return []

    def get_runtime_id(self):
        return "test"


def reset_core():
    """重置内核单例。

    MingbuCore 是模块级单例，ensure() 只在首次调用时真正装配引擎。
    若不重置，第二个测试构造的 star 拿到的仍是**第一个** star 绑定的
    conversation_inspector / memory，写入会落到上一个测试的 KV 里，
    表现为「用户数对得上、对话数恒为 0」这种极难排查的串味。
    """
    import core as core_mod
    core_mod._CORE = None


def build_star(**cfg_override):
    """装配一个接内存 KV 的 StarAdmin（每次都是干净内核）。"""
    reset_core()
    from stars.admin import StarAdmin

    store = {}

    class TestStarAdmin(StarAdmin):
        async def get_kv_data(self, key, default=None):
            return store.get(key, default)

        async def put_kv_data(self, key, value):
            store[key] = value

    cfg = {
        "background_watch_enabled": False,
        "self_evolution_enabled": False,
        "proactive_enabled": False,
        "news_enabled": False,
        **cfg_override,
    }
    star = TestStarAdmin(FakeContext(), cfg)
    star._kv_store = store
    return star


def call(handler, method="GET", query=None, body=None):
    """在绑定请求上下文的情况下执行 handler，返回 StubResponse。"""
    async def _run():
        token = set_current_request(StubRequest(method, query, body))
        try:
            return await handler()
        finally:
            reset_current_request(token)

    return asyncio.run(_run())


def payload_of(resp):
    return resp.payload if isinstance(resp, StubResponse) else resp


@pytest.fixture()
def star():
    s = build_star()
    asyncio.run(s.initialize())
    return s


@pytest.fixture()
def seeded():
    """播三个用户 + 若干对话，覆盖含下划线的 uid。"""
    from core import Relationship

    s = build_star()

    async def _setup():
        await s.initialize()
        specs = [
            ("wxid_zhang_wei", "张伟", 60.0, 120, "attached", 3000.0),
            ("tg_10086", "Nova", 20.0, 30, "warming", 5000.0),
            ("qq_874512", "老陈", 1.0, 3, "normal", 1000.0),
        ]
        for uid, name, score, count, mode, last in specs:
            await s._remember_user(uid)
            rel = Relationship(uid)
            rel.score, rel.interaction_count, rel.mode = score, count, mode
            rel.last_interaction = last
            await s._save_relationship(rel)
            profile = await s._get_profile(uid)
            profile["name"] = name
            await s._save_profile(uid, profile)

        insp = s.core.conversation_inspector
        if insp:
            await insp.log(uid="wxid_zhang_wei", content="我妈最近身体不太好，我很担心",
                           msg_type="user")
            await insp.log(uid="wxid_zhang_wei", content="要不要陪她去做个全面检查？",
                           msg_type="bot")
            await insp.log(uid="tg_10086", content="项目黄了", msg_type="user")
            await insp.log(uid="qq_874512", content="群里聊天", msg_type="user",
                           gid="g_1", is_group=True)
            await insp.flush_all()
        return s

    return asyncio.run(_setup())


# ══════════════════════════════════════════════════════════════
# 路由注册
# ══════════════════════════════════════════════════════════════

class TestRouteRegistration:
    def test_all_routes_registered(self, star):
        routes = star.context.routes
        # 模型目录持久化、系统诊断和连通性测试均属于显式管理 API，
        # 新增路由须同步更新此处计数与专项断言。
        # v4.8.130：+3 条音色库路由（tts/voices、tts/preview、tts/preview_long）
        # 2.0 第四阶段：+2 条跨平台身份绑定路由（identity/binding、identity/bind）
        assert len(routes) == 43, f"路由数变化：{len(routes)}"

    def test_every_handler_is_coroutine_function(self, star):
        import inspect
        for route, (handler, _methods, _desc) in star.context.routes.items():
            assert inspect.iscoroutinefunction(handler), f"{route} 不是协程"

    def test_methods_are_valid(self, star):
        for route, (_h, methods, _d) in star.context.routes.items():
            assert methods, f"{route} 未声明方法"
            for m in methods:
                assert m in ("GET", "POST"), f"{route} 声明了异常方法 {m}"


# ══════════════════════════════════════════════════════════════
# 只读接口：空库 + 有数据都要能跑
# ══════════════════════════════════════════════════════════════

GET_ENDPOINTS_NO_ARGS = [
    "stats", "users", "graph", "recent_activity", "token_stats", "config",
    "collection/all", "worldview/presets", "worldview/custom",
    "slang/groups", "conversation/list", "conversation/stats",
    "conversation/knowledge", "list_providers", "diagnostics",
]


class TestReadOnlyEndpoints:
    @pytest.mark.parametrize("endpoint", GET_ENDPOINTS_NO_ARGS)
    def test_empty_store_does_not_crash(self, star, endpoint):
        """空库是真实的首次安装状态，不允许抛异常。"""
        handler = star.context.routes[f"/astrbot_plugin_mingbu/{endpoint}"][0]
        resp = call(handler)
        assert isinstance(payload_of(resp), dict)

    @pytest.mark.parametrize("endpoint", GET_ENDPOINTS_NO_ARGS)
    def test_seeded_store_returns_200(self, seeded, endpoint):
        handler = seeded.context.routes[f"/astrbot_plugin_mingbu/{endpoint}"][0]
        resp = call(handler)
        status = resp.status_code if isinstance(resp, StubResponse) else 200
        assert status == 200, payload_of(resp)


# ══════════════════════════════════════════════════════════════
# api_stats / api_users
# ══════════════════════════════════════════════════════════════

class TestStats:
    def test_reports_true_active_user_count(self, seeded):
        data = payload_of(call(seeded.api_stats))
        assert data["active_users"] == 3
        assert data["relationships_truncated"] is False
        assert len(data["relationships"]) == 3

    def test_relationship_fields_present(self, seeded):
        data = payload_of(call(seeded.api_stats))
        r = data["relationships"][0]
        assert {"uid", "mode", "score", "interactions"} <= set(r)

    def test_stats_exposes_failure_counters(self, seeded):
        """静默失败的关键路径必须可在面板里看见计数（可观测性）。"""
        from metrics import get_failure_counts, reset_failure_counts
        reset_failure_counts()
        data = payload_of(call(seeded.api_stats))
        assert "failures" in data
        counts = data["failures"]
        assert set(counts) >= {
            "conversation_log", "memory_write",
            "background_watch_emotion", "slang_learn",
        }
        # 计数均为非负整数
        assert all(isinstance(v, int) and v >= 0 for v in counts.values())
        # 确认 get_failure_counts 与面板看到的是同一份快照
        assert get_failure_counts() == counts

    def test_stats_exposes_current_version(self, seeded):
        """面板页脚的版本号靠这个字段回填，删掉它会让页脚静默停在打包时的占位值。

        历史上 pages/admin/app.js 从不消费该字段，页脚硬编码滞后了三个版本；
        修复后前端依赖它，这里把后端这一侧的契约也钉住，避免链条另一头被摘掉。
        """
        from _version import VERSION
        data = payload_of(call(seeded.api_stats))
        assert data.get("version") == VERSION


class TestUsers:
    def test_returns_all_seeded_users(self, seeded):
        data = payload_of(call(seeded.api_users))
        assert data["total"] == 3
        assert len(data["users"]) == 3

    def test_uid_with_underscore_is_intact(self, seeded):
        """uid 含下划线时不得被截断。"""
        data = payload_of(call(seeded.api_users))
        uids = {u["uid"] for u in data["users"]}
        assert "wxid_zhang_wei" in uids

    def test_not_truncated_for_small_dataset(self, seeded):
        data = payload_of(call(seeded.api_users))
        assert data["truncated"] is False


# ══════════════════════════════════════════════════════════════
# api_user_detail —— 上一轮修的 m.ts → m.created_at
# ══════════════════════════════════════════════════════════════

class TestUserDetail:
    def test_missing_uid_returns_400(self, seeded):
        resp = call(seeded.api_user_detail, query={})
        assert resp.status_code == 400
        assert resp.payload["status"] == "error"

    def test_detail_does_not_500_with_memories(self, seeded):
        """回归：曾因引用不存在的 m.ts 抛 AttributeError。"""
        async def _add():
            mem = seeded.core.memory
            if mem:
                await mem.add_episode("wxid_zhang_wei", "聊到了家人的健康")
                await mem.flush_all()
        asyncio.run(_add())

        resp = call(seeded.api_user_detail, query={"uid": "wxid_zhang_wei"})
        status = resp.status_code if isinstance(resp, StubResponse) else 200
        assert status == 200, resp.payload
        assert resp.payload["uid"] == "wxid_zhang_wei"

    def test_unknown_uid_returns_empty_shape(self, seeded):
        resp = call(seeded.api_user_detail, query={"uid": "nobody_here"})
        data = payload_of(resp)
        assert data.get("uid") == "nobody_here"


# ══════════════════════════════════════════════════════════════
# api_recent_activity —— 排序 + 昵称，两个都修过
# ══════════════════════════════════════════════════════════════

class TestRecentActivity:
    def test_sorted_by_last_interaction_desc(self, seeded):
        """回归：曾先取 uids[:10] 再排序，永远是同一批人。"""
        items = payload_of(call(seeded.api_recent_activity))["items"]
        ts = [x["timestamp"] for x in items]
        assert ts == sorted(ts, reverse=True)
        assert items[0]["user_uid"] == "tg_10086"   # last_interaction 最大

    def test_user_name_not_truncated(self, seeded):
        """回归：读 profile["name"] 失败时兜底 u[:4]，"wxid_zhang_wei"→"wxid"。"""
        items = payload_of(call(seeded.api_recent_activity))["items"]
        by_uid = {x["user_uid"]: x["user_name"] for x in items}
        assert by_uid["wxid_zhang_wei"] == "张伟"
        assert "wxid" not in by_uid.values()

    def test_falls_back_to_full_uid_when_no_name(self, star):
        """无档案时应回落到完整 uid，而不是切前 4 个字符。"""
        from core import Relationship

        async def _setup():
            await star.initialize()
            await star._remember_user("wxid_no_profile_user")
            rel = Relationship("wxid_no_profile_user")
            rel.last_interaction = 999.0
            await star._save_relationship(rel)
        asyncio.run(_setup())

        items = payload_of(call(star.api_recent_activity))["items"]
        assert items[0]["user_name"] == "wxid_no_profile_user"

    def test_caps_at_ten(self, star):
        from core import Relationship

        async def _setup():
            await star.initialize()
            for i in range(25):
                uid = f"user_{i:03d}"
                await star._remember_user(uid)
                rel = Relationship(uid)
                rel.last_interaction = float(i)
                await star._save_relationship(rel)
        asyncio.run(_setup())

        items = payload_of(call(star.api_recent_activity))["items"]
        assert len(items) == 10
        assert items[0]["user_uid"] == "user_024"


# ══════════════════════════════════════════════════════════════
# api_collection_all —— 本轮修的 500
# ══════════════════════════════════════════════════════════════

class TestCollectionAll:
    def test_does_not_500(self, star):
        """回归：list_presets 是协程却未 await，且调用了不存在的 get_hidden_preset。"""
        resp = call(star.api_collection_all)
        status = resp.status_code if isinstance(resp, StubResponse) else 200
        assert status == 200, payload_of(resp)

    def test_characters_is_a_list_of_dicts(self, star):
        data = payload_of(call(star.api_collection_all))
        assert isinstance(data["characters"], list)
        assert data["characters"], "角色列表不应为空"
        for c in data["characters"]:
            assert {"id", "name", "description", "icon", "unlocked",
                    "hidden", "affinity",
                    # 角色人设富字段
                    "background", "catchphrases", "taboos", "pet_names",
                    "sample_dialogue", "emoji_usage", "verbosity",
                    "speaking_style", "counseling_style"} <= set(c)
            # 富字段应确有内容（预设角色已填充）
            assert c["background"], f"{c['id']} 缺少 background"
            assert c["catchphrases"], f"{c['id']} 缺少 catchphrases"
            assert c["verbosity"], f"{c['id']} 缺少 verbosity"

    def test_icon_comes_from_avatar_field(self, star):
        """回归：模板字段是 avatar，误取 icon 会让图标全变成默认 🎭。"""
        data = payload_of(call(star.api_collection_all))
        icons = [c["icon"] for c in data["characters"]]
        assert len(set(icons)) > 1, f"图标全都一样，疑似取错字段: {icons}"

    def test_hidden_character_present_and_locked(self, star):
        data = payload_of(call(star.api_collection_all))
        hidden = [c for c in data["characters"] if c["hidden"]]
        assert len(hidden) == 1
        assert hidden[0]["unlocked"] is False
        assert data["hidden_unlocked"] is False

    def test_unlocked_count_reflects_owned_only(self, star):
        """回归：曾把所有预设写死 unlocked=True，进度条恒为满。"""
        data = payload_of(call(star.api_collection_all))
        assert data["unlocked_count"] <= data["total_with_hidden"]
        owned = [c for c in data["characters"] if c["unlocked"]]
        assert len(owned) == data["unlocked_count"]

    def test_unlock_hidden_flips_flag(self, star):
        before = payload_of(call(star.api_collection_all))
        assert before["hidden_unlocked"] is False

        resp = call(star.api_collection_unlock_hidden, method="POST",
                    query={"uid": "u1"})
        assert payload_of(resp).get("ok") is True

        after = payload_of(call(star.api_collection_all))
        assert after["hidden_unlocked"] is True
        hidden = [c for c in after["characters"] if c["hidden"]][0]
        assert hidden["unlocked"] is True

    def test_affinity_within_range(self, star):
        data = payload_of(call(star.api_collection_all))
        for c in data["characters"]:
            assert 0 <= c["affinity"] <= 100

    def test_with_uid_respects_per_user_unlocked(self, star):
        """回归：/collection/all 曾用全局角色池判定解锁，导致 UI 显示已解锁但切换失败。"""
        uid = "u_per_user"
        # 只解锁前两个预设角色
        unlocked_ids = ["preset-mystery-friend", "preset-gentle-sister"]
        asyncio.run(star.put_kv_data(
            f"mingbu.collection:{uid}",
            json.dumps({"unlocked": unlocked_ids, "current": unlocked_ids[0]}),
        ))

        data = payload_of(call(star.api_collection_all, query={"uid": uid}))
        by_id = {c["id"]: c for c in data["characters"]}
        assert by_id["preset-mystery-friend"]["unlocked"] is True
        assert by_id["preset-gentle-sister"]["unlocked"] is True
        assert by_id["preset-quiet-companion"]["unlocked"] is False
        assert data["unlocked_count"] == 2


# ══════════════════════════════════════════════════════════════
# 对话检视相关路由
# ══════════════════════════════════════════════════════════════

class TestConversationRoutes:
    def test_list_returns_seeded_logs(self, seeded):
        data = payload_of(call(seeded.api_conversation_list))
        assert data["total"] == 4
        assert len(data["items"]) == 4

    def test_list_filters_by_uid(self, seeded):
        data = payload_of(call(seeded.api_conversation_list,
                               query={"uid": "wxid_zhang_wei"}))
        assert data["total"] == 2

    def test_list_search_is_applied(self, seeded):
        data = payload_of(call(seeded.api_conversation_list,
                               query={"search": "身体"}))
        assert data["total"] == 1

    def test_list_pagination(self, seeded):
        data = payload_of(call(seeded.api_conversation_list,
                               query={"limit": "2", "offset": "1"}))
        assert data["limit"] == 2
        assert data["offset"] == 1
        assert len(data["items"]) == 2
        assert data["total"] == 4

    def test_learn_then_unlearn_roundtrip(self, seeded):
        listing = payload_of(call(seeded.api_conversation_list, query={"limit": "1"}))
        log_id = listing["items"][0]["id"]

        r1 = payload_of(call(seeded.api_conversation_learn, method="POST",
                             body={"log_id": log_id, "note": "重要"}))
        assert r1["ok"] is True
        learned = payload_of(call(seeded.api_conversation_list,
                                  query={"learned_only": "true"}))
        assert learned["total"] == 1

        r2 = payload_of(call(seeded.api_conversation_unlearn, method="POST",
                             body={"log_id": log_id}))
        assert r2["ok"] is True
        learned2 = payload_of(call(seeded.api_conversation_list,
                                   query={"learned_only": "true"}))
        assert learned2["total"] == 0

    def test_delete_removes_the_record(self, seeded):
        listing = payload_of(call(seeded.api_conversation_list, query={"limit": "1"}))
        log_id = listing["items"][0]["id"]
        payload_of(call(seeded.api_conversation_delete, method="POST",
                        body={"log_id": log_id}))
        after = payload_of(call(seeded.api_conversation_list))
        assert after["total"] == 3
        assert log_id not in [x["id"] for x in after["items"]]

    def test_write_ops_on_underscore_uid(self, seeded):
        """log_id 由 uid 拼接而成，含下划线的 uid 曾被 split("_")[0] 截断。"""
        listing = payload_of(call(seeded.api_conversation_list,
                                  query={"uid": "wxid_zhang_wei", "limit": "1"}))
        log_id = listing["items"][0]["id"]
        assert log_id.startswith("wxid_zhang_wei_")
        r = payload_of(call(seeded.api_conversation_learn, method="POST",
                            body={"log_id": log_id, "note": "x"}))
        assert r["ok"] is True

    def test_weighted_requires_uid(self, seeded):
        resp = call(seeded.api_conversation_weighted, query={})
        assert resp.status_code == 400

    def test_stats_shape(self, seeded):
        data = payload_of(call(seeded.api_conversation_stats))
        assert {"total_users", "total_logs", "total_learned",
                "total_weighted"} <= set(data)
        assert data["total_logs"] == 4


# ══════════════════════════════════════════════════════════════
# api_list_providers —— 面板 Provider ID 下拉的数据源
# ══════════════════════════════════════════════════════════════

class FakeProvider:
    def __init__(self, pid, name=None):
        self.id = pid
        self.name = name or pid


class FakeProviderManager:
    def __init__(self, insts):
        self.llm_provider_insts = insts


class TestListProviders:
    def test_no_provider_manager_returns_empty(self, star):
        """环境无 provider_manager 时降级为空列表，不允许抛异常。"""
        data = payload_of(call(star.api_list_providers))
        assert data["ok"] is True
        assert data["providers"] == []

    def test_enumerates_llm_providers(self, star):
        star.context.provider_manager = FakeProviderManager([
            FakeProvider("openai-gpt-4o"),
            FakeProvider("deepseek", "DeepSeek"),
        ])
        data = payload_of(call(star.api_list_providers))
        ids = [p["id"] for p in data["providers"]]
        assert ids == ["openai-gpt-4o", "deepseek"]
        # name 与 id 不同时一并返回
        by_id = {p["id"]: p["name"] for p in data["providers"]}
        assert by_id["deepseek"] == "DeepSeek"

    def test_dedupes_by_id(self, star):
        star.context.provider_manager = FakeProviderManager([
            FakeProvider("dup"),
            FakeProvider("dup"),
        ])
        data = payload_of(call(star.api_list_providers))
        assert len(data["providers"]) == 1


# ══════════════════════════════════════════════════════════════
# 配置读写
# ══════════════════════════════════════════════════════════════

class TestConfig:
    def test_get_returns_dict(self, star):
        data = payload_of(call(star.api_config, method="GET"))
        assert isinstance(data, dict)

    def test_post_persists_and_reads_back(self, star):
        payload_of(call(star.api_config, method="POST",
                        body={"proactive_interval_min": 42}))
        data = payload_of(call(star.api_config, method="GET"))
        assert data.get("proactive_interval_min") == 42


# ══════════════════════════════════════════════════════════════
# KV 读写
# ══════════════════════════════════════════════════════════════

class TestKV:
    def test_get_requires_key(self, star):
        resp = call(star.api_kv_get, query={})
        assert resp.status_code == 400

    def test_set_then_get(self, star):
        payload_of(call(star.api_kv_set, method="POST",
                        body={"key": "mingbu.t", "value": "hello"}))
        data = payload_of(call(star.api_kv_get, query={"key": "mingbu.t"}))
        assert data["value"] == "hello"


# ══════════════════════════════════════════════════════════════
# 列表上限
# ══════════════════════════════════════════════════════════════

class TestListCap:
    def test_users_capped_and_flagged(self, star):
        from core import Relationship
        from stars.admin import MAX_LIST_USERS

        async def _setup():
            await star.initialize()
            for i in range(MAX_LIST_USERS + 30):
                uid = f"u{i:04d}"
                await star._remember_user(uid)
                await star._save_relationship(Relationship(uid))
        asyncio.run(_setup())

        data = payload_of(call(star.api_users))
        assert len(data["users"]) == MAX_LIST_USERS
        assert data["total"] == MAX_LIST_USERS + 30
        assert data["truncated"] is True

    def test_stats_reports_untruncated_total(self, star):
        from core import Relationship
        from stars.admin import MAX_LIST_USERS

        async def _setup():
            await star.initialize()
            for i in range(MAX_LIST_USERS + 5):
                uid = f"u{i:04d}"
                await star._remember_user(uid)
                await star._save_relationship(Relationship(uid))
        asyncio.run(_setup())

        data = payload_of(call(star.api_stats))
        assert data["active_users"] == MAX_LIST_USERS + 5
        assert len(data["relationships"]) == MAX_LIST_USERS
        assert data["relationships_truncated"] is True
