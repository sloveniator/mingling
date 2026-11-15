"""stars/apis/_base.py -- 管理面板 Web API 的公共样板
=====================================================
拆分前 ``stars/admin.py`` 里 31 个 handler 各自重复同一套样板：

    from astrbot.api.web import error_response, json_response, request
    uid = request.query.get("uid", "")
    if not uid:
        return error_response("缺少 uid")
    if not self.some_module:
        return error_response("xxx 模块未初始化")
    ...

本模块把这套样板收敛为「惰性 web 包装 + 三个装饰器」：

* :func:`json_response` / :func:`error_response` / :func:`request_method`
  与 ``query_*`` 系列 —— 惰性 import ``astrbot.api.web``。
  **必须惰性**：旧版 AstrBot 无 ``astrbot.api.web``，模块级 import 会让
  整个插件在导入期就炸掉（这也是拆分前每个 handler 都写一遍局部 import
  的原因）。收敛到这里后，各领域模块顶层不再出现 astrbot.api.web。
* :func:`require_query` / :func:`with_json` / :func:`require_json`
  —— 必填参数前置校验。装饰器**顺序即校验顺序**：参数装饰器写在
  ``require_module`` 之上，保证「缺参数」优先于「模块未初始化」，与拆分前
  各 handler 的分支顺序逐字一致。
* :func:`require_module` —— 统一「模块未初始化」响应。
* :func:`api_endpoint` —— 兜底 try/except（建议⑤），任何未捕获异常都转成
  ``error_response`` 并计入 metrics，而不是让整页 500。

JSON 体只由装饰器读一次并注入 handler 形参，避免对同一请求重复
``await request.json()``（不同 AstrBot 版本对重复读的容忍度不一致）。
"""

from __future__ import annotations

import asyncio
import functools
from typing import Any, Callable, Optional

from astrbot.api import logger

from ...metrics import record_failure

# 列表类接口一次最多展开的用户数。活跃用户索引可能有上万条，
# 逐个拉关系/档案会把管理面板拖垮，这里统一设一个上限。
MAX_LIST_USERS = 200

# 扇出拉取 KV 时的并发上限（避免一次性打爆 KV 后端）
LIST_CONCURRENCY = 16


# ══════════════════════════════════════════════════════════════
# astrbot.api.web 惰性包装
# ══════════════════════════════════════════════════════════════

def json_response(payload: Any):
    """成功响应。"""
    from astrbot.api.web import json_response as _impl
    return _impl(payload)


def error_response(message: str):
    """失败响应。"""
    from astrbot.api.web import error_response as _impl
    return _impl(message)


def _request():
    from astrbot.api.web import request
    return request


def request_method() -> str:
    return _request().method


async def request_json() -> dict:
    """读取 JSON 请求体（缺失时返回 {}）。"""
    return await _request().json(default={}) or {}


def query_str(name: str, default: str = "") -> str:
    return _request().query.get(name, default)


def query_int(name: str, default: int) -> int:
    """取整型查询参数；非法值回退 default（与拆分前的 ValueError 兜底一致）。"""
    try:
        return int(_request().query.get(name, default))
    except (TypeError, ValueError):
        return default


def query_float(name: str, default: float) -> float:
    try:
        return float(_request().query.get(name, default))
    except (TypeError, ValueError):
        return default


def query_bool(name: str, default: bool = False) -> bool:
    raw = _request().query.get(name)
    if raw is None:
        return default
    return str(raw).lower() == "true"


# ══════════════════════════════════════════════════════════════
# 并发助手
# ══════════════════════════════════════════════════════════════

async def gather_limited(coro_factories, concurrency: int = LIST_CONCURRENCY):
    """带并发上限地并行执行，避免一次性打爆 KV 后端。"""
    sem = asyncio.Semaphore(concurrency)

    async def _one(factory):
        async with sem:
            return await factory()

    return await asyncio.gather(*(_one(f) for f in coro_factories))


async def safe_row(factory: Callable, *, label: str):
    """列表类接口的单行兜底（建议⑤）。

    单个用户的 KV 读失败不该让整页 500：记一次失败计数并返回 None，
    由调用方过滤掉即可。
    """
    try:
        return await factory()
    except Exception as e:
        logger.debug(f"[mingbu] {label} 单行取数失败(已跳过): {e}")
        try:
            record_failure(f"api_{label}")
        except Exception:
            pass
        return None


# ══════════════════════════════════════════════════════════════
# 装饰器
# ══════════════════════════════════════════════════════════════

