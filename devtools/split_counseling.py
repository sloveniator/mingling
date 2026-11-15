#!/usr/bin/env python
"""一次性脚本：把 counseling.py 切分为 counseling/ 子包。

做法：用 AST 取每个顶层 def/assign/class 的精确起止行，按名称映射到子模块，
再对每一组做「连续行切片」导出原始源码（保留内部注释与格式），最后在子模块
顶部补上 docstring + 精确 import。__init__.py 用 PEP 484 的 ``X as X`` 显式
re-export，保持 ``from counseling import X`` 与 ``from .counseling import X`` 兼容。

运行后需删除 counseling.py，并由 astrbot_stub.py 把 counseling 当包预加载。
"""
from __future__ import annotations

import ast
import os

PLUGIN_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(PLUGIN_DIR, "counseling.py")

if not os.path.exists(SRC):
    print("counseling.py 已不存在（拆分已完成），本一次性脚本无需运行。")
    raise SystemExit(0)

with open(SRC, encoding="utf-8") as f:
    lines = f.readlines()

tree = ast.parse("".join(lines))


def _is_setdefault(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Call)
        and isinstance(node.value.func, ast.Attribute)
        and node.value.func.attr == "setdefault"
        and isinstance(node.value.func.value, ast.Name)
        and node.value.func.value.id == "EMOTION_KEYWORDS"
    )


# 顶层节点 → 目标子模块
MODULE_OF = {
    # emotion_lexicon
    "EMOTION_KEYWORDS": "emotion_lexicon",
    "EMOTION_LABELS": "emotion_lexicon",
    "EMOTION_INTENSITY": "emotion_lexicon",
    "EMOTION_INTENSIFIERS": "emotion_lexicon",
    "POSITIVE_KEYWORDS": "emotion_lexicon",
    "DIVINATION_KEYWORDS": "emotion_lexicon",
    # crisis
    "CRISIS_IDEATION_KEYWORDS": "crisis",
    "CRISIS_ACTION_KEYWORDS": "crisis",
    "CRISIS_IN_PROGRESS_MARKERS": "crisis",
    "CRISIS_HOTLINES": "crisis",
    "CRISIS_SELF_HARM": "crisis",
    "_crisis_in_progress": "crisis",
    "_crisis_level": "crisis",
    "detect_crisis": "crisis",
    "crisis_support_prompt": "crisis",
    "technical_guidance_prompt": "crisis",
    # therapy
    "CORE_SKILLS": "therapy",
    "THERAPY_FRAMEWORKS": "therapy",
    "EMOTION_TO_THERAPY": "therapy",
    "BOUNDARY_RULES": "therapy",
    # tracker
    "EmotionalSignal": "tracker",
    "UserCounselingState": "tracker",
    "EmotionalTracker": "tracker",
    # prompts
    "CounselingPrompts": "prompts",
    # session
    "COUNSEL_STAGES": "session",
    "COUNSEL_STAGE_LABEL": "session",
    "CounselingSession": "session",
    "CounselingSessionManager": "session",
    "counseling_turn_prompt": "session",
}


def _name_of(node: ast.AST) -> str | None:
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return node.name
    if isinstance(node, ast.Assign) and len(node.targets) == 1:
        t = node.targets[0]
        if isinstance(t, ast.Name):
            return t.id
    return None


# 收集每个子模块的节点（保持源码顺序）
nodes_by_module: dict[str, list[ast.AST]] = {m: [] for m in set(MODULE_OF.values())}
for node in tree.body:
    name = _name_of(node)
    if name is not None and name in MODULE_OF:
        nodes_by_module[MODULE_OF[name]].append(node)
        continue
    if _is_setdefault(node):
        nodes_by_module["emotion_lexicon"].append(node)

# 子模块顶部（docstring + import）
HEADERS = {
    "emotion_lexicon": '''"""counseling/emotion_lexicon.py -- 情绪与命理意图关键词库

纯数据模块：情绪分类关键词、强度、加重词、积极信号、命理意图词。
被 tracker / prompts / core 等多个模块引用，无任何运行期依赖。
"""

''',
    "crisis": '''"""counseling/crisis.py -- 危机识别（红线）与对应提示词

最高优先级的自我伤害 / 轻生意向检测与分级，以及对应的 LLM 提示注入模板。
"""

import re
from typing import Optional

''',
    "therapy": '''"""counseling/therapy.py -- 咨询流派 / 技能工具箱 / 边界守则

纯数据模块：五大核心技能、五大流派、情绪→疗法映射、朋友边界守则。
"""

''',
    "tracker": '''"""counseling/tracker.py -- 情绪信号记录与状态机

EmotionalSignal / UserCounselingState / EmotionalTracker：
逐用户追踪情绪信号，驱动朋友式关心钩子与探索 / 祝福状态机。
"""

import time
from collections import Counter
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from astrbot.api import logger

from .emotion_lexicon import (
    EMOTION_INTENSIFIERS,
    EMOTION_INTENSITY,
    EMOTION_KEYWORDS,
    EMOTION_LABELS,
    POSITIVE_KEYWORDS,
)

''',
    "prompts": '''"""counseling/prompts.py -- 心理咨询式提示模板

CounselingPrompts：生成朋友式关心钩子、命理解读引导、神秘鼓励、自我探查引导。
情绪自适应地选择疗法组合（依赖 therapy / tracker）。
"""

from typing import Optional

from .emotion_lexicon import DIVINATION_KEYWORDS, EMOTION_LABELS
from .therapy import (
    BOUNDARY_RULES,
    CORE_SKILLS,
    EMOTION_TO_THERAPY,
    THERAPY_FRAMEWORKS,
)
from .tracker import EmotionalTracker

''',
    "session": '''"""counseling/session.py -- 多轮心理疏导会话与管理

COUNSEL_STAGES / CounselingSession / CounselingSessionManager /
counseling_turn_prompt：保持多轮疏导连贯性，内存缓存 + KV 持久化。
"""

import json
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from astrbot.api import logger

from .prompts import CounselingPrompts

''',
}

