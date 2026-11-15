"""
client.py -- Mingbu Next.js API 异步客户端
============================================
带连接池复用的 aiohttp 客户端。

覆盖 Mingbu 后端完整 API 路由表（截至 2026-07）：
  /api/bazi /api/combined /api/liuyao /api/qimen /api/interpret
  /api/health /api/clients/ensure /api/library /api/techniques
  /api/kline /api/cache /api/ab/assign
"""
from __future__ import annotations

import json
from typing import Any, List, Optional

import aiohttp

from .http_client import ServiceHttpError, request_json, request_stream

MINGBU_DEFAULT_URL = "http://localhost:2963"

# 单个后端、多个用户共享，连接池上限 20 足够覆盖并发排盘/占卜
DEFAULT_CONNECTOR_LIMIT = 20
DEFAULT_TIMEOUT = 30


def _accumulate_sse(lines: List[str]) -> str:
    """从一个 SSE 文本流（按行）累积 data 负载，返回拼接后的完整文本。

    Mingbu 后端 SSE 格式：
      data: {"delta":{"text":"..."}}
      data: [DONE]

    规则：
      - 忽略以 ':' 开头的注释/心跳行；
      - 取 'data:' 后的 JSON payload，提取 delta.text 再拼接；
      - 跳过 '[DONE]' 终止标记。
    """
    out: List[str] = []
    for line in lines:
        line = line.rstrip("\n").rstrip("\r")
        if not line:
            continue
        if line.startswith(":"):
            continue
        if line.startswith("data:"):
            payload = line[len("data:"):].lstrip()
            if payload == "[DONE]":
                continue
            try:
                obj = json.loads(payload)
                text = obj.get("delta", {}).get("text", "")
                if text:
                    out.append(text)
            except json.JSONDecodeError:
                out.append(payload)
    return "".join(out)


