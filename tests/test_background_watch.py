"""命灵·命卜后台关注单测（"接入 API 后，即可正常后台关注"）。"""
import asyncio
import json
import time

from background_watch import (
    KV_WATCH,
    WATCH_TTL,
    BackgroundWatcher,
    build_divine_watch_phrase,
    build_watch_phrase,
    weave_watch_note,
)


def test_build_watch_phrase_none():
    assert build_watch_phrase("", "", []) is None


def test_build_watch_phrase_term_free():
    note = build_watch_phrase("甲子", "乙丑", ["夫妻宫"])
    assert note is not None
    # 绝不允许出现命理术语
    assert "大运" not in note
    assert "流年" not in note
    assert "八字" not in note
    assert "命盘" not in note
    # 宫位脱"宫"字后应有"夫妻"等模糊指代
    assert "夫妻" in note


def test_build_watch_phrase_with_liuyue():
    note = build_watch_phrase("甲子", "乙丑", ["夫妻宫"], "丙寅")
    assert note is not None
    assert "这个月" in note  # 流月用"这个月"表述
    assert "流月" not in note  # 术语不可出现
    assert "大运" not in note


def test_weave_watch_note_guard():
    w = weave_watch_note("你正处在一段落里——像种子破土前的蓄力")
    assert ("来源不可透露" in w) or ("说不清从哪来" in w)
    assert "大运" not in w


class _FakePlugin:
    def __init__(self):
        self.kv = {}

    async def get_kv_data(self, k, d=None):
        return self.kv.get(k, d)

    async def put_kv_data(self, k, v):
        self.kv[k] = v


def test_note_for_roundtrip_and_ttl():
    p = _FakePlugin()
    bw = BackgroundWatcher(p)
    asyncio.run(p.put_kv_data(
        f"{KV_WATCH}:u1", json.dumps({"note": "留意", "ts": time.time()})))
    assert asyncio.run(bw.note_for("u1")) == "留意"
    # 过期应返回 None
    asyncio.run(p.put_kv_data(
        f"{KV_WATCH}:u2",
        json.dumps({"note": "old", "ts": time.time() - WATCH_TTL - 10})))
    assert asyncio.run(bw.note_for("u2")) is None
    # 缺失返回 None
    assert asyncio.run(bw.note_for("u3")) is None


class _FakeCron:
    def __init__(self):
        self.calls = []

    async def add_basic_job(self, **kw):
        self.calls.append(kw)
        return type("Job", (), {"job_id": kw.get("name", "job") + "-id"})()


class _Ctx:
    def __init__(self):
        self.cron_manager = _FakeCron()


class _FakePluginCron:
    def __init__(self):
        self._config = {}
        self.context = _Ctx()


def test_monthly_cron_default():
    p = _FakePluginCron()
    bw = BackgroundWatcher(p)
    asyncio.run(bw.register_jobs())
    assert p.context.cron_manager.calls[0]["cron_expression"] == "0 7 1 * *"
    assert "每月" in p.context.cron_manager.calls[0]["description"]


class _FakeApi:
    async def combined(self, birth):
        return {"analysis": {
            "bazi": {"daYun": [
                {"ganZhi": "甲子", "startYear": 1990,
                 "liuNian": [{"ganZhi": "丙午", "year": 2026}]}
            ]}
        }}


class _FakeCore:
    def __init__(self):
        self.api = _FakeApi()


class _FakePluginEmotion:
    def __init__(self):
        self.kv = {}
        self._config = {}
        self.core = _FakeCore()

    async def get_kv_data(self, k, d=None):
        return self.kv.get(k, d)

    async def put_kv_data(self, k, v):
        self.kv[k] = v

    async def _get_profile(self, uid):
        return {"birth": {"year": 1990, "month": 5, "day": 1, "hour": 12}}

    def _birth_from(self, profile):
        return profile.get("birth")


def test_maybe_watch_on_emotion_high_weight_triggers():
    p = _FakePluginEmotion()
    bw = BackgroundWatcher(p)
    asyncio.run(bw.maybe_watch_on_emotion("u1", 0.8))  # >= 默认阈值 0.6
    snap = json.loads(p.kv[f"{KV_WATCH}:u1"])
    assert snap["note"]  # 已写入注记
    assert "liu_yue" in snap  # 含流月字段


