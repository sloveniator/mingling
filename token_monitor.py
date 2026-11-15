"""
token_monitor.py -- Token 用量监控
==================================
记录插件内部任务的调用次数、Token 消耗、失败记录、cached_tokens，
支持每日限额和每日统计。
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import timedelta
from typing import Any, Dict

from astrbot.api import logger

from .bj_time import now_bj

KV_TOKEN_STATS = "mingbu.token_stats"
KV_TOKEN_DAILY = "mingbu.token_daily:{date}"


@dataclass
class DailyStats:
    """单日用量统计。"""
    calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cached_tokens: int = 0
    failures: int = 0
    # 任务级当日拆分（2026-09-22 P2）：{task_type: {calls/prompt_tokens/...}}
    # 存 plain dict，兼容旧记录（无该键时默认空）
    tasks: Dict[str, Dict[str, int]] = field(default_factory=dict)


@dataclass
class TaskStats:
    """单任务类型用量统计。"""
    calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cached_tokens: int = 0
    failures: int = 0


@dataclass
class MonitorStats:
    """全局用量统计。"""
    total_calls: int = 0
    total_prompt_tokens: int = 0
    total_completion_tokens: int = 0
    total_cached_tokens: int = 0
    total_failures: int = 0
    by_task: Dict[str, TaskStats] = field(default_factory=dict)


def _int(val: Any, default: int = 0) -> int:
    """安全转 int，失败返回默认值。"""
    try:
        return int(val) if val is not None else default
    except (ValueError, TypeError):
        return default


def _extract_usage(resp, prompt: str = "") -> Dict[str, int]:
    """从 AstrBot LLM 响应对象中提取 usage 信息。

    2026-09-22 修复：AstrBot 4.x 的 ``LLMResponse.usage`` 是 ``TokenUsage``
    （字段 input_other / input_cached / output），旧实现只认 prompt_tokens /
    completion_tokens，导致计数恒为 0、daily_token_limit 形同虚设。
    现在依次尝试：TokenUsage 风格 → dict → raw_completion.usage；
    全部缺失时按字符量估算（中文约 2 字符/token），保证限额闸门始终有数可算。
    """
    def _from_obj(obj):
        p = _int(getattr(obj, "prompt_tokens", 0))
        if not p:
            # AstrBot TokenUsage：input = input_other + input_cached
            # （property 缺失时按字段求和，双保险）
            p = _int(getattr(obj, "input", 0)) or (
                _int(getattr(obj, "input_other", 0))
                + _int(getattr(obj, "input_cached", 0))
            )
        return {
            "prompt_tokens": p,
            "completion_tokens": _int(getattr(obj, "completion_tokens", 0))
            or _int(getattr(obj, "output", 0)),
            "cached_tokens": _int(getattr(obj, "cached_tokens", 0))
            or _int(getattr(obj, "input_cached", 0)),
        }

    for attr in ("usage", "token_usage"):
        obj = getattr(resp, attr, None)
        if obj is None:
            continue
        u = _normalize_usage(obj) if isinstance(obj, dict) else _from_obj(obj)
        if u["prompt_tokens"] or u["completion_tokens"]:
            return u

    raw = getattr(resp, "raw_completion", None)
    usage_obj = getattr(raw, "usage", None) if raw is not None else None
    if usage_obj is not None:
        u = _from_obj(usage_obj)
        if u["prompt_tokens"] or u["completion_tokens"]:
            return u

    # 估算兜底：宁高勿低（分母取 2 字符/token，对中文偏保守）
    text = ""
    for cand in ("completion_text", "response_text", "_completion_text"):
        v = getattr(resp, cand, None)
        if isinstance(v, str) and v:
            text = v
            break
    return {
        "prompt_tokens": (len(prompt or "") + len(text)) // 2,
        "completion_tokens": len(text) // 2,
        "cached_tokens": 0,
    }


def _normalize_usage(usage: dict) -> Dict[str, int]:
    return {
        "prompt_tokens": _int(usage.get("prompt_tokens", 0)),
        "completion_tokens": _int(usage.get("completion_tokens", 0)),
        "cached_tokens": _int(usage.get("cached_tokens", 0) or usage.get("cache_read_tokens", 0)),
    }


def _load_dict(raw: Any) -> dict:
    """统一 JSON 反序列化。"""
    if isinstance(raw, str):
        return json.loads(raw)
    return raw if raw else {}


class TokenMonitor:
    def __init__(self, plugin):
        self.plugin = plugin
        self._stats = MonitorStats()

    async def initialize(self):
        raw = await self.plugin.get_kv_data(KV_TOKEN_STATS, None)
        if raw:
            try:
                data = _load_dict(raw)
                self._stats.total_calls = data.get("total_calls", 0)
                self._stats.total_prompt_tokens = data.get("total_prompt_tokens", 0)
                self._stats.total_completion_tokens = data.get("total_completion_tokens", 0)
                self._stats.total_cached_tokens = data.get("total_cached_tokens", 0)
                self._stats.total_failures = data.get("total_failures", 0)
                for task, td in (data.get("by_task", {}) or {}).items():
                    self._stats.by_task[task] = TaskStats(**{
                        k: _int(td.get(k, 0)) for k in
                        ("calls", "prompt_tokens", "completion_tokens", "cached_tokens", "failures")
                    })
            except Exception as e:
                logger.debug(f"[mingbu] Token 统计载入失败: {e}")

    async def _save(self):
        data = {
            "total_calls": self._stats.total_calls,
            "total_prompt_tokens": self._stats.total_prompt_tokens,
            "total_completion_tokens": self._stats.total_completion_tokens,
            "total_cached_tokens": self._stats.total_cached_tokens,
            "total_failures": self._stats.total_failures,
            "by_task": {k: asdict(v) for k, v in self._stats.by_task.items()},
        }
        await self.plugin.put_kv_data(
            KV_TOKEN_STATS,
            json.dumps(data, ensure_ascii=False),
        )

    def _today_key(self) -> str:
        return now_bj().strftime("%Y-%m-%d")

    async def _get_daily(self, date: str) -> DailyStats:
        key = KV_TOKEN_DAILY.format(date=date)
        raw = await self.plugin.get_kv_data(key, None)
        if raw:
            try:
                d = _load_dict(raw)
                # 旧记录无 tasks 键：剥离后再构造，避免 TypeError
                tasks = d.pop("tasks", {}) or {}
                ds = DailyStats(**d)
                ds.tasks = tasks
                return ds
            except Exception:
                pass
        return DailyStats()

    async def _save_daily(self, date: str, data: DailyStats):
        key = KV_TOKEN_DAILY.format(date=date)
        await self.plugin.put_kv_data(key, json.dumps(asdict(data), ensure_ascii=False))

    def _task_stats(self, task_type: str) -> TaskStats:
        if task_type not in self._stats.by_task:
            self._stats.by_task[task_type] = TaskStats()
        return self._stats.by_task[task_type]

    async def record_call(self, task_type: str, resp=None, failed: bool = False,
                          prompt: str = ""):
        """记录一次 LLM 调用。resp 为 AstrBot LLM 响应对象。

        prompt: 发送的提示词文本，用于真实 usage 缺失时的字符量估算。
        """
        cfg = getattr(self.plugin, "_config", {}) if hasattr(self.plugin, "_config") else {}
        if not cfg.get("token_monitor_enabled", True):
            return

        self._stats.total_calls += 1
        task = self._task_stats(task_type)
        task.calls += 1
        daily = await self._get_daily(self._today_key())
        daily.calls += 1
        # P2：当日按任务拆分（任务级限额与报表的数据源）
        t = daily.tasks.setdefault(task_type, {})
        t["calls"] = _int(t.get("calls")) + 1

        if failed:
            self._stats.total_failures += 1
            task.failures += 1
            daily.failures += 1
            t["failures"] = _int(t.get("failures")) + 1
        elif resp is not None:
            u = _extract_usage(resp, prompt=prompt)
            self._stats.total_prompt_tokens += u["prompt_tokens"]
            self._stats.total_completion_tokens += u["completion_tokens"]
            self._stats.total_cached_tokens += u["cached_tokens"]
            task.prompt_tokens += u["prompt_tokens"]
            task.completion_tokens += u["completion_tokens"]
            task.cached_tokens += u["cached_tokens"]
            daily.prompt_tokens += u["prompt_tokens"]
            daily.completion_tokens += u["completion_tokens"]
            daily.cached_tokens += u["cached_tokens"]
            t["prompt_tokens"] = _int(t.get("prompt_tokens")) + u["prompt_tokens"]
            t["completion_tokens"] = _int(t.get("completion_tokens")) + u["completion_tokens"]
            t["cached_tokens"] = _int(t.get("cached_tokens")) + u["cached_tokens"]

        await self._save_daily(self._today_key(), daily)
        await self._save()

    async def check_limit(self, task_type: str = "") -> bool:
        """检查今日 Token 是否超过限额（全局 + 任务级）。

        2026-09-22（P2 硬预算）：全局 daily_token_limit 之外，增加任务级
        限额 task_token_limits（JSON 映射，如 {"dream": 100000}）——防止
        单一任务吃满全局预算饿死其他任务。返回 True 表示可以继续调用。
        """
        cfg = getattr(self.plugin, "_config", {}) if hasattr(self.plugin, "_config") else {}
        daily = None

        limit = _int(cfg.get("daily_token_limit", 0))
        if limit > 0:
            daily = await self._get_daily(self._today_key())
            if (daily.prompt_tokens + daily.completion_tokens) >= limit:
                return False

        if task_type:
            try:
                limits = _load_dict(cfg.get("task_token_limits") or {})
            except Exception:
                limits = {}
            tlim = _int(limits.get(task_type, 0))
            if tlim > 0:
                if daily is None:
                    daily = await self._get_daily(self._today_key())
                t = daily.tasks.get(task_type) or {}
                if (_int(t.get("prompt_tokens"))
                        + _int(t.get("completion_tokens"))) >= tlim:
                    return False
        return True

    async def get_stats(self) -> dict:
        today = self._today_key()
        daily = await self._get_daily(today)
        s = self._stats
        last_7 = []
        for i in range(7):
            d = (now_bj() - timedelta(days=i)).strftime("%Y-%m-%d")
            last_7.append({"date": d, **asdict(await self._get_daily(d))})
        cfg = getattr(self.plugin, "_config", {}) or {}
        raw_limits = cfg.get("task_token_limits") or {}
        try:
            task_limits = _load_dict(raw_limits)
        except Exception:
            task_limits = {}
        return {
            "total": {
                "calls": s.total_calls,
                "prompt_tokens": s.total_prompt_tokens,
                "completion_tokens": s.total_completion_tokens,
                "cached_tokens": s.total_cached_tokens,
                "failures": s.total_failures,
            },
            "today": asdict(daily),
            "last_7_days": last_7,
            "by_task": {k: asdict(v) for k, v in s.by_task.items()},
            "limit": cfg.get("daily_token_limit", 0),
            # P2 可观测：任务级限额与当日用量对照（today.tasks / last_7_days.tasks）
            "task_limits": task_limits,
        }
