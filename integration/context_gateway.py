"""integration/context_gateway.py -- 轻量上下文网关（2.0 第一阶段任务 4）。

职责边界（对应方案 3.2）：只负责为 AstrBot 当前这一轮请求**准备增强上下文**，
不聊天、不推理、不改写主链路。它把散落在 llm_hook/persona_ctx 的「取身份 →
取人格 → 检索记忆 → 组装提示」串成一个可测试的入口，并复用任务 2/3 的
retrieve_relevant + scope 隔离语义。

设计原则：
- core 以参数传入（鸭子类型），不 import MingbuCore，避免循环依赖，
  也让本模块可在没有完整插件装配的情况下单测。
- 每个增强步骤独立 try/except，失败返回空值并继续（fail-open 局部化），
  绝不让单点增强异常中断 build()。
- 输出 EnhancementContext 是纯数据快照，交由注入侧（persona_ctx/HookChain）
  决定如何呈现；本模块不直接 inject_text_part，保持与注入解耦。

注意：这是第一阶段的最小可用 Gateway。真正接管 llm_hook 全部组装是后续
阶段的工作；当前主要价值是把「相关记忆检索 + 身份/会话」收敛为一个入口，
供注入侧与工具（mingling_recall 等）复用。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from astrbot.api import logger

from .context_budget import estimate_tokens
from .feature_flags import FeatureFlags


@dataclass
class EnhancementContext:
    """一轮请求的增强上下文快照（纯数据，不含行为）。"""
    user_id: str = ""
    session_id: str = ""
    is_group: bool = False
    # 群聊隔离后的召回范围：group 只含可分享记忆，private/None 含全部
    recall_scope: Optional[str] = None
    persona: Dict = field(default_factory=dict)
    memories: List = field(default_factory=list)   # List[Memory]
    hints: List[str] = field(default_factory=list)
    estimated_tokens: int = 0
    # 可观测性：记录本轮是否真正注入了内容（observe 模式恒为 False）
    injected: bool = False


class ContextGateway:
    """为当前请求组装增强上下文。core 以参数传入，鸭子类型解耦。"""

    def __init__(self, core, flags: Optional[FeatureFlags] = None):
        self.core = core
        self.flags = flags

    async def build(self, user_id: str, session_id: str = "",
                    message: str = "", is_group: bool = False,
                    limit: int = 5,
                    ) -> EnhancementContext:
        """构建本轮增强上下文。任一步骤失败均降级为空值，绝不抛出。"""
        ctx = EnhancementContext(
            user_id=user_id,
            session_id=session_id,
            is_group=is_group,
            # 群聊只召回可分享记忆（任务 3 隔离语义）
            recall_scope="group" if is_group else None,
        )
        if self.flags is not None and not self.flags.should_modify_request():
            # observe 模式或总开关关闭：只观察不组装注入内容
            return ctx

        # 1) 相关记忆检索（任务 2 兜底：无需 embedding，按当前消息相关性）
        if self._cap_enabled("memory"):
            try:
                memory = getattr(self.core, "memory", None)
                if memory is not None and message:
                    ctx.memories = list(await memory.retrieve_relevant(
                        user_id, message, limit=limit,
                        max_scope=ctx.recall_scope))
            except Exception as e:  # noqa: BLE001 - 单点增强失败不中断
                logger.debug(f"[mingbu.gateway] 记忆检索失败(忽略): {e}")

        # 2) 人格快照（轻量：persona 引擎的 self_prompt 摘要）
        if self._cap_enabled("persona"):
            try:
                persona = getattr(self.core, "persona", None)
                if persona is not None:
                    ctx.persona = {"name": getattr(persona, "name", "")}
            except Exception as e:  # noqa: BLE001
                logger.debug(f"[mingbu.gateway] 人格快照失败(忽略): {e}")

        # 3) 组装辅助提示（交注入侧呈现，本模块不改 req）
        ctx.hints = self._build_hints(ctx)
        ctx.estimated_tokens = sum(estimate_tokens(h) for h in ctx.hints)
        ctx.injected = bool(ctx.memories or ctx.hints)
        return ctx

    def _cap_enabled(self, name: str) -> bool:
        """能力开关：有 flags 用 flags，无则默认放行（向后兼容）。"""
        if self.flags is None:
            return True
        return self.flags.is_enabled(name)

    @staticmethod
    def _build_hints(ctx: EnhancementContext) -> List[str]:
        """把检索到的记忆转成注入侧可用的辅助提示。"""
        hints: List[str] = []
        for m in ctx.memories:
            content = (getattr(m, "content", "") or "").strip()
            if content:
                hints.append(content)
        return hints
