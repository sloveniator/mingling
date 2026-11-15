"""验证 LLM 工具 handler 已内联到入口模块 main.py，并正确打上 LLMTool 入口标记。

根因：AstrBot 只实例化并识别入口模块（main.py）的 @filter handler，兄弟子模块
stars/tools.py 里的 @filter.llm_tool 从未被绑定（此前的 _with_self/_STAR_INSTANCE
适配器只绕过了实例化问题，工具仍未在 AstrBot 里正确登记）。

修复：10 个 LLM 工具方法内联迁入 main.py 的 MingbuPlugin，直接 @filter.llm_tool 装饰，
AstrBot 会以 functools.partial 把入口实例绑定为 self，工具即可正常派发。
"""

LLM_TOOLS = [
    "query_bazi",
    "query_ziwei",
    "divination_liuyao",
    "query_qimen",
    "query_combined",
    "query_situation",
    "guided_reflection",
    "divination_tarot",
    "divination_zizhan",
    "health_advice",
    "psychological_counsel",
]


def _entry_type(method):
    return getattr(method, "__astrbot_event_type__", None)


def test_all_llm_tools_inline_in_main():
    from main import MingbuPlugin

    for name in LLM_TOOLS:
        method = getattr(MingbuPlugin, name, None)
        assert method is not None, f"main.MingbuPlugin 缺少 LLM 工具方法 {name}"


def test_llm_tools_carry_llmtool_marker():
    from main import MingbuPlugin

    for name in LLM_TOOLS:
        method = getattr(MingbuPlugin, name)
        assert _entry_type(method) == "LLMTool", (
            f"{name} 未被注册为 LLM 工具（__astrbot_event_type__={_entry_type(method)!r}）"
        )


def test_llm_tool_binds_to_plugin_instance():
    from main import MingbuPlugin
    from tests.test_admin_api import FakeContext

    plugin = MingbuPlugin(FakeContext(), {"background_watch_enabled": False})
    # 工具方法绑定到入口实例后，能访问共享内核 api（经 __getattr__ 代理到 core 单例）
    bound = MingbuPlugin.query_bazi.__get__(plugin)
    assert bound.__self__ is plugin
    assert plugin.api is not None


def test_llm_tool_does_not_leak_internal_prompt():
    """占卜工具返回内容不应泄露内部「朋友式解读」引导 prompt（counseling_frame 等）。

    历史 bug：divination_liuyao 等工具在用户有情绪信号时，把
    CounselingPrompts.counseling_frame 生成的「请以朋友的方式…/疗法指引/朋友边界」
    这段本该喂给 LLM 的内部指令直接拼进 yield event.plain_result 发给用户。
    （v4.8.61 起工具改为 return 结果字符串交给 LLM 整合，本测试用 await 取返回值。）
    """
    import asyncio

    import core as core_mod
    from main import MingbuPlugin
    from tests.test_admin_api import FakeContext

    core_mod._CORE = None
    plugin = MingbuPlugin(FakeContext(), {"background_watch_enabled": False})

    class _FakeApi:
        async def liuyao(self, question, question_type):
            return {"chart": {"originalGua": {"name": "天雷无妄"},
                              "question": question, "lines": []}}

        async def techniques(self, tech_type, terms, top):
            return {"entries": []}

    class _FakeTracker:
        def get_emotion_summary(self, uid):
            return "迷茫和不确定(1次)"  # 非空 → 原实现会走 counseling_frame 分支
        def exploration_completed(self, uid, result):
            pass

    plugin.api = _FakeApi()
    plugin.tracker = _FakeTracker()

    class _FakeEvent:
        def get_sender_id(self):
            return "u1"

    text = asyncio.run(plugin.divination_liuyao(_FakeEvent(), "该不该换工作"))
    assert "天雷无妄" in text
    for kw in ("朋友陪你解读", "疗法指引", "朋友边界", "请以朋友的方式"):
        assert kw not in text, f"工具输出泄露了内部 prompt 关键词：{kw}"