OUT_DIR = os.path.join(PLUGIN_DIR, "counseling")
os.makedirs(OUT_DIR, exist_ok=True)

for module, nodes in nodes_by_module.items():
    assert nodes, f"module {module} has no nodes"
    nodes_sorted = sorted(nodes, key=lambda n: n.lineno)
    start = nodes_sorted[0].lineno
    end = max(n.end_lineno for n in nodes_sorted)
    block = "".join(lines[start - 1 : end])
    content = HEADERS[module].rstrip("\n") + "\n\n" + block
    if not content.endswith("\n"):
        content += "\n"
    out_path = os.path.join(OUT_DIR, f"{module}.py")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(content)
    print(f"wrote {out_path}  (lines {start}-{end})")

# __init__.py
INIT = '''"""counseling/ -- 朋友关心 & 神秘直觉引擎（子包）

原单一 1108 行 counseling.py 拆分为职责单一的子模块：

- emotion_lexicon : 情绪 / 命理意图 关键词库
- crisis          : 危机识别（红线）与对应提示词
- therapy         : 咨询流派 / 技能工具箱 / 边界守则
- tracker         : 情绪信号记录与状态机（EmotionalTracker）
- prompts         : 心理咨询式提示模板（CounselingPrompts）
- session         : 多轮疏导会话与会话管理

所有对外符号在 __init__ 中 re-export，``from counseling import X`` 与
``from .counseling import X`` 均保持可用。

# 全局实例在插件装配时由 core.py 创建（tracker = EmotionalTracker()）。
"""

from .emotion_lexicon import (
    DIVINATION_KEYWORDS as DIVINATION_KEYWORDS,
    EMOTION_INTENSIFIERS as EMOTION_INTENSIFIERS,
    EMOTION_INTENSITY as EMOTION_INTENSITY,
    EMOTION_KEYWORDS as EMOTION_KEYWORDS,
    EMOTION_LABELS as EMOTION_LABELS,
    POSITIVE_KEYWORDS as POSITIVE_KEYWORDS,
)
from .crisis import (
    CRISIS_ACTION_KEYWORDS as CRISIS_ACTION_KEYWORDS,
    CRISIS_HOTLINES as CRISIS_HOTLINES,
    CRISIS_IDEATION_KEYWORDS as CRISIS_IDEATION_KEYWORDS,
    CRISIS_IN_PROGRESS_MARKERS as CRISIS_IN_PROGRESS_MARKERS,
    CRISIS_SELF_HARM as CRISIS_SELF_HARM,
    _crisis_in_progress as _crisis_in_progress,
    _crisis_level as _crisis_level,
    crisis_support_prompt as crisis_support_prompt,
    detect_crisis as detect_crisis,
    technical_guidance_prompt as technical_guidance_prompt,
)
from .therapy import (
    BOUNDARY_RULES as BOUNDARY_RULES,
    CORE_SKILLS as CORE_SKILLS,
    EMOTION_TO_THERAPY as EMOTION_TO_THERAPY,
    THERAPY_FRAMEWORKS as THERAPY_FRAMEWORKS,
)
from .tracker import (
    EmotionalSignal as EmotionalSignal,
    EmotionalTracker as EmotionalTracker,
    UserCounselingState as UserCounselingState,
)
from .prompts import CounselingPrompts as CounselingPrompts
from .session import (
    COUNSEL_STAGES as COUNSEL_STAGES,
    COUNSEL_STAGE_LABEL as COUNSEL_STAGE_LABEL,
    CounselingSession as CounselingSession,
    CounselingSessionManager as CounselingSessionManager,
    counseling_turn_prompt as counseling_turn_prompt,
)

__all__ = [
    "DIVINATION_KEYWORDS", "EMOTION_INTENSIFIERS", "EMOTION_INTENSITY",
    "EMOTION_KEYWORDS", "EMOTION_LABELS", "POSITIVE_KEYWORDS",
    "CRISIS_ACTION_KEYWORDS", "CRISIS_HOTLINES", "CRISIS_IDEATION_KEYWORDS",
    "CRISIS_IN_PROGRESS_MARKERS", "CRISIS_SELF_HARM",
    "_crisis_in_progress", "_crisis_level",
    "crisis_support_prompt", "detect_crisis", "technical_guidance_prompt",
    "BOUNDARY_RULES", "CORE_SKILLS", "EMOTION_TO_THERAPY", "THERAPY_FRAMEWORKS",
    "EmotionalSignal", "EmotionalTracker", "UserCounselingState",
    "CounselingPrompts",
    "COUNSEL_STAGES", "COUNSEL_STAGE_LABEL", "CounselingSession",
    "CounselingSessionManager", "counseling_turn_prompt",
]
'''
with open(os.path.join(OUT_DIR, "__init__.py"), "w", encoding="utf-8") as f:
    f.write(INIT)
print(f"wrote {os.path.join(OUT_DIR, '__init__.py')}")
print("DONE")
