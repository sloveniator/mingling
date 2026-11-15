"""
devtools/split_star_mixins.py -- 一次性拆分脚本：MingbuStar -> star_mixins/
==========================================================================
把 ``core.py`` 中 ``MingbuStar`` 的 30 个助手方法按职责切分为多个 mixin 模块。

做法：用 AST 定位 ``MingbuStar`` 类体内每个成员的 ``lineno``/``end_lineno``，
按行范围原样切片，保证方法体**字节级**与原文一致（不重排、不改写）。

上一轮拆 ``counseling.py`` 踩过的坑：``ClassDef.lineno`` 指向 ``class`` 行
而非装饰器行，导致 ``@dataclass`` 被切掉。本脚本改用
``min(decorator_list.lineno)`` 兜底，虽然当前这批方法都没有装饰器。

本脚本仅用于一次性迁移，执行后 ``core.py`` 会被改写，故不可重复运行。
"""
import ast
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN_DIR = os.path.dirname(HERE)
SRC = os.path.join(PLUGIN_DIR, "core.py")
OUT_DIR = os.path.join(PLUGIN_DIR, "star_mixins")

# ---- mixin 划分：模块名 -> (类名, 模块 docstring, 成员名列表) ----
GROUPS = [
    (
        "llm",
        "LLMTextMixin",
        "系统级 LLM 文本生成（带防递归 + 超时兜底 + Token 监控）。",
        ["_llm_text"],
    ),
    (
        "relations",
        "RelationshipMixin",
        "用户关系读写与活跃用户索引维护。\n\n"
        "``_remember_user`` 位于热路径（每条消息都会走到），命中内存集合时零 IO。",
        ["_get_relationship", "_save_relationship", "_remember_user", "_active_uids"],
    ),
    (
        "persona_ctx",
        "PersonaContextMixin",
        "人格块拼装与记忆召回（语义向量召回 / rerank 重排，均优雅降级）。",
        ["_build_persona_block", "_rerank_memories", "_recall_semantic", "_save_persona"],
    ),
    (
        "group",
        "GroupChatMixin",
        "群聊与好友场景辅助：@ 检测、首次私聊 onboarding。",
        ["_bot_mentioned", "_maybe_onboard"],
    ),
    (
        "profile",
        "ProfileMixin",
        "用户档案与出生信息：读写、参数归一、方法推断、合法性校验。",
        [
            "_get_profile", "_save_profile", "_birth_from", "_birth_params",
            "_method_for", "_save_birth", "_validate_birth",
        ],
    ),
    (
        "conversation",
        "ConversationContextMixin",
        "聊天上下文管理：结构化轮次记录 + 话题追踪。",
        [
            "TURN_HISTORY", "_append_turn", "_get_turns",
            "_append_context", "_get_context", "_set_topic", "_get_topic",
        ],
    ),
    (
        "health",
        "HealthProfileMixin",
        "健康档案存取。",
        ["_get_health_profile", "_save_health_profile"],
    ),
    (
        "llm_hook",
        "LLMRequestHookMixin",
        "LLM 请求钩子主体：收集上下文 → 交给共享 HookChain 完成注入。",
        ["_on_llm_request_body"],
    ),
]

# ---- 各 mixin 模块需要的 import 头 ----
IMPORTS = {
    "llm": """import asyncio

from astrbot.api import logger
from astrbot.api.provider import ProviderRequest

from ..constants import LLM_TEXT_TIMEOUT
""",
    "relations": """import json

from astrbot.api import logger

from ..constants import KV_USERS
from ..persona import Relationship
""",
    "persona_ctx": """import json

from astrbot.api import logger

from ..memory_core import MemoryCore
from ..persona import Relationship
from ..provider_router import (
    embed_texts,
    get_embedding_provider,
    get_rerank_provider,
    rank_indices_by_vector,
    rerank_memory_list,
)
from ..worldview_adapter import adapt_system_prompt, auto_style_for_character
""",
    "group": """from datetime import datetime

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent
""",
    "profile": """import json
from datetime import datetime
from typing import Optional

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent

from ..constants import BUGUA_METHODS, KV_PROFILE, MINGPAN_METHODS
from ..formatters import _clock_to_shichen
""",
    "conversation": """import json
from datetime import datetime
from typing import List

from astrbot.api import logger

from ..constants import KV_CONTEXT
""",
    "health": """import json

from astrbot.api import logger

from ..health import HealthProfile
""",
    "llm_hook": """import asyncio
import random

from astrbot.api import logger

from ..counseling import CounselingPrompts, detect_crisis
from ..emotion_detect import detect_emotion, emotion_to_weight
from ..hooks import get_default_chain
from ..metrics import record_failure
""",
}

MODULE_HEADER = '''"""
star_mixins/{mod}.py -- {title}
{underline}
{doc}

本模块由 ``MingbuStar`` 混入。方法体内的 ``self.<engine>`` 访问依赖
``MingbuStar.__getattr__`` 代理到 ``MingbuCore``，因此 mixin 本身不 import
``core``，也就不产生循环依赖。
"""
{imports}

class {cls}:
    """{doc_first}"""

'''


def main() -> int:
    if not os.path.exists(SRC):
        print(f"[ABORT] 找不到 {SRC}")
        return 1

    with open(SRC, encoding="utf-8") as f:
        lines = f.readlines()
    tree = ast.parse("".join(lines))

    star = None
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "MingbuStar":
            star = node
            break
    if star is None:
        print("[ABORT] 未找到 MingbuStar 类")
        return 1

    # 收集成员 -> (start, end)，start 取装饰器首行（防重蹈 counseling 覆辙）
    members = {}
    for item in star.body:
        name = None
        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
            name = item.name
        elif isinstance(item, ast.Assign) and len(item.targets) == 1 \
                and isinstance(item.targets[0], ast.Name):
            name = item.targets[0].id
        if not name:
            continue
        start = item.lineno
        for dec in getattr(item, "decorator_list", []) or []:
            start = min(start, dec.lineno)
        members[name] = (start, item.end_lineno)

    assigned = {n for _, _, _, names in GROUPS for n in names}
    keep = {"__init__", "__getattr__"}
    missing = assigned - set(members)
    if missing:
        print(f"[ABORT] 这些成员在 MingbuStar 中不存在: {sorted(missing)}")
        return 1
    unassigned = set(members) - assigned - keep
    if unassigned:
        print(f"[ABORT] 有成员未被分配到任何 mixin: {sorted(unassigned)}")
        return 1

    os.makedirs(OUT_DIR, exist_ok=True)

    for mod, cls, doc, names in GROUPS:
        title = doc.split("\n")[0].rstrip("。")
        body_chunks = []
        for name in names:
            start, end = members[name]
            body_chunks.append("".join(lines[start - 1:end]).rstrip("\n"))
        header = MODULE_HEADER.format(
            mod=mod,
            title=title,
            underline="=" * (len(f"star_mixins/{mod}.py -- {title}") + 8),
            doc=doc,
            imports=IMPORTS[mod],
            cls=cls,
            doc_first=doc.split("\n")[0],
        )
        content = header + "\n\n".join(body_chunks) + "\n"
        path = os.path.join(OUT_DIR, f"{mod}.py")
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        print(f"[OK] {path}  ({len(content.splitlines())} 行, {len(names)} 个成员)")

    print("\n生成完成。core.py 需手动改写为组合 mixin。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
