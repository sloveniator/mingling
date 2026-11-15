"""http.py -- 统一 HTTP 客户端助手
==================================
收敛 client.py 与 provider_router.py 直连函数中重复的「建 session / 拼 header /
设超时 / 连接失败·超时异常处理」样板，并作为统一异常 ``ServiceHttpError`` 的来源。

设计要点：
  - 内部 ``session`` 参数若为 ``None``，则自带临时 session 并在用完后关闭
    （provider_router.py 的直连场景）；若传入复用中的 session（client.py 的连接池），
    则只负责请求，不关闭它 —— 连接池由调用方管理。
  - 所有非 2xx / 连接失败 / 超时统一转成 ``ServiceHttpError``，消息与旧实现完全一致，
    因此调用方既有的 ``except Exception`` 兜底行为不变。
  - **韧性层（本次新增）**：每个请求外面包一层「熔断 + 有限重试」——
      * 熔断：按 host 维度，连续失败达阈值后进入冷却，期间直接拒绝（避免对死亡服务
        反复打爆）；冷却结束后放行一次探活。
      * 重试：仅对「可重试」错误（连接失败 / 超时 / 429 限流 / 5xx 服务端错误）做
        有限次的指数退避重试；4xx 业务错误（400/401/403/404…）与非法 JSON 不重试。
"""

from __future__ import annotations

import asyncio
import random
import time
from typing import Any, Optional
from urllib.parse import urlparse

import aiohttp
from aiohttp import ContentTypeError
from astrbot.api import logger

DEFAULT_TIMEOUT = 30
_CONNECT_MSG = "无法连接到 Mingbu 命理服务，请确认服务已启动"
_TIMEOUT_MSG = "Mingbu 命理服务响应超时，请稍后重试"

# ---- 重试 / 熔断可调参数 ----
RETRY_MAX = 3                 # 最多额外重试次数（含首试共 4 次）
RETRY_BASE_DELAY = 0.5        # 指数退避基值（秒）
RETRY_MAX_DELAY = 8.0         # 退避上限（秒）
BREAKER_FAILURE_THRESHOLD = 5  # 连续失败达此值则熔断
BREAKER_COOLDOWN_SEC = 30.0    # 熔断冷却时长（秒）


class ServiceHttpError(Exception):
    """HTTP 层统一异常（连接失败 / 超时 / 非 2xx / 非法响应）。

    新增 ``status`` / ``retryable`` 两个可选字段，供韧性层判断「该不该重试 /
    算不算熔断计数」；旧调用方只比对消息文本的写法不受影响。
    """

    def __init__(self, message: str, *, status: Optional[int] = None,
                 retryable: Optional[bool] = None):
        super().__init__(message)
        self.status = status
        self.retryable = retryable


def _is_retryable_status(status: int) -> bool:
    """仅 429 限流与 5xx 服务端错误可重试；4xx 业务错误不重试。"""
    return status == 429 or 500 <= status < 600


class _CircuitBreaker:
    """极简熔断：按 host 维度，连续失败达阈值后进入冷却，期间直接拒绝，
    避免对死亡服务反复重试打爆；冷却结束后放行一次探活。

    进程级单例（见 ``_DEFAULT_BREAKER``）：只有真正「跨请求共享状态」才有意义。
    """

    def __init__(self, failure_threshold: int = BREAKER_FAILURE_THRESHOLD,
                 cooldown_sec: float = BREAKER_COOLDOWN_SEC):
        self.failure_threshold = failure_threshold
        self.cooldown_sec = cooldown_sec
        self._failures: dict[str, int] = {}
        self._opened_at: dict[str, float] = {}

    @staticmethod
    def _key(url: str) -> str:
        try:
            return urlparse(url).netloc or url
        except Exception:
            return url

    def allow(self, url: str) -> bool:
        key = self._key(url)
        f = self._failures.get(key, 0)
        if f < self.failure_threshold:
            return True
        # 已达阈值：冷却结束则半开探活（放行一次），否则继续拒绝
        if time.monotonic() - self._opened_at.get(key, 0.0) >= self.cooldown_sec:
            self._failures[key] = self.failure_threshold - 1
            return True
        return False

    def on_success(self, url: str) -> None:
        key = self._key(url)
        if self._failures.get(key, 0) != 0:
            self._failures[key] = 0

    def on_failure(self, url: str) -> None:
        key = self._key(url)
        f = self._failures.get(key, 0) + 1
        if f >= self.failure_threshold:
            self._opened_at[key] = time.monotonic()
        self._failures[key] = f