def test_liuyao_grounds_with_local_technique_kb():
    """六爻占卜工具应附本地六爻技法库（断卦法则 + 八卦类象 + 动爻提示）。"""
    import asyncio

    import core as core_mod
    from main import MingbuPlugin
    from tests.test_admin_api import FakeContext

    core_mod._CORE = None
    plugin = MingbuPlugin(FakeContext(), {"background_watch_enabled": False})

    class _FakeApi:
        async def liuyao(self, question, question_type):
            return {"chart": {
                "originalGua": {"name": "天雷无妄"},
                "changedGua": {"name": "火泽睽"},
                "lines": [
                    {"position": 1, "positionName": "初爻", "liuQin": "父母", "liuShen": "玄武", "moving": False},
                    {"position": 4, "positionName": "四爻", "liuQin": "妻财", "liuShen": "青龙", "moving": True},
                ],
                "question": question,
            }}

    class _FakeTracker:
        def get_emotion_summary(self, uid):
            return ""
        def exploration_completed(self, uid, result):
            pass

    plugin.api = _FakeApi()
    plugin.tracker = _FakeTracker()

    class _FakeEvent:
        def get_sender_id(self):
            return "u1"

    text = asyncio.run(plugin.divination_liuyao(_FakeEvent(), "该不该换工作"))

    # 本地六爻技法库 + 八卦类象 + 动爻提示
    assert "六爻技法" in text
    assert "断卦法则" in text
    assert "世应" in text and "用神" in text and "六神" in text
    assert "八卦类象" in text
    assert "天雷无妄" in text          # 卦象本身
    assert "妻财" in text              # 动爻六亲提示
    # 不再依赖 /api/techniques 的空转检索
    assert "命理技法依据" not in text


def test_liuyao_technique_grounding_is_fault_tolerant():
    """本地六爻技法库对空/异常卦象应优雅降级、不破坏主链路。"""
    import asyncio

    import core as core_mod
    from main import MingbuPlugin
    from tests.test_admin_api import FakeContext

    core_mod._CORE = None
    plugin = MingbuPlugin(FakeContext(), {"background_watch_enabled": False})

    class _FakeApiNoGua:
        async def liuyao(self, question, question_type):
            return {"chart": {"originalGua": {}, "lines": []}}

    class _FakeTracker:
        def get_emotion_summary(self, uid):
            return ""
        def exploration_completed(self, uid, result):
            pass

    plugin.api = _FakeApiNoGua()
    plugin.tracker = _FakeTracker()

    class _FakeEvent:
        def get_sender_id(self):
            return "u1"

    text = asyncio.run(plugin.divination_liuyao(_FakeEvent(), "该不该换工作"))
    assert "本卦" in text              # format_liuyao 正常输出
    assert "六爻技法" not in text      # 空卦名 → 技法库静默跳过



# ============================================================
# 占卜工具防重复起卦（v4.8.115 新增）
# 背景：AstrBot tool_loop 下 LLM 可能对同一问题反复调用随机起卦类工具
# （六爻/奇门/塔罗），每次结果不同导致卦象混乱 + 平台配额浪费。
# 修复：同用户同问题在 TTL 内重复调用直接返回上次结果（一卦一占）。
# ============================================================


def test_divination_liuyao_dedup_returns_same_chart():
    """同一用户同一问题在 TTL 内重复调用六爻：第二次不重新起卦，返回上次卦象。"""
    import asyncio

    import core as core_mod
    from main import MingbuPlugin
    from tests.test_admin_api import FakeContext

    core_mod._CORE = None
    plugin = MingbuPlugin(FakeContext(), {"background_watch_enabled": False})

    call_count = {"n": 0}

    class _FakeApi:
        async def liuyao(self, question, question_type):
            call_count["n"] += 1
            return {"chart": {"originalGua": {"name": "水火既济"},
                              "question": question, "lines": []}}

    class _FakeTracker:
        def get_emotion_summary(self, uid):
            return ""
        def exploration_completed(self, uid, result):
            pass

    plugin.api = _FakeApi()
    plugin.tracker = _FakeTracker()

    class _FakeEvent:
        def get_sender_id(self):
            return "u1"

    ev = _FakeEvent()
    first = asyncio.run(plugin.divination_liuyao(ev, "下半年工作运势如何", "career"))
    second = asyncio.run(plugin.divination_liuyao(ev, "下半年工作运势如何", "career"))

    assert call_count["n"] == 1, "TTL 内重复调用不应再次请求平台起卦"
    assert first == second, "重复调用应返回同一卦象文本"
    assert "水火既济" in second


