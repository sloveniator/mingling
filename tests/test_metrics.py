"""tests/test_metrics.py -- 关键路径故障计数器
================================================
验证 record_failure / get_failure_counts 行为，确保 api_stats 暴露的
`failures` 字段来源可靠。原先这些路径是 `except Exception: pass` 静默吞掉，
现在至少能在管理面板里看见计数。
"""
from metrics import get_failure_counts, record_failure, reset_failure_counts


def test_record_failure_increments():
    reset_failure_counts()
    assert get_failure_counts()["conversation_log"] == 0
    record_failure("conversation_log")
    record_failure("conversation_log")
    assert get_failure_counts()["conversation_log"] == 2


def test_unknown_name_is_registered_on_demand():
    reset_failure_counts()
    record_failure("some_new_path")
    assert get_failure_counts()["some_new_path"] == 1


def test_get_failure_counts_returns_independent_snapshot():
    reset_failure_counts()
    snap = get_failure_counts()
    record_failure("memory_write")
    # 快照不应被后续写入改变
    assert snap["memory_write"] == 0
    assert get_failure_counts()["memory_write"] == 1


def test_reset_clears_counts():
    reset_failure_counts()
    record_failure("slang_learn")
    reset_failure_counts()
    assert get_failure_counts()["slang_learn"] == 0
