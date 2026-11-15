"""
kv_cache.py -- 写回缓存的「防抖落盘」调度器（去重通用件）
========================================================
`memory_core` 与 `conversation_inspector` 各自实现了一遍「脏标记 + 防抖落盘任务」
的机械逻辑（建任务、去重、到点调用 flush、flush_all 时取消在途任务、任务结束自清理）。
三处写法几乎一致，却各抄了一遍，是报告里点名的「第三处、第四处再抄一遍」风险。

本模块把**机械部分**抽成 `WriteBehindScheduler`：实际的序列化与写回仍由各模块
自己负责（两者的缓存结构/落盘语义不同），调度器只管：
  - `schedule(key)`：安排一次防抖落盘，同一 key 不重复排任务；
  - 防抖到点后调用 `flush_fn(key)`；
  - `cancel_all()`：flush_all 时取消所有在途任务（避免 terminate 时重复写/竞争）；
  - 任务结束自行从表里清理。

防抖时长通过 `debounce_fn()` 延迟取值，因此运行时改 `FLUSH_DEBOUNCE_SEC`
（测试里常这么做）能立即生效，与改造前行为一致。
"""
from __future__ import annotations

import asyncio
from typing import Awaitable, Callable

from astrbot.api import logger

# flush_fn: 给定 key，执行一次真正的落盘
FlushFn = Callable[[str], Awaitable[None]]
# debounce_fn: 返回当前防抖秒数（延迟取值，允许运行时调整）
DebounceFn = Callable[[], float]


class WriteBehindScheduler:
    """管理「脏标记后的防抖落盘任务」生命周期。"""

    def __init__(self, flush_fn: FlushFn, debounce_fn: DebounceFn):
        self._flush_fn = flush_fn
        self._debounce_fn = debounce_fn
        self._tasks: dict[str, "asyncio.Task"] = {}

    def schedule(self, key: str) -> None:
        """安排一次防抖落盘；若该 key 已有在途任务则跳过（去重）。"""
        task = self._tasks.get(key)
        if task and not task.done():
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        self._tasks[key] = loop.create_task(self._debounced(key))

    async def _debounced(self, key: str) -> None:
        try:
            await asyncio.sleep(self._debounce_fn())
            await self._flush_fn(key)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning(f"[wb] 防抖落盘失败({key}): {e}")
        finally:
            self._tasks.pop(key, None)

    def cancel_all(self) -> None:
        """取消所有在途防抖任务（flush_all 落盘前调用）。"""
        for task in list(self._tasks.values()):
            task.cancel()
        self._tasks.clear()
