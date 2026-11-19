from __future__ import annotations

from dataclasses import dataclass

DIVINATION_PREFIXES = (
    "query_bazi", "query_ziwei", "divination_", "query_qimen",
    "query_combined", "query_interpret", "health_advice",
)
COUNSELING_TOOLS = {"psychological_counsel"}
ALWAYS_ON = {"query_situation", "guided_reflection"}


@dataclass
class ToolVisibilityContext:
    is_private: bool
    has_bound_chart: bool
    counseling_active: bool


def apply_tool_visibility(req, ctx: ToolVisibilityContext, cfg) -> None:
    """
    通过 FunctionTool.active 控制工具是否进入运行时请求。
    不删除工具定义，避免影响其它插件。
    群聊域：命灵相关工具全部不可见。
    """
    func_tool = getattr(req, "func_tool", None)
    if func_tool is None:
        return
    tools = getattr(func_tool, "tools", None)
    if not tools:
        return

    for tool in tools:
        name = getattr(tool, "name", "")
        if not name:
            continue

        if not ctx.is_private:
            if name in ALWAYS_ON or name in COUNSELING_TOOLS or name.startswith(DIVINATION_PREFIXES):
                tool.active = False
            continue

        if name in ALWAYS_ON:
            tool.active = True
        elif name in COUNSELING_TOOLS:
            tool.active = bool(cfg.counseling_tools_enabled and ctx.counseling_active)
        elif name.startswith(DIVINATION_PREFIXES):
            tool.active = bool(cfg.divination_tools_enabled and ctx.has_bound_chart)
        # 其它未分类工具：不主动干预
