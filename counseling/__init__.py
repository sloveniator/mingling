"""counseling/ -- 朋友关心 & 神秘直觉引擎（子包）

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

from .crisis import (
    CRISIS_ACTION_KEYWORDS as CRISIS_ACTION_KEYWORDS,
)
from .crisis import (
    CRISIS_HOTLINES as CRISIS_HOTLINES,
)
from .crisis import (
    CRISIS_IDEATION_KEYWORDS as CRISIS_IDEATION_KEYWORDS,
)
from .crisis import (
    CRISIS_IN_PROGRESS_MARKERS as CRISIS_IN_PROGRESS_MARKERS,
)
from .crisis import (
    CRISIS_SELF_HARM as CRISIS_SELF_HARM,
)
from .crisis import (
    _crisis_in_progress as _crisis_in_progress,
)
from .crisis import (
    _crisis_level as _crisis_level,
)
from .crisis import (
    crisis_support_prompt as crisis_support_prompt,
)
from .crisis import (
    detect_crisis as detect_crisis,
)
from .crisis import (
    technical_guidance_prompt as technical_guidance_prompt,
)
from .emotion_lexicon import (
    DIVINATION_KEYWORDS as DIVINATION_KEYWORDS,
)
from .emotion_lexicon import (
    EMOTION_INTENSIFIERS as EMOTION_INTENSIFIERS,
)
from .emotion_lexicon import (
    EMOTION_INTENSITY as EMOTION_INTENSITY,
)
from .emotion_lexicon import (
    EMOTION_KEYWORDS as EMOTION_KEYWORDS,
)
from .emotion_lexicon import (
    EMOTION_LABELS as EMOTION_LABELS,
)
from .emotion_lexicon import (
    POSITIVE_KEYWORDS as POSITIVE_KEYWORDS,
)
from .prompts import CounselingPrompts as CounselingPrompts
from .session import (
    COUNSEL_STAGE_LABEL as COUNSEL_STAGE_LABEL,
)
from .session import (
    COUNSEL_STAGES as COUNSEL_STAGES,
)
from .session import (
    CounselingSession as CounselingSession,
)
from .session import (
    CounselingSessionManager as CounselingSessionManager,
)
from .session import (
    counseling_turn_prompt as counseling_turn_prompt,
)
from .therapy import (
    BOUNDARY_RULES as BOUNDARY_RULES,
)
from .therapy import (
    CORE_SKILLS as CORE_SKILLS,
)
from .therapy import (
    EMOTION_TO_THERAPY as EMOTION_TO_THERAPY,
)
from .therapy import (
    THERAPY_FRAMEWORKS as THERAPY_FRAMEWORKS,
)
from .tracker import (
    EmotionalSignal as EmotionalSignal,
)
from .tracker import (
    EmotionalTracker as EmotionalTracker,
)
from .tracker import (
    UserCounselingState as UserCounselingState,
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