_DEFAULT_BREAKER = _CircuitBreaker()


async def _resilient(corofn, url: str, *,
                     breaker: _CircuitBreaker = _DEFAULT_BREAKER,
                     retry_max: int = RETRY_MAX,
                     retry_base: float = RETRY_BASE_DELAY,
                     retry_max_delay: float = RETRY_MAX_DELAY):
    """对 ``corofn()`` 施加熔断前置 + 有限重试（指数退避 + 抖动）。

    - 熔断打开时直接抛 ``ServiceHttpError``，不重试；
    - 仅对 ``exc.retryable`` 为真（连接/超时/429/5xx）的错误重试；
    - 重试次数用尽或遇到不可重试错误，抛出最后一次异常。
    """
    attempt = 0
    while True:
        if not breaker.allow(url):
            raise ServiceHttpError(
                "Mingbu 外部服务熔断中（连续失败过多），请稍后重试",
                status=None, retryable=False,
            )
        try:
            result = await corofn()
        except ServiceHttpError as exc:
            # 仅「可重试」错误（连接失败/超时/429/5xx）计入熔断并重试；
            # 4xx 业务错误（400/401/403/404…）不是服务不健康的信号，若也计入
            # 熔断计数，反复 4xx（如出生参数非法）会把存活的服务误拖入冷却。
            if not exc.retryable:
                raise
            breaker.on_failure(url)
            attempt += 1
            if attempt > retry_max:
                raise
            delay = min(retry_max_delay, retry_base * (2 ** (attempt - 1)))
            delay += random.uniform(0, 0.2 * delay)  # 抖动，避免惊群
            logger.warning(f"[http] 请求 {url} 第 {attempt} 次重试: {exc}")
            await asyncio.sleep(delay)
        else:
            breaker.on_success(url)
            return result


