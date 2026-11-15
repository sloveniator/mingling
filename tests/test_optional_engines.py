"""
tests/test_optional_engines.py -- 可选引擎缺失时的优雅降级护栏
==============================================================
插件的十余个引擎（主动关怀、后台关注、梦境、社交图谱……）都由配置开关控制，
关掉后对应属性就是 ``None``。热路径与命令处理必须逐一判空，否则用户在面板上
关掉一个开关，插件就在运行时崩溃。

这类缺陷功能测试抓不到：默认配置下引擎都在，测试全绿；只有把开关关掉才暴露。
本文件实测发现过一个真实线上缺陷——``_on_llm_request_body`` 无条件调用
``self.proactive.track_session``，导致任何关掉"启用主动关怀"的用户，
每条私聊消息都让 LLM 注入链路抛 AttributeError（main 层无兜底）。

覆盖列表**由能力注册表驱动**：新增 ``@feature(...)`` 会自动纳入本护栏，
无需回来补用例。
"""
import asyncio

import pytest

from features import get_features

MSG = "最近有点累，想找人说说话"

# 有单一开关的能力才能逐项关闭；复合条件能力（如 news）由 all-off 用例兜住。
TOGGLEABLE = [(f.name, f.config_key) for f in get_features().values() if f.config_key]

# 少数能力名与它在 core 上落的属性名不一致。新增此类能力时在这里补一条，
# 否则「关掉后属性应为 None」的断言会因取不到属性而报错。
ATTR_ALIASES = {
    "slang": "slang_manager",
    "news": "news_search",
}


def _feature_attr(name: str) -> str:
    return ATTR_ALIASES.get(name, name)


def _make_event(msg_type="FriendMessage"):
    from astrbot.api.event import AstrMessageEvent
    return AstrMessageEvent(
        message_str=MSG,
        unified_msg_origin="test:degrade:u_d",
        session_id="s_d",
        msg_type=msg_type,
    )


def _make_req():
    from astrbot.api.provider import ProviderRequest
    return ProviderRequest(prompt=MSG, session_id="s_d")


def _run_hotpath(**cfg_override):
    """按给定配置走完整生命周期：装配 → 私聊/群聊热路径 → 卸载。异常直接冒泡。

    卸载一并纳入，是因为 ``core.shutdown()`` 要逐个停引擎，属于同一类
    「可选引擎缺失」风险；顺带释放 HTTP 连接池，避免测试留下未关闭的
    aiohttp session。
    """
    from tests.test_admin_api import build_star

    async def _run():
        star = build_star(**cfg_override)
        await star.initialize()
        await star._on_llm_request_body(_make_event("FriendMessage"), _make_req())
        await star._on_llm_request_body(_make_event("GroupMessage"), _make_req())
        await star.core.shutdown()
        return star

    return asyncio.run(_run())


# 让所有可选能力都开着的基线配置（build_star 默认关掉了好几个）。
ALL_ON = {key: True for _, key in TOGGLEABLE}
ALL_OFF = {key: False for _, key in TOGGLEABLE}


class TestRegistryIntrospection:
    """护栏自身的有效性依赖注册表可自省——先把这点钉住。"""

    def test_registry_is_populated(self):
        assert len(get_features()) >= 10, (
            f"能力注册表只有 {len(get_features())} 项，疑似 features.py 未被导入"
            f"（装饰器没执行，注册表为空会让本文件所有用例变成空跑）"
        )

    def test_most_features_expose_config_key(self):
        """绝大多数能力应有单一开关，否则本护栏覆盖面会悄悄缩小。"""
        total = len(get_features())
        assert len(TOGGLEABLE) >= total - 2, (
            f"{total} 个能力里只有 {len(TOGGLEABLE)} 个暴露了 config_key，"
            f"逐项降级覆盖面不足"
        )

    def test_config_keys_are_unique(self):
        keys = [key for _, key in TOGGLEABLE]
        assert len(keys) == len(set(keys)), f"能力开关配置项重复：{keys}"

    @pytest.mark.parametrize("name,key", TOGGLEABLE)
    def test_toggle_actually_controls_feature(self, name, key):
        """开关必须真的能关掉能力——否则下面的降级用例是空转。"""
        feat = get_features()[name]
        assert feat.enabled_if({key: True}) is True, f"{name}: 开关置 True 未启用"
        assert feat.enabled_if({key: False}) is False, f"{name}: 开关置 False 未关闭"