def test_maybe_watch_on_emotion_low_weight_skipped():
    p = _FakePluginEmotion()
    bw = BackgroundWatcher(p)
    asyncio.run(bw.maybe_watch_on_emotion("u2", 0.3))  # 低于阈值
    assert f"{KV_WATCH}:u2" not in p.kv


def test_maybe_watch_on_emotion_cooldown():
    p = _FakePluginEmotion()
    bw = BackgroundWatcher(p)
    asyncio.run(bw.maybe_watch_on_emotion("u3", 0.8))
    first = p.kv[f"{KV_WATCH}:u3"]
    # 冷却期内再次高权重 → 跳过（注记不变）
    asyncio.run(bw.maybe_watch_on_emotion("u3", 0.8))
    assert p.kv[f"{KV_WATCH}:u3"] == first


def test_maybe_watch_on_emotion_no_birth_skipped():
    p = _FakePluginEmotion()

    async def _no_profile(uid):
        return {}
    p._get_profile = _no_profile

    bw = BackgroundWatcher(p)
    asyncio.run(bw.maybe_watch_on_emotion("u4", 0.8))
    assert f"{KV_WATCH}:u4" not in p.kv


def test_build_watch_phrase_with_daxian():
    note = build_watch_phrase("甲子", "乙丑", ["夫妻宫"], "丙寅", "官禄宫")
    assert note is not None
    assert "这几年" in note  # 大限落宫表述
    assert "大限" not in note  # 术语不可出现


def test_build_divine_watch_phrase_liuyao_term_free():
    # 六爻：本卦乾为天 → 应有弱信号短语，且无任何术语
    chart = {"chart": {"originalGua": {"name": "乾为天"}}}
    note = build_divine_watch_phrase("我要不要换工作", chart, "liuyao")
    assert note
    for term in ("卦", "爻", "星", "门", "神", "六爻", "奇门", "梅花", "大限"):
        assert term not in note, term
    assert "换工作" in note  # 话题应自然带出


def test_build_divine_watch_phrase_qimen_generic():
    # 奇门无本卦 → 通用说法，仍术语无关
    chart = {"chart": {"gongs": [{}]}}
    note = build_divine_watch_phrase("近期境况如何", chart, "qimen")
    assert note
    for term in ("卦", "爻", "星", "门", "神"):
        assert term not in note


def test_build_divine_watch_phrase_empty_chart():
    note = build_divine_watch_phrase("", None, "liuyao")
    assert note
    assert "近况" in note


class _FakeApiFull:
    def __init__(self):
        self.calls = []

    async def combined(self, birth):
        return {"analysis": {
            "bazi": {"daYun": [
                {"ganZhi": "甲子", "startYear": 1990,
                 "liuNian": [{"ganZhi": "丙午", "year": 2026}]}
            ]},
            "ziwei": {"currentAge": 30, "daXians": [
                {"startAge": 20, "endAge": 29, "palaceName": "官禄宫", "ganZhi": "甲子"},
                {"startAge": 30, "endAge": 39, "palaceName": "夫妻宫", "ganZhi": "乙丑"},
            ]}
        }}

    async def liuyao(self, question, question_type="general"):
        self.calls.append(("liuyao", question, question_type))
        return {"chart": {"originalGua": {"name": "乾为天"}}}

    async def qimen(self, year, month, day, hour, minute=0):
        self.calls.append(("qimen", year, month, day, hour, minute))
        return {"chart": {"gongs": [{}]}}


class _FakeCoreFull:
    def __init__(self):
        self.api = _FakeApiFull()


class _FakePluginMethod:
    def __init__(self, method="auto", birth=None, turns=None):
        self.kv = {}
        self._config = {}
        self.core = _FakeCoreFull()
        self._method = method
        self._birth = birth
        self._turns = turns or []

    async def get_kv_data(self, k, d=None):
        return self.kv.get(k, d)

    async def put_kv_data(self, k, v):
        self.kv[k] = v

    async def _get_profile(self, uid):
        prof = {}
        if self._birth:
            prof["birth"] = self._birth
        if self._method != "auto":
            prof["method"] = self._method
        return prof

    def _birth_from(self, profile):
        return profile.get("birth")

    def _method_for(self, uid, profile):
        m = (profile or {}).get("method") or self._config.get(
            "background_watch_method_default", "auto") or "auto"
        if m in ("ziwei", "bazi", "combined", "liuyao", "qimen", "meihua"):
            return m
        if self._birth_from(profile or {}):
            return "combined"
        return "liuyao"

    async def _get_turns(self, uid, n=10):
        return self._turns[-n:]


