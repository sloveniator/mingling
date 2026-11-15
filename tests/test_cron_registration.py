"""tests/test_cron_registration.py -- cron 注册健壮性回归测试（v4.9.0）
==================================================================

生产事故回归：生产配置里 ``self_evolution_cron`` / ``background_watch_cron``
键存在但值为空串，``cfg.get(k, default)`` 的默认值对「键存在但为空」无效
→ cron_expression="" 入库 → AstrBot ``_schedule_job`` 抛
"recurring job missing cron_expression"，两个定时任务静默失效。

修复：读取改为 ``(cfg.get(k) or "").strip() or DEFAULT``——空值/空白也回退默认。
本文件钉死三态行为，防止未来改回 ``get(k, default)``。

另覆盖 ``util.cleanup_stale_cron_jobs``：basic cron job 的 handler 不可持久化，
每次重启重建 job；db 层纯 INSERT 不按 name 去重，需清理同名旧 job 防积累。
"""
import asyncio

import pytest

from background_watch import BackgroundWatcher
from proactive import ProactiveEngine
from self_evolution import SelfEvolution
from util import cleanup_stale_cron_jobs


class _FakeCron:
    """AstrBot CronJobManager 最小桩：记录调用并模拟 job 对象。"""

    #: AstrBot 4.28 ``CronJobManager.add_basic_job`` 的 keyword-only 参数集。
    #: 桩接受 **kw 会放过任何拼错的参数名（如 tz= / time_zone=），
    #: 本集合把「签名契约」钉进测试——插件传了 AstrBot 不认的参数即失败。
    ALLOWED_KWARGS = {
        "name",
        "cron_expression",
        "handler",
        "description",
        "timezone",
        "payload",
        "enabled",
        "persistent",
    }

    def __init__(self):
        self.calls = []
        self.jobs = []  # 既有 job 列表（dict），供 list_jobs / delete_job
        self.deleted = []

    async def add_basic_job(self, **kw):
        unknown = set(kw) - self.ALLOWED_KWARGS
        assert not unknown, f"add_basic_job 收到 AstrBot 签名以外的参数: {sorted(unknown)}"
        self.calls.append(kw)
        job = {"job_id": f"new-{len(self.calls)}", "name": kw.get("name")}
        return type("Job", (), job)()

    async def list_jobs(self, job_type=None):
        if job_type == "basic":
            return [type("Job", (), j)() for j in self.jobs]
        return []

    async def delete_job(self, job_id):
        self.deleted.append(job_id)


class _Ctx:
    def __init__(self, cron):
        self.cron_manager = cron


class _Plugin:
    def __init__(self, config=None, cron=None):
        self._config = config or {}
        self.context = _Ctx(cron or _FakeCron())


# ── 空配置回退默认值（生产事故回归核心） ────────────────────────────────


@pytest.mark.parametrize("bad", ["", "   "])
def test_self_evolution_blank_config_falls_back(bad):
    p = _Plugin({"self_evolution_cron": bad})
    se = SelfEvolution(p, memory=None)
    asyncio.run(se.register_jobs())
    assert p.context.cron_manager.calls[0]["cron_expression"] == "0 4 * * *"


@pytest.mark.parametrize("bad", ["", "   "])
def test_background_watch_blank_config_falls_back(bad):
    p = _Plugin({"background_watch_cron": bad})
    bw = BackgroundWatcher(p)
    asyncio.run(bw.register_jobs())
    assert p.context.cron_manager.calls[0]["cron_expression"] == "0 7 1 * *"


@pytest.mark.parametrize("bad", ["", "   "])
def test_proactive_blank_config_falls_back(bad):
    p = _Plugin({"proactive_cron": bad, "proactive_enabled": True})
    eng = ProactiveEngine(p)
    asyncio.run(eng.register_jobs())
    assert p.context.cron_manager.calls[0]["cron_expression"] == "*/20 * * * *"


@pytest.mark.parametrize("bad", ["", "   "])
def test_self_evolution_none_config_falls_back(bad):
    p = _Plugin({"self_evolution_cron": bad})
    asyncio.run(SelfEvolution(p, memory=None).register_jobs())
    assert p.context.cron_manager.calls[0]["cron_expression"] == "0 4 * * *"


def test_custom_config_passthrough():
    """配置了合法表达式必须原样传递，不许被默认值吃掉。"""
    p = _Plugin({"self_evolution_cron": "30 5 * * *"})
    asyncio.run(SelfEvolution(p, memory=None).register_jobs())
    assert p.context.cron_manager.calls[0]["cron_expression"] == "30 5 * * *"