class MingbuClient:
    """Mingbu 命理计算服务（八字/紫微/六爻/奇门）异步客户端。"""

    def __init__(
        self,
        base_url: str = MINGBU_DEFAULT_URL,
        connector_limit: int = DEFAULT_CONNECTOR_LIMIT,
        default_timeout: int = DEFAULT_TIMEOUT,
    ):
        self.base_url = base_url.rstrip("/")
        self._connector_limit = connector_limit
        self._default_timeout = default_timeout
        # 懒加载，避免在事件循环外创建 session（aiohttp 硬性要求）
        self._session: Optional[aiohttp.ClientSession] = None

    async def _ensure_session(self) -> aiohttp.ClientSession:
        """复用同一个带连接池的 session；closed 时自动重建。"""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                connector=aiohttp.TCPConnector(limit=self._connector_limit),
            )
        return self._session

    async def _post(
        self, path: str, payload: dict, timeout: Optional[int] = None,
        headers: Optional[dict] = None,
    ) -> dict:
        session = await self._ensure_session()
        url = f"{self.base_url}{path}"
        return await request_json(
            session, "POST", url, payload=payload,
            timeout=timeout or self._default_timeout, headers=headers,
        )

    async def _get(
        self, path: str, params: Optional[dict[str, str]] = None,
        timeout: Optional[int] = None,
    ) -> dict:
        session = await self._ensure_session()
        url = f"{self.base_url}{path}"
        return await request_json(
            session, "GET", url, params=params, timeout=timeout or self._default_timeout
        )

    async def _delete(
        self, path: str, timeout: Optional[int] = None,
    ) -> dict:
        session = await self._ensure_session()
        url = f"{self.base_url}{path}"
        return await request_json(
            session, "DELETE", url, timeout=timeout or self._default_timeout
        )

    async def close(self) -> None:
        """释放连接池。插件 terminate() 时调用。"""
        if self._session is not None and not self._session.closed:
            await self._session.close()
            self._session = None

    # ---- 命理排盘 ----

    async def bazi(self, birth: dict) -> dict:
        return await self._post("/api/bazi", {"birthInfo": birth})

    async def combined(self, birth: dict) -> dict:
        return await self._post("/api/combined", {"birthInfo": birth})

    async def liuyao(
        self,
        question: str,
        question_type: str = "general",
        *,
        method: str = "coin",
        throws: Optional[List[int]] = None,
        number: Optional[int] = None,
        date: Optional[str] = None,
        cast_label: Optional[str] = None,
        calendar_kind: Optional[str] = None,
        calendar_value: Optional[str] = None,
        lines: Optional[List[int]] = None,
        moving_positions: Optional[List[int]] = None,
    ) -> dict:
        """六爻起卦。

        后端 POST /api/liuyao 支持完整参数：
          method: coin/time/number/random/given
          throws(number[]): 手动指定每次掷爻结果
          number: 数字起卦用
          date: ISO 时间字符串
          castLabel: 起卦标签
          calendarKind: "lunar" | "gregorian"
          calendarValue: 农历/公历文本
          lines(6): given 模式指定六爻
          movingPositions(6): given 模式指定动爻位置
        """
        payload: dict[str, Any] = {
            "method": method,
            "question": question,
            "questionType": question_type,
        }
        if throws is not None:
            payload["throws"] = throws
        if number is not None:
            payload["number"] = number
        if date is not None:
            payload["date"] = date
        if cast_label is not None:
            payload["castLabel"] = cast_label
        if calendar_kind is not None:
            payload["calendarKind"] = calendar_kind
        if calendar_value is not None:
            payload["calendarValue"] = calendar_value
        if lines is not None:
            payload["lines"] = lines
        if moving_positions is not None:
            payload["movingPositions"] = moving_positions
        return await self._post("/api/liuyao", payload)

    async def qimen(
        self, year: int, month: int, day: int,
        hour: int, minute: int = 0,
    ) -> dict:
        """奇门遁甲起局。

        后端 POST /api/qimen 接受 { year, month, day, hour, minute }，其中
        ``hour`` 为「时辰地支索引 0–11」（0=子 … 11=亥），**不是** 0–23 钟点。
        调用方须先经 ``_clock_to_shichen(clock_hour)`` 折算（见 background_watch 与
        stars/tools.query_qimen 的用法）。
        """
        return await self._post("/api/qimen", {
            "year": year, "month": month, "day": day,
            "hour": hour, "minute": minute,
        })

    async def zizhan(self, question: str = "", mode: str = "now") -> dict:
        """紫占（紫微斗数占卜）：起盘 + 三层卦 + 占断 prompt。

        后端 POST /api/zizhan 接受 { question?, mode? }，mode 可选
        ``now``（当前时刻时空起盘，默认）/ ``random``（密码学安全随机起盘）。
        返回 { birthInfo, chart, threeLayer, prompt }，全部本地计算，
        不消耗 AI Provider 配额。三层卦始终按「当下问事之时空」计算。
        """
        return await self._post("/api/zizhan", {
            "question": question,
            "mode": mode,
        })

    # ---- 客户端/档案管理 ----

    async def ensure_client(self, input_data: dict, user_id: Optional[str] = None) -> dict:
        """按生辰去重获取或创建客户档案。

        后端 POST /api/clients/ensure 接受：
          { birth: { year, month, day, hour, minute?, gender }, name?: string }
        返回 { client: {...} }。

        后端要求请求头 ``x-ziwei-user-id``（匿名 UUID，无登录鉴权）。调用方须
        传入稳定 ``user_id``（如 AstrBot 用户 ID 的散列/UUID 映射）；缺省时后端
        会以「缺少或非法 user-id」拒绝。当前插件聊天流尚未接线（需确认
        sender_id → 后端匿名 UUID 的映射策略），属可选增强。
        """
        headers = {"x-ziwei-user-id": user_id} if user_id else None
        return await self._post("/api/clients/ensure", input_data, headers=headers)

    # ---- 人生 K 线 ----

    async def kline(self, chart: dict) -> dict:
        """基于紫微命盘生成 1-100 岁人生运势 K 线。"""
        return await self._post("/api/kline", {"chart": chart})

    # ---- 技法知识库 ----

    async def techniques(
        self, type: str = "liuyao", terms: str = "", top: int = 12,
    ) -> dict:
        """检索技法知识库条目。

        type: liuyao / meihua / qimen
        terms: 逗号分隔的关键词
        top: 返回条数上限（1-30）
        """
        params = {"type": type, "terms": terms, "top": str(top)}
        return await self._get("/api/techniques", params=params)

    # ---- 命盘缓存 ----

    async def cache_get(self) -> dict:
        """加载缓存的命盘。"""
        return await self._get("/api/cache")

    async def cache_save(self, birth_info: dict, chart: dict) -> dict:
        """保存命盘到缓存。"""
        return await self._post("/api/cache", {
            "birthInfo": birth_info, "chart": chart,
        })

    async def cache_clear(self) -> dict:
        """清除命盘缓存。"""
        return await self._delete("/api/cache")

    # ---- 命理图书馆（数据导入/导出/备份） ----

    async def library_export(self, op: str = "") -> dict:
        """导出命盘数据库。

        op="backup" 时触发文件级备份。
        """
        params = {"op": op} if op else None
        return await self._get("/api/library", params=params)

    async def library_import(self, data: dict) -> dict:
        """导入命盘数据库（全量替换）。"""
        return await self._post("/api/library", data)

    # ---- A/B 测试分配 ----

    async def ab_assign(self, experiment_id: str, user_id: Optional[str] = None) -> dict:
        """分配 A/B 测试实验变体。"""
        payload: dict[str, Any] = {"experimentId": experiment_id}
        if user_id is not None:
            payload["userId"] = user_id
        return await self._post("/api/ab/assign", payload)

    # ---- 服务健康检查 ----

    async def health(self) -> dict:
        """GET /api/health → 服务可用性与 AI Provider 配置状态。"""
        return await self._get("/api/health")

    # ---- 流式解读（AI 解读端点，返回 SSE）----

    async def interpret(
        self, *, type: str = "combined", birth: Optional[dict] = None,
        chart: Optional[dict] = None, topic: str = "free",
        question: Optional[str] = None, question_type: Optional[str] = None,
        provider: Optional[str] = None, api_key: Optional[str] = None,
        base_url: Optional[str] = None, model: Optional[str] = None,
        target_year: Optional[int] = None, timeout: Optional[int] = None,
        view: Optional[str] = None,
        all_flow_data: Optional[dict] = None,
        enable_thinking: Optional[bool] = None,
        enable_web_search: Optional[bool] = None,
        messages: Optional[list] = None,
    ) -> str:
        """调用 /api/interpret 流式端点，返回完整解读文本。

        该端点返回 text/event-stream（格式：data: {"delta":{"text":"..."}} ）；
        命灵在后台使用，故累积为完整字符串（无需逐字流式）。

        view / allFlowData: 前端 TimeNav 传递的流盘视图参数，
        用于综合解读分层叠加（本命/大限/流年/流月/流日）。
        messages: 命灵侧拼好的对话历史（[{role, content}]），传入后 mingbu
        可做连贯的多轮解读；缺省时回退为单条 user 消息（仅 question）。
        """
        if messages:
            payload_messages = messages
        else:
            payload_messages = [{"role": "user", "content": question or "请解读"}]
        payload: dict[str, Any] = {
            "type": type,
            "topic": topic,
            "messages": payload_messages,
        }
        if birth:
            payload["birthInfo"] = birth
        if chart:
            payload["chart"] = chart
        if question_type:
            payload["questionType"] = question_type
        if target_year is not None:
            payload["targetYear"] = target_year
        if provider:
            payload["provider"] = provider
        if api_key:
            payload["apiKey"] = api_key
        if base_url:
            payload["baseUrl"] = base_url
        if model:
            payload["model"] = model
        if view is not None:
            payload["view"] = view
        if all_flow_data is not None:
            payload["allFlowData"] = all_flow_data
        if enable_thinking is not None:
            payload["enableThinking"] = enable_thinking
        if enable_web_search is not None:
            payload["enableWebSearch"] = enable_web_search

        url = f"{self.base_url}/api/interpret"
        session = await self._ensure_session()
        try:
            buf = await request_stream(
                session, url, payload=payload, timeout=timeout or self._default_timeout
            )
        except ServiceHttpError as e:
            # 与旧实现语义一致：以普通 Exception 抛出，调用方统一 except Exception 兜底
            raise Exception(str(e))
        return _accumulate_sse(buf)