def test_watch_user_mingpan_combined():
    p = _FakePluginMethod(
        method="ziwei",
        birth={"year": 1990, "month": 5, "day": 1, "hour": 12, "gender": "男"})
    bw = BackgroundWatcher(p)
    asyncio.run(bw.watch_user("u1", p._birth, "ziwei"))
    snap = json.loads(p.kv[f"{KV_WATCH}:u1"])
    assert snap["family"] == "mingpan"
    assert snap["da_yun"] == "甲子"
    assert snap["liu_nian"] == "丙午"
    assert snap["liu_yue"]  # 五虎遁推导
    assert snap["da_xian_palace"] == "夫妻宫"  # 当前年龄30落在第二项
    assert snap["note"]
    assert "大限" not in snap["note"]


def test_watch_user_bugua_liuyao():
    p = _FakePluginMethod(method="liuyao")
    bw = BackgroundWatcher(p)
    asyncio.run(bw.watch_user("u1", None, "liuyao"))
    snap = json.loads(p.kv[f"{KV_WATCH}:u1"])
    assert snap["family"] == "bugua"
    assert snap["method"] == "liuyao"
    assert snap["note"]
    for term in ("卦", "爻", "六爻"):
        assert term not in snap["note"]
    assert p.core.api.calls[0][0] == "liuyao"


def test_watch_user_bugua_qimen():
    p = _FakePluginMethod(method="qimen")
    bw = BackgroundWatcher(p)
    asyncio.run(bw.watch_user("u1", None, "qimen"))
    snap = json.loads(p.kv[f"{KV_WATCH}:u1"])
    assert snap["family"] == "bugua"
    assert p.core.api.calls[0][0] == "qimen"
    # 后端 /api/qimen 的 hour 是「时辰地支索引 0–11」；now.hour 为 0–23 钟点，
    # 必须折算为 branch（回归：曾直传 0–23 导致 ≥12 的钟点非法）
    hour_branch = p.core.api.calls[0][4]
    assert 0 <= hour_branch <= 11


def test_watch_user_bugua_meihua_bridges_liuyao():
    p = _FakePluginMethod(method="meihua")
    bw = BackgroundWatcher(p)
    asyncio.run(bw.watch_user("u1", None, "meihua"))
    snap = json.loads(p.kv[f"{KV_WATCH}:u1"])
    assert snap["method"] == "meihua"
    # 梅花无服务端端点，应桥接 liuyao（而非 qimen）
    assert p.core.api.calls[0][0] == "liuyao"


def test_watch_user_mingpan_no_birth_skipped():
    p = _FakePluginMethod(method="ziwei")  # 无 birth
    bw = BackgroundWatcher(p)
    asyncio.run(bw.watch_user("u1", None, "ziwei"))
    assert f"{KV_WATCH}:u1" not in p.kv


def test_scan_all_branches_by_method():
    p = _FakePluginMethod()
    asyncio.run(p.put_kv_data("mingbu.sessions", json.dumps({
        "s1": {"uid": "u_mingpan"},
        "s2": {"uid": "u_bugua"},
    })))

    async def _get_profile(uid):
        if uid == "u_mingpan":
            return {"birth": {"year": 1990, "month": 1, "day": 1,
                              "hour": 8, "gender": "女"}}
        return {"method": "liuyao"}
    p._get_profile = _get_profile
    bw = BackgroundWatcher(p)
    asyncio.run(bw.scan_all())
    ming = json.loads(p.kv[f"{KV_WATCH}:u_mingpan"])
    bug = json.loads(p.kv[f"{KV_WATCH}:u_bugua"])
    assert ming["family"] == "mingpan"
    assert bug["family"] == "bugua"