def test_divination_liuyao_dedup_different_question_not_cached():
    """不同问题（或不同用户）不受防重影响，仍正常重新起卦。"""
    import asyncio

    import core as core_mod
    from main import MingbuPlugin
    from tests.test_admin_api import FakeContext

    core_mod._CORE = None
    plugin = MingbuPlugin(FakeContext(), {"background_watch_enabled": False})

    call_count = {"n": 0}

    class _FakeApi:
        async def liuyao(self, question, question_type):
            call_count["n"] += 1
            return {"chart": {"originalGua": {"name": "离为火"},
                              "question": question, "lines": []}}

    class _FakeTracker:
        def get_emotion_summary(self, uid):
            return ""
        def exploration_completed(self, uid, result):
            pass

    plugin.api = _FakeApi()
    plugin.tracker = _FakeTracker()

    class _FakeEvent:
        def __init__(self, uid):
            self._uid = uid
        def get_sender_id(self):
            return self._uid

    ev1 = _FakeEvent("u1")
    asyncio.run(plugin.divination_liuyao(ev1, "问 A", "general"))
    asyncio.run(plugin.divination_liuyao(ev1, "问 B", "general"))
    asyncio.run(plugin.divination_liuyao(_FakeEvent("u2"), "问 A", "general"))

    assert call_count["n"] == 3, "不同问题/不同用户应各自重新起卦"


def test_divination_tarot_dedup_returns_same_spread():
    """塔罗抽牌同样防重：同用户同问题 TTL 内重复调用沿用上次牌面。"""
    import asyncio

    import core as core_mod
    from main import MingbuPlugin
    from tests.test_admin_api import FakeContext

    core_mod._CORE = None
    plugin = MingbuPlugin(FakeContext(), {"background_watch_enabled": False,
                                          "tarot_enabled": True})

    class _FakeTracker:
        def get_emotion_summary(self, uid):
            return ""
        def exploration_completed(self, uid, result):
            pass

    plugin.tracker = _FakeTracker()

    class _FakeEvent:
        def get_sender_id(self):
            return "u1"

    ev = _FakeEvent()
    first = asyncio.run(plugin.divination_tarot(ev, "感情发展", "love"))
    second = asyncio.run(plugin.divination_tarot(ev, "感情发展", "love"))

    assert first == second, "TTL 内重复调用应返回同一牌面"


def test_divination_qimen_dedup_returns_same_result():
    """奇门起局同样防重：同用户同问题 TTL 内重复调用沿用上次局象。"""
    import asyncio

    import core as core_mod
    from main import MingbuPlugin
    from tests.test_admin_api import FakeContext

    core_mod._CORE = None
    plugin = MingbuPlugin(FakeContext(), {"background_watch_enabled": False})

    call_count = {"n": 0}

    class _FakeApi:
        async def qimen(self, year, month, day, hour, minute):
            call_count["n"] += 1
            return {"chart": {"ju": "阳遁三局", "question": "合作"}}

    class _FakeTracker:
        def get_emotion_summary(self, uid):
            return ""
        def exploration_completed(self, uid, result):
            pass

    plugin.api = _FakeApi()
    plugin.tracker = _FakeTracker()

    class _FakeEvent:
        def get_sender_id(self):
            return "u1"

    ev = _FakeEvent()
    asyncio.run(plugin.query_qimen(ev, "这次合作能成吗"))
    second = asyncio.run(plugin.query_qimen(ev, "这次合作能成吗"))

    assert call_count["n"] == 1, "TTL 内重复调用不应再次请求平台起局"
    assert "这次合作能成吗" in second


