"""tests/test_bj_time.py -- 北京时间工具 + 「裸本地时间」护栏（v4.9.5）
======================================================================
背景：生产容器原先没有 TZ 环境变量 → 进程本地时区 = UTC，而插件代码默认
「本地时间 = 北京时间」，于是显示、睡眠/午休判定、日界、冷却窗口全部推迟
8 小时；同时 10 个 cron job 因 ``timezone=None`` 回退到调度器默认（= 进程
本地 = UTC），「夜间任务」整体 +8h 跑到白天。

为什么不能靠改容器时区根治：AstrBot 核心假定「本地时间 == UTC」——
``core/db/po.py`` 里 aware-UTC 字段与 ``datetime_utils.normalize_datetime_utc``
（把 naive 按 UTC 解读）都以此为基准。故插件改为**显式**取北京时间，
对环境零依赖。本文件钉死：

1. **环境无关**：把 TZ 改成 UTC/纽约/空，``now_bj()`` 仍是北京时间（核心价值）；
2. **格式兼容**：``now_bj()`` 返回 naive 墙钟，与历史 isoformat 存储格式一致，
   ``datetime.now()`` 时代的差值运算与序列化语义不变（替换是行为等价的）；
3. **aware 变体**：``now_bj_aware()`` 带 +08:00，供 ``.timestamp()`` 使用；
4. **源码护栏**：插件业务代码里不得再出现裸 ``datetime.now()`` / ``date.today()``。
"""
from __future__ import annotations

import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from bj_time import BJ_TZ, CRON_TZ, now_bj, now_bj_aware, today_bj

_ROOT = Path(__file__).resolve().parent.parent
_UTC8 = timedelta(hours=8)

# ``time.tzset()`` 仅在 Unix 可用（生产容器为 Linux）。Windows 上无此 API，
# 无法在进程内切换时区来验证「环境无关性」；该测试的核心价值正是覆盖
# Linux 容器的时区漂移，故在非 Unix 平台跳过（而非改写语义）。
_TZSET_AVAILABLE = hasattr(time, "tzset")
_requires_tzset = pytest.mark.skipif(
    not _TZSET_AVAILABLE,
    reason="time.tzset() 仅 Unix 可用；环境无关性验证依赖进程内切换时区",
)


def _set_tz(value: str | None) -> None:
    if value is None:
        os.environ.pop("TZ", None)
    else:
        os.environ["TZ"] = value
    if _TZSET_AVAILABLE:
        time.tzset()  # type: ignore[attr-defined]


# ── 契约 ───────────────────────────────────────────────────────────────


def test_cron_tz_string_is_beijing():
    assert CRON_TZ == "Asia/Shanghai"


def test_bj_tz_is_utc_plus_8():
    assert datetime(2026, 6, 1, 12, tzinfo=BJ_TZ).utcoffset() == _UTC8


def test_now_bj_is_naive():
    """必须 naive：否则 ``aware - naive`` 会让全部历史差值运算抛 TypeError。"""
    assert now_bj().tzinfo is None


def test_now_bj_is_utc_plus_8():
    delta = (now_bj() - datetime.now(timezone.utc).replace(tzinfo=None)).total_seconds()
    assert abs(delta - 8 * 3600) < 2, f"now_bj 应比 UTC 快 8h，实测 {delta}s"


def test_today_bj_matches_now_bj():
    assert today_bj() == now_bj().date()


def test_now_bj_aware_offset():
    assert now_bj_aware().utcoffset() == _UTC8


# ── 环境无关（本次修复的核心价值） ──────────────────────────────────────


@_requires_tzset
@pytest.mark.parametrize("fake_tz", ["UTC", "America/New_York", "Europe/London", ""])
def test_now_bj_is_env_independent(fake_tz):
    """容器 TZ 是 UTC / 别的 / 空的，取到的都必须是北京时间。"""
    original = os.environ.get("TZ")
    try:
        _set_tz(fake_tz)
        naive = now_bj()
        delta = (naive - datetime.now(timezone.utc).replace(tzinfo=None)).total_seconds()
        assert abs(delta - 8 * 3600) < 2, f"TZ={fake_tz!r} 时 now_bj 偏了 {delta}s"
        assert now_bj_aware().utcoffset() == _UTC8
    finally:
        _set_tz(original)


# ── 源码护栏：不许再退回裸本地时间 ──────────────────────────────────────

_NAIVE_RE = re.compile(r"(?<![\w.])datetime\.now\(\)|(?<![\w.])date\.today\(\)")


def test_no_bare_local_time_in_plugin_source():
    """插件业务代码必须一律走 bj_time，否则「非北京时区」会复发。

    ``bj_time`` 自身（文档里引用旧写法）与 tests/ 豁免；
    纯注释行豁免；``datetime.now(timezone.utc)`` 这类 aware 写法不匹配。
    """
    offenders = []
    for path in sorted(_ROOT.rglob("*.py")):
        parts = path.relative_to(_ROOT).parts
        if "tests" in parts or path.name == "bj_time.py":
            continue
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if line.lstrip().startswith("#"):
                continue
            if _NAIVE_RE.search(line):
                offenders.append(f"{path.relative_to(_ROOT)}:{lineno}")
    assert not offenders, (
        "插件里仍有裸本地时间调用（进程时区不是北京时会错 8 小时），"
        f"请改用 bj_time.now_bj()/today_bj(): {offenders}"
    )
