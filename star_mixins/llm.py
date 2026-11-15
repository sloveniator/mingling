"""
star_mixins/llm.py -- 系统级 LLM 文本生成（带防递归 + 超时兜底 + Token 监控）
==================================================================
系统级 LLM 文本生成（带防递归 + 超时兜底 + Token 监控）。

本模块由 ``MingbuStar`` 混入。方法体内的 ``self.<engine>`` 访问依赖
``MingbuStar.__getattr__`` 代理到 ``MingbuCore``，因此 mixin 本身不 import
``core``，也就不产生循环依赖。
"""
import asyncio

from astrbot.api import logger
from astrbot.api.provider import ProviderRequest

from ..constants import LLM_TEXT_TIMEOUT


class LLMTextMixin:
    """系统级 LLM 文本生成（带防递归 + 超时兜底 + Token 监控）。"""

    async def _llm_text(self, prompt: str, session_id: str = "mingbu_sys", task_type: str = "general") -> str:
        """系统级 LLM 文本生成（带防递归 + 超时兜底 + Token 监控）。"""
        if self.core._in_sys_llm:
            return ""  # 防止 on_llm_request 钩子递归
        # Token 限额检查（P2：全局 + 任务级硬预算）
        if self.token_monitor and not await self.token_monitor.check_limit(task_type):
            logger.warning(
                f"[mingbu] 今日 Token 限额已用完（task={task_type}），跳过系统 LLM 调用"
            )
            return ""
        self.core._in_sys_llm = True
        try:
            req = ProviderRequest(prompt=prompt, session_id=session_id)
            # 读取本任务指定模型；子任务未指定则沿用主回复模型
            model = self._config.get(f"provider_{task_type}_model", "") or ""
            if task_type not in ("main_reply", "general") and not model:
                model = self._config.get("provider_main_reply_model", "") or ""
            if model:
                try:
                    req.model = model
                except Exception:
                    pass
            try:
                if self.provider_router:
                    resp = await asyncio.wait_for(
                        self.provider_router.llm_request(task_type, req, model=model), timeout=LLM_TEXT_TIMEOUT
                    )
                else:
                    # 真实 AstrBot 的 Context 没有 llm_request()，直接调用会 AttributeError。
                    # 统一走 default_llm_request：旧版/桩优先用 llm_request，
                    # 否则取「当前使用的对话服务商」执行 text_chat。
                    from ..provider_router import default_llm_request

                    resp = await asyncio.wait_for(
                        default_llm_request(self.context, req, model), timeout=LLM_TEXT_TIMEOUT
                    )
            except asyncio.TimeoutError:
                logger.warning(f"[mingbu] 系统 LLM 生成超时({LLM_TEXT_TIMEOUT}s)，用兜底")
                if self.token_monitor:
                    await self.token_monitor.record_call(task_type, failed=True)
                return ""
            if resp is None:
                if self.token_monitor:
                    await self.token_monitor.record_call(task_type, failed=True)
                return ""
            text = getattr(resp, "completion_text", None) \
                or getattr(resp, "response_text", None) \
                or str(resp)
            if self.token_monitor:
                # 传入 prompt：真实 usage 缺失时用于字符量估算（见 token_monitor）
                await self.token_monitor.record_call(task_type, resp=resp, prompt=prompt)
            return text.strip()
        except Exception as e:
            logger.warning(f"[mingbu] 系统 LLM 生成失败(用兜底): {e}")
            if self.token_monitor:
                await self.token_monitor.record_call(task_type, failed=True)
            return ""
        finally:
            self.core._in_sys_llm = False
