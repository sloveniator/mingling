"""bj_time.py -- 北京时间唯一来源（插件内一律用它取「现在」）
==============================================================
背景（2026-09-11 排查「非北京时区」）：
    容器默认时区可能不是北京——生产实例原先没有 TZ 环境变量，进程本地时区
    就是 UTC（北京 -8h），而插件代码默认「本地时间 = 北京时间」，于是显示、
    睡眠/午休判定、日界、冷却窗口全部推迟 8 小时。
    又因为 AstrBot 核心假定「本地时间 == UTC」（db 层 naive 时间戳按 UTC 解读），
    **不能靠改容器时区来根治**。故插件改为显式取北京时间，对环境零依赖：
    容器 TZ 是 UTC、CST 还是别的，这里取到的都是北京时间。

约定：
    ``now_bj()`` / ``today_bj()`` 返回 **naive**（不带 tzinfo）的北京墙钟，
    与插件历史存储格式（naive isoformat）保持一致，可直接参与
    ``datetime.now()`` 时代的全部差值运算与序列化——因此替换是行为等价的，
    不会改变任何已有数据的格式或比较语义。
    需要 epoch（``.timestamp()``）或与 aware 时间比较时用 ``now_bj_aware()``。

用法：
    from .bj_time import CRON_TZ, now_bj, today_bj
    now_bj().isoformat()                  # 存/读时间戳
    now_bj().hour                         # 时段判定
    add_basic_job(..., timezone=CRON_TZ)  # cron 显式绑定北京时区
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone, tzinfo

try:
    # 生产容器已确认自带 tzdata（/usr/share/zoneinfo/Asia/Shanghai）
    from zoneinfo import ZoneInfo

    BJ_TZ: tzinfo = ZoneInfo("Asia/Shanghai")
except Exception:  # pragma: no cover -- 极端环境退化为固定 UTC+8（中国无夏令时）
    BJ_TZ = timezone(timedelta(hours=8))

#: AstrBot cron 注册用的时区字符串（add_basic_job(timezone=...)）。
#: 不传时 AstrBot 会回退到「调度器默认 = 进程本地时区」，在生产实例上即为 UTC，
#: 导致 dream/growth/decay 等「夜间」任务整体 +8h 跑到白天。
CRON_TZ = "Asia/Shanghai"


def now_bj() -> datetime:
    """当前北京时间（naive 墙钟，与历史存储格式一致）。"""
    return datetime.now(BJ_TZ).replace(tzinfo=None)


def today_bj() -> date:
    """当前北京日期。"""
    return now_bj().date()


def now_bj_aware() -> datetime:
    """当前北京时间（带 +08:00），用于 epoch / 与 aware 时间比较。"""
    return datetime.now(BJ_TZ)
