"""
metrics.py -- 关键路径故障计数器（可观测性）
==========================================
此前大量 `except Exception: pass` 把真实故障静默吞掉（本次 P0 记忆写入链路断裂
就是这么藏了很久）。这里提供一组轻量计数器：关键路径捕获到异常时，既降级为
`logger.debug(..., exc_info=True)` 保留现场，又调用 `record_failure(name)`
累加计数，最终在管理面板 `api_stats` 的 `failures` 字段暴露出来，让「静默失败」
变成「看得见、可告警」。

用法：
    from metrics import record_failure, get_failure_counts
    try:
        await self.conversation_inspector.log(...)
    except Exception as e:
        logger.debug(f"[mingbu] 对话记录失败(已忽略): {e}", exc_info=True)
        record_failure("conversation_log")
"""
from __future__ import annotations

from astrbot.api import logger

# 已知关键路径的故障计数。新增路径时在此登记 name 即可。
_FAILED: dict[str, int] = {
    "conversation_log": 0,          # 对话记录写入失败
    "memory_write": 0,              # 情景/情感记忆写入失败
    "background_watch_emotion": 0,  # 后台关注·情绪触发失败
    "slang_learn": 0,               # 群 slang 候选学习失败
    "watch_note_inject": 0,         # 后台关注注记注入失败
}


def record_failure(name: str) -> None:
    """累加某关键路径的故障计数。"""
    _FAILED[name] = _FAILED.get(name, 0) + 1
    logger.debug(f"[metrics] 关键路径故障 +1: {name} (累计 {_FAILED[name]})")


def get_failure_counts() -> dict[str, int]:
    """返回当前各路径的故障计数快照。"""
    return dict(_FAILED)


def reset_failure_counts() -> None:
    """测试用：清空所有计数。"""
    for k in _FAILED:
        _FAILED[k] = 0