# ============================================================
# 紫占工具 + 出生校验 + 探索完成标记（v4.8.116 新增）
# ============================================================


def test_divination_zizhan_dedup_returns_same_chart():
    """紫占防重：同用户同问题 TTL 内重复调用沿用上次卦象（紫占以当下时空起盘，
    重复起盘会得到不同卦象，与六爻/奇门/塔罗同理）。"""
    import asyncio

    import core as core_mod
    from main import MingbuPlugin
    from tests.test_admin_api import FakeContext

    core_mod._CORE = None
    plugin = MingbuPlugin(FakeContext(), {"background_watch_enabled": False})

    call_count = {"n": 0}

    class _FakeApi:
        async def zizhan(self, question, mode):
            call_count["n"] += 1
            return {
                "birthInfo": {"year": 2026, "month": 8, "day": 21,
                              "hour": 5, "timeIndex": 5, "gender": "male",
                              "name": "紫占·当下时空盘"},
                "threeLayer": {
                    "direction": "顺",
                    "main": {"name": "命宫", "summary": "紫微"},
                    "layer2": {"name": "夫妻宫", "summary": "天机"},
                    "layer3": {"name": "财帛宫", "summary": "太阳"},
                    "shiChenBranch": 5,
                    "elapsedMin": 30,
                },
                "prompt": "占断指引：以主卦命宫为体…",
            }

    class _FakeTracker:
        def get_emotion_summary(self, uid):
            return ""
        def exploration_completed(self, uid, result):
            pass

    plugin.api = _FakeApi()
    plugin.tracker = _FakeTracker()

    class _FakeEvent:
        def get_sender_id(self):
            return "u1"

    ev = _FakeEvent()
    first = asyncio.run(plugin.divination_zizhan(ev, "近期财运如何"))
    second = asyncio.run(plugin.divination_zizhan(ev, "近期财运如何"))

    assert call_count["n"] == 1, "TTL 内重复调用不应再次请求平台起盘"
    assert first == second, "重复调用应返回同一卦象文本"
    assert "紫占" in second


def test_divination_zizhan_different_question_not_cached():
    """紫占防重按问题隔离：新问题重新起盘。"""
    import asyncio

    import core as core_mod
    from main import MingbuPlugin
    from tests.test_admin_api import FakeContext

    core_mod._CORE = None
    plugin = MingbuPlugin(FakeContext(), {"background_watch_enabled": False})

    call_count = {"n": 0}

    class _FakeApi:
        async def zizhan(self, question, mode):
            call_count["n"] += 1
            return {"threeLayer": {"main": {"name": "命宫", "summary": "紫微"}},
                    "prompt": "…"}

    class _FakeTracker:
        def get_emotion_summary(self, uid):
            return ""
        def exploration_completed(self, uid, result):
            pass

    plugin.api = _FakeApi()
    plugin.tracker = _FakeTracker()

    class _FakeEvent:
        def get_sender_id(self):
            return "u1"

    ev = _FakeEvent()
    asyncio.run(plugin.divination_zizhan(ev, "问 A"))
    asyncio.run(plugin.divination_zizhan(ev, "问 B"))

    assert call_count["n"] == 2, "不同问题应各自重新起盘"