class TestHotPathSurvivesMissingEngine:
    """逐个关掉能力，热路径都不得抛异常。"""

    @pytest.mark.parametrize("name,key", TOGGLEABLE)
    def test_single_engine_disabled(self, name, key):
        cfg = dict(ALL_ON)
        cfg[key] = False
        try:
            star = _run_hotpath(**cfg)
        except Exception as e:  # noqa: BLE001 - 断言用，需要看到原始异常
            pytest.fail(
                f"关闭「{name}」（{key}=False）后热路径抛出 {type(e).__name__}: {e}\n"
                f"可选引擎必须判空后再调用。"
            )
        attr = _feature_attr(name)
        actual = getattr(star, attr, "__missing__")
        assert actual is None, (
            f"{key}=False 时 core.{attr} 应为 None，实际为 {actual!r}。"
            f"若该能力落在别的属性名上，请在 ATTR_ALIASES 里补一条映射"
        )

    def test_all_engines_disabled(self):
        """最小配置（全部关掉）也要能正常跑——这是最脆弱的组合。"""
        try:
            _run_hotpath(**ALL_OFF)
        except Exception as e:  # noqa: BLE001
            pytest.fail(f"全部可选引擎关闭后热路径抛出 {type(e).__name__}: {e}")

    def test_all_engines_enabled(self):
        """反向基线：全开也要能跑，确认上面的通过不是因为整体没执行。"""
        try:
            _run_hotpath(**ALL_ON)
        except Exception as e:  # noqa: BLE001
            pytest.fail(f"全部可选引擎开启后热路径抛出 {type(e).__name__}: {e}")


class TestOnboardingDegradesCleanly:
    """onboarding 曾经在缺 proactive 时留下"毒标记"：已标记已引导，却没发欢迎。"""

    def test_onboarding_still_assigns_character_without_proactive(self):
        """缺主动引擎时，onboarding 的角色分配环节仍应执行完。

        原实现里 onboarded 标记先落库、紧接着调 proactive 崩掉，异常被
        except 咽掉——用户被永久标记为已引导，却拿不到初始角色。
        """
        from tests.test_admin_api import build_star

        async def _run():
            star = build_star(proactive_enabled=False,
                              character_collection_enabled=True)
            await star.initialize()
            await star._maybe_onboard("u_onboard", "s_onboard")
            marker = await star.get_kv_data("mingbu.onboarded:u_onboard", None)
            return star, marker

        star, marker = asyncio.run(_run())
        assert marker, "onboarding 未写入已引导标记"
        assert star.proactive is None, "本用例前提是 proactive 关闭"
        # 角色分配是 onboarding 的另一半，不应被 proactive 缺失带崩。
        if star.character_collection:
            coll = asyncio.run(
                star.character_collection.get_user_collection("u_onboard"))
            assert coll["unlocked"], (
                "缺 proactive 时初始角色未分配——onboarding 在 proactive 处"
                "崩断，后续步骤全被跳过（毒标记问题复现）"
            )

    def test_onboarding_is_idempotent(self):
        """重复调用不得重复分配/重复发欢迎。"""
        from tests.test_admin_api import build_star

        async def _run():
            star = build_star(proactive_enabled=False)
            await star.initialize()
            await star._maybe_onboard("u_idem", "s_idem")
            first = await star.get_kv_data("mingbu.onboarded:u_idem", None)
            await star._maybe_onboard("u_idem", "s_idem")
            second = await star.get_kv_data("mingbu.onboarded:u_idem", None)
            return first, second

        first, second = asyncio.run(_run())
        assert first == second, "二次 onboarding 覆盖了首次标记，非幂等"
