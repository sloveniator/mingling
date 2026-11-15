"""
hooks/base.py -- 注入钩子基类与调度链
====================================

钩子只通过调用方传入的 ``core`` 参数访问运行时依赖（鸭子类型），
不导入 core 模块本身，因此 hooks 子包与 core.py 之间不存在循环依赖。
"""
from astrbot.api import logger


class HookProcessor:
    """LLM 注入钩子基类。每个子类处理一类注入逻辑。"""
    async def process(self, core, event, req, **kwargs):
        raise NotImplementedError


class HookChain:
    """钩子链：按序执行所有处理器。

    协议：``process`` 返回 False 表示「已接管本次注入」，链条就此中断，
    后续钩子不再执行。此前 run() 直接丢弃返回值，各钩子里的 ``return True``
    形同虚设，也就无法表达「危机场景下不要再叠加祝福/玩笑注入」这类优先级。
    单个钩子抛异常只记录并跳过，不影响其余钩子（注入是增强而非主链路）。
    """

    def __init__(self):
        self._hooks = []

    def register(self, hook: HookProcessor):
        self._hooks.append(hook)
        return self

    async def run(self, core, event, req, **kwargs) -> bool:
        for hook in self._hooks:
            try:
                if await hook.process(core, event, req, **kwargs) is False:
                    logger.debug(f"[mingbu] 钩子 {type(hook).__name__} 中断了后续注入")
                    return False
            except Exception as e:
                logger.debug(f"[mingbu] 钩子 {type(hook).__name__} 异常(忽略): {e}")
        return True