def test_birth_validation_rejects_invalid_date():
    """出生校验接线：非法日期（如 2 月 30 日）被工具链拦截，不再落到后端。"""
    import asyncio

    import core as core_mod
    from main import MingbuPlugin
    from tests.test_admin_api import FakeContext

    core_mod._CORE = None
    plugin = MingbuPlugin(FakeContext(), {"background_watch_enabled": False})

    called = {"n": 0}

    class _FakeApi:
        async def bazi(self, birth):
            called["n"] += 1
            return {"chart": {}}

    class _FakeTracker:
        def exploration_completed(self, uid, result):
            pass

    plugin.api = _FakeApi()
    plugin.tracker = _FakeTracker()

    class _FakeEvent:
        def get_sender_id(self):
            return "u1"
        def get_sender_name(self):
            return ""

    text = asyncio.run(plugin.query_bazi(_FakeEvent(), 1993, 2, 30, 5, "male"))
    assert called["n"] == 0, "非法日期不应调用后端排盘"
    assert "只有" in text and "不合法" in text


def test_birth_validation_accepts_leap_day():
    """出生校验：闰年 2 月 29 日合法，正常放行。"""
    import asyncio

    import core as core_mod
    from main import MingbuPlugin
    from tests.test_admin_api import FakeContext

    core_mod._CORE = None
    plugin = MingbuPlugin(FakeContext(), {"background_watch_enabled": False})

    called = {"n": 0}

    class _FakeApi:
        async def bazi(self, birth):
            called["n"] += 1
            return {"chart": {"birthInfo": birth}}

    class _FakeTracker:
        def exploration_completed(self, uid, result):
            pass

    plugin.api = _FakeApi()
    plugin.tracker = _FakeTracker()

    class _FakeEvent:
        def get_sender_id(self):
            return "u1"
        def get_sender_name(self):
            return ""

    asyncio.run(plugin.query_bazi(_FakeEvent(), 2000, 2, 29, 12, "female"))
    assert called["n"] == 1, "合法闰日应正常排盘"


def test_query_bazi_marks_exploration_completed():
    """八字排盘后应标记探索完成（与合参/六爻/奇门一致）。

    历史缺口：query_bazi / query_ziwei 未调用 tracker.exploration_completed，
    导致排盘后 tracker 状态不更新、divination_cache 不写入，祝福钩子拿不到命盘数据。
    """
    import asyncio

    import core as core_mod
    from main import MingbuPlugin
    from tests.test_admin_api import FakeContext

    core_mod._CORE = None
    plugin = MingbuPlugin(FakeContext(), {"background_watch_enabled": False})

    completed = {"n": 0, "uid": None, "result": None}

    class _FakeApi:
        async def bazi(self, birth):
            return {"chart": {"birthInfo": birth}}

    class _FakeTracker:
        def exploration_completed(self, uid, result):
            completed["n"] += 1
            completed["uid"] = uid
            completed["result"] = result

    plugin.api = _FakeApi()
    plugin.tracker = _FakeTracker()

    class _FakeEvent:
        def get_sender_id(self):
            return "u1"
        def get_sender_name(self):
            return ""

    asyncio.run(plugin.query_bazi(_FakeEvent(), 1993, 7, 15, 5, "male"))
    assert completed["n"] == 1, "八字排盘后应标记探索完成"
    assert completed["uid"] == "u1"
    assert completed["result"] is not None


def test_query_ziwei_marks_exploration_completed():
    """紫微排盘后应标记探索完成（同八字，历史缺口修复）。"""
    import asyncio

    import core as core_mod
    from main import MingbuPlugin
    from tests.test_admin_api import FakeContext

    core_mod._CORE = None
    plugin = MingbuPlugin(FakeContext(), {"background_watch_enabled": False})

    completed = {"n": 0}

    class _FakeApi:
        async def combined(self, birth):
            return {"analysis": {"ziwei": {"birthInfo": birth}}}

    class _FakeTracker:
        def exploration_completed(self, uid, result):
            completed["n"] += 1

    plugin.api = _FakeApi()
    plugin.tracker = _FakeTracker()

    class _FakeEvent:
        def get_sender_id(self):
            return "u1"
        def get_sender_name(self):
            return ""

    asyncio.run(plugin.query_ziwei(_FakeEvent(), 1993, 7, 15, 5, "male"))
    assert completed["n"] == 1, "紫微排盘后应标记探索完成"