def require_query(name: str, message: Optional[str] = None):
    """必填查询参数。缺失时返回 error_response(message)。"""
    msg = message or f"缺少 {name}"

    def deco(fn):
        @functools.wraps(fn)
        async def wrapper(self, *args, **kwargs):
            if not query_str(name):
                return error_response(msg)
            return await fn(self, *args, **kwargs)

        return wrapper

    return deco


def with_json(fn):
    """读取一次 JSON 体并作为第一个位置参数注入 handler。"""

    @functools.wraps(fn)
    async def wrapper(self, *args, **kwargs):
        body = await request_json()
        return await fn(self, body, *args, **kwargs)

    return wrapper


def require_json(name: str, message: Optional[str] = None):
    """必填 JSON 字段；同时把请求体注入 handler 首个位置参数。

    与 :func:`with_json` 二者只需其一——本装饰器已包含注入行为。
    """
    msg = message or f"缺少 {name}"

    def deco(fn):
        @functools.wraps(fn)
        async def wrapper(self, *args, **kwargs):
            body = await request_json()
            if not body.get(name):
                return error_response(msg)
            return await fn(self, body, *args, **kwargs)

        return wrapper

    return deco


def require_module(attr: str,
                   message: Optional[str] = None,
                   fallback: Optional[Callable[..., Any]] = None):
    """引擎未初始化时的统一响应。

    :param attr:     ``self`` 上的引擎属性名（经 MingbuStar 代理到内核）
    :param message:  返回 ``error_response(message)``
    :param fallback: 返回 ``json_response(fallback(handler_args))``——
                     用于「空数据比报错更合适」的只读列表接口。
                     可调用对象接收与 handler 相同的实参，便于把
                     ``gid`` 之类的请求参数回填进空响应。

    ``message`` 与 ``fallback`` 二者必须且只需给一个。
    """
    if (message is None) == (fallback is None):
        raise ValueError("require_module 需要 message 或 fallback 其一")

    def deco(fn):
        @functools.wraps(fn)
        async def wrapper(self, *args, **kwargs):
            if not getattr(self, attr, None):
                if fallback is not None:
                    return json_response(fallback(*args, **kwargs))
                return error_response(message)
            return await fn(self, *args, **kwargs)

        return wrapper

    return deco


def api_endpoint(label: str):
    """兜底 try/except（建议⑤）。

    拆分前只有 ``api_trigger`` 写了完整 try/except，``api_user_detail``
    这类接口任一底层 KV 异常都会让整页 500。这里统一兜底：转成
    ``error_response`` 并计入 metrics，前端能看到原因而不是白屏。
    """

    def deco(fn):
        @functools.wraps(fn)
        async def wrapper(self, *args, **kwargs):
            try:
                return await fn(self, *args, **kwargs)
            except Exception as e:
                logger.warning(f"[mingbu] Web API {label} 失败: {e}", exc_info=True)
                try:
                    record_failure(f"api_{label}")
                except Exception:
                    pass
                return error_response(f"{label}失败: {str(e)[:120]}")

        return wrapper

    return deco


# ══════════════════════════════════════════════════════════════
# KV 命名空间护栏
# ══════════════════════════════════════════════════════════════

# 只读可见的命名空间
KV_READ_PREFIXES = ("mingbu.", "astrbot.")
# 可写命名空间（比只读更严：不允许碰 astrbot.* 主配置）
KV_WRITE_PREFIX = "mingbu."
# 受保护键：人格状态由引擎自己维护，经面板裸写会把内存态与 KV 割裂
KV_PROTECTED = ("mingbu.persona",)


def kv_read_guard(key: str) -> Optional[str]:
    """校验 KV 读键；通过返回 None，否则返回错误文案。"""
    if not key:
        return "缺少 key"
    if not key.startswith(KV_READ_PREFIXES):
        return "仅允许查看 mingbu.* / astrbot.* 命名空间"
    return None


def kv_write_guard(key: str) -> Optional[str]:
    """校验 KV 写键；通过返回 None，否则返回错误文案。"""
    if not key or not key.startswith(KV_WRITE_PREFIX):
        return "仅允许写入 mingbu.* 键"
    if key in KV_PROTECTED:
        return "persona 受保护，不可经此覆盖"
    return None


__all__ = [
    "MAX_LIST_USERS",
    "LIST_CONCURRENCY",
    "KV_PROTECTED",
    "api_endpoint",
    "error_response",
    "gather_limited",
    "json_response",
    "kv_read_guard",
    "kv_write_guard",
    "query_bool",
    "query_float",
    "query_int",
    "query_str",
    "request_json",
    "request_method",
    "require_json",
    "require_module",
    "require_query",
    "safe_row",
    "with_json",
]


# 兼容旧名：拆分前 admin.py 内部叫 _gather_limited，
# tests / devtools 若按旧名引用仍可用。
_gather_limited = gather_limited