def _headers(api_key: Optional[str] = None) -> dict:
    # 不在此处硬编码 Content-Type：aiohttp 会根据 body 类型自动设置——
    #   * json=payload  → 自动 `Content-Type: application/json`；
    #   * data=FormData → 自动 `Content-Type: multipart/form-data; boundary=...`。
    # 历史实现无条件写 `Content-Type: application/json`，会把 multipart 表单的
    # boundary 覆盖掉，导致 STT 上传音频时后端按 JSON 解析 multipart 字节、报
    # HTTP 400 / code 20015（参数无效）。这里只保留鉴权头，Content-Type 交还 aiohttp。
    headers = {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    return headers


async def request_json(
    session: Optional[aiohttp.ClientSession],
    method: str,
    url: str,
    *,
    payload: Any = None,
    params: Optional[dict] = None,
    form: Optional[aiohttp.FormData] = None,
    api_key: Optional[str] = None,
    timeout: int = DEFAULT_TIMEOUT,
    ssl: Optional[bool] = None,
    headers: Optional[dict] = None,
    breaker: Optional[_CircuitBreaker] = None,
) -> Any:
    """发出一次请求（JSON 或表单）并解析响应 JSON。

    session 为 ``None`` 时自带临时 session（用后关闭）；否则复用传入 session。
    成功返回解析后的 JSON；非 2xx / 连接失败 / 超时抛 ``ServiceHttpError``。
    ``headers`` 为额外请求头，会覆盖同名默认头（用于 x-ziwei-user-id 等）。
    ``breaker`` 可覆盖默认熔断器； Dashboard 等可选内网拉取可传入独立实例，
    避免局部网络失败污染全局熔断器。
    """
    async def _core():
        own_session = session is None
        s = aiohttp.ClientSession() if own_session else session
        try:
            hdrs = _headers(api_key)
            if headers:
                hdrs.update(headers)
            async with s.request(
                method, url, json=payload, params=params, data=form,
                timeout=aiohttp.ClientTimeout(total=timeout),
                headers=hdrs, ssl=ssl,
            ) as resp:
                if not 200 <= resp.status < 300:
                    text = await resp.text()
                    raise ServiceHttpError(
                        f"HTTP {resp.status}: {text[:300]}",
                        status=resp.status,
                        retryable=_is_retryable_status(resp.status),
                    )
                try:
                    return await resp.json()
                except (ContentTypeError, ValueError) as exc:
                    text = await resp.text()
                    raise ServiceHttpError(
                        f"响应不是合法 JSON: {text[:300]}",
                        status=200, retryable=False,
                    ) from exc
        except aiohttp.ClientConnectorError:
            raise ServiceHttpError(_CONNECT_MSG, retryable=True)
        except asyncio.TimeoutError:
            raise ServiceHttpError(_TIMEOUT_MSG, retryable=True)
        except aiohttp.ClientError as exc:
            raise ServiceHttpError(f"请求失败: {exc}", retryable=True)
        finally:
            if own_session:
                await s.close()

    return await _resilient(_core, url, breaker=breaker if breaker is not None else _DEFAULT_BREAKER)


async def request_stream(
    session: Optional[aiohttp.ClientSession],
    url: str,
    *,
    payload: Any = None,
    api_key: Optional[str] = None,
    timeout: int = DEFAULT_TIMEOUT,
) -> list:
    """POST 并逐行读取 SSE 文本流，返回解码后的行列表（由调用方累积）。

    用于 client.py 的 /api/interpret 流式解读端点。
    """
    async def _core():
        own_session = session is None
        s = aiohttp.ClientSession() if own_session else session
        try:
            async with s.post(
                url, json=payload,
                timeout=aiohttp.ClientTimeout(total=timeout),
                headers=_headers(api_key),
            ) as resp:
                if not 200 <= resp.status < 300:
                    text = await resp.text()
                    raise ServiceHttpError(
                        f"HTTP {resp.status}: {text[:300]}",
                        status=resp.status,
                        retryable=_is_retryable_status(resp.status),
                    )
                buf: list = []
                while True:
                    raw_line = await resp.content.readline()
                    if not raw_line:
                        break
                    buf.append(raw_line.decode("utf-8", errors="ignore"))
                return buf
        except aiohttp.ClientConnectorError:
            raise ServiceHttpError(_CONNECT_MSG, retryable=True)
        except asyncio.TimeoutError:
            raise ServiceHttpError(_TIMEOUT_MSG, retryable=True)
        except aiohttp.ClientError as exc:
            raise ServiceHttpError(f"请求失败: {exc}", retryable=True)
        finally:
            if own_session:
                await s.close()

    return await _resilient(_core, url)


async def request_bytes(
    session: Optional[aiohttp.ClientSession],
    url: str,
    *,
    payload: Any = None,
    form: Optional[aiohttp.FormData] = None,
    api_key: Optional[str] = None,
    timeout: int = DEFAULT_TIMEOUT,
    ssl: Optional[bool] = None,
) -> bytes:
    """POST 并返回原始响应字节（TTS 语音合成）。"""
    async def _core():
        own_session = session is None
        s = aiohttp.ClientSession() if own_session else session
        try:
            async with s.post(
                url, json=payload, data=form,
                timeout=aiohttp.ClientTimeout(total=timeout),
                headers=_headers(api_key), ssl=ssl,
            ) as resp:
                if not 200 <= resp.status < 300:
                    text = await resp.text()
                    raise ServiceHttpError(
                        f"HTTP {resp.status}: {text[:300]}",
                        status=resp.status,
                        retryable=_is_retryable_status(resp.status),
                    )
                return await resp.read()
        except aiohttp.ClientConnectorError:
            raise ServiceHttpError(_CONNECT_MSG, retryable=True)
        except asyncio.TimeoutError:
            raise ServiceHttpError(_TIMEOUT_MSG, retryable=True)
        except aiohttp.ClientError as exc:
            raise ServiceHttpError(f"请求失败: {exc}", retryable=True)
        finally:
            if own_session:
                await s.close()

    return await _resilient(_core, url)


__all__ = [
    "DEFAULT_TIMEOUT",
    "ServiceHttpError",
    "request_bytes",
    "request_json",
    "request_stream",
]