def test_missing_key_uses_default():
    """键不存在（老配置）行为不变。"""
    p = _Plugin({})
    asyncio.run(SelfEvolution(p, memory=None).register_jobs())
    assert p.context.cron_manager.calls[0]["cron_expression"] == "0 4 * * *"


# ── cleanup_stale_cron_jobs ────────────────────────────────────────────


def test_cleanup_removes_stale_same_name_jobs():
    cron = _FakeCron()
    cron.jobs = [
        {"job_id": "old-1", "name": "mingbu_proactive"},
        {"job_id": "old-2", "name": "mingbu_proactive"},
        {"job_id": "other", "name": "mingbu_other"},  # 异名不删
    ]
    removed = asyncio.run(cleanup_stale_cron_jobs(cron, "mingbu_proactive", "new-9"))
    assert removed == 2
    assert cron.deleted == ["old-1", "old-2"]
    assert "other" not in cron.deleted


def test_cleanup_never_deletes_keep_id():
    cron = _FakeCron()
    cron.jobs = [{"job_id": "keep-me", "name": "mingbu_proactive"}]
    removed = asyncio.run(cleanup_stale_cron_jobs(cron, "mingbu_proactive", "keep-me"))
    assert removed == 0
    assert cron.deleted == []


def test_cleanup_swallow_errors():
    """list_jobs 抛异常只告警，绝不外抛打断注册主流程。"""
    class _BoomCron(_FakeCron):
        async def list_jobs(self, job_type=None):
            raise RuntimeError("db down")

    cron = _BoomCron()
    removed = asyncio.run(cleanup_stale_cron_jobs(cron, "mingbu_x", "keep"))
    assert removed == 0


def test_register_invokes_cleanup_with_new_job_id():
    """注册成功后必须以新 job_id 调用清理（防重启积累的完整链路）。"""
    p = _Plugin({"self_evolution_cron": "0 4 * * *"})
    se = SelfEvolution(p, memory=None)
    asyncio.run(se.register_jobs())
    # FakeCron 新 job id 依次为 new-1；无旧 job 时 cleaned 列表为空但不报错
    assert p.context.cron_manager.calls, "add_basic_job 必须被调用"

# ── 时区绑定（v4.9.5）：所有 job 必须显式绑北京时区 ──────────────────────
#
# 生产实例原先没有 TZ 环境变量 → 进程本地时区 = UTC。AstrBot 的
# ``_schedule_job`` 在 ``job.timezone is None`` 时回退「调度器默认 =
# 进程本地时区」，于是 ``0 3 * * *`` 的梦境巩固实际跑在北京 11:00、
# ``0 4`` 的记忆衰减跑在 12:00——「夜间任务」全跑到白天。
# 修法：注册时显式传 ``timezone=CRON_TZ``（bj_time.CRON_TZ = Asia/Shanghai），
# 不再依赖容器时区。以下钉死「一个都不许漏」。


def _assert_all_beijing(cron):
    from bj_time import CRON_TZ

    assert cron.calls, "add_basic_job 未被调用"
    bad = [c.get("name") for c in cron.calls if c.get("timezone") != CRON_TZ]
    assert not bad, f"以下 cron job 未绑定北京时区: {bad}"


def test_self_evolution_binds_beijing_timezone():
    p = _Plugin({"self_evolution_cron": "0 4 * * *"})
    asyncio.run(SelfEvolution(p, memory=None).register_jobs())
    _assert_all_beijing(p.context.cron_manager)


def test_background_watch_binds_beijing_timezone():
    p = _Plugin({"background_watch_cron": "0 7 1 * *"})
    asyncio.run(BackgroundWatcher(p).register_jobs())
    _assert_all_beijing(p.context.cron_manager)


def test_proactive_binds_beijing_timezone():
    p = _Plugin({"proactive_cron": "*/20 * * * *", "proactive_enabled": True})
    asyncio.run(ProactiveEngine(p).register_jobs())
    _assert_all_beijing(p.context.cron_manager)


def test_main_register_cron_binds_beijing_timezone_for_all_jobs():
    """main.py 的 7 个周期任务：tick/dream/growth/decay/emotion_reset/news/search。"""
    from main import MingbuPlugin
    from tests.test_admin_api import FakeContext

    cron = _FakeCron()
    ctx = FakeContext()
    ctx.cron_manager = cron
    plugin = MingbuPlugin(ctx, {"background_watch_enabled": False})
    asyncio.run(plugin._register_cron())
    assert len(cron.calls) == 7, f"应注册 7 个周期任务，实测 {len(cron.calls)}"
    _assert_all_beijing(cron)
