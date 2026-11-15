"""
constants.py -- 全插件共享常量
==============================
从 ``core.py`` 下沉而来。存在的理由有两个：

1. **打破循环依赖**。``star_mixins/`` 里的助手方法需要 KV 键名、命理方法
   集合等常量，而 ``core.py`` 又要导入这些 mixin 组装 ``MingbuStar``。
   若常量留在 ``core.py``，两者就会互相 import。常量下沉到本模块后，
   依赖方向变为单向：``constants ← star_mixins ← core``。

2. **消除手工复制的常量副本**。``background_watch.py`` 曾因为「导入 core
   会循环」而自行抄了一份 ``_MINGPAN_METHODS`` / ``_BUGUA_METHODS``，
   靠注释提醒「与 core 保持一致」——这种约定迟早漂移。现在它可以直接
   从本模块导入同一份定义。

本模块只依赖 ``counseling``（取情绪词库），不依赖任何上层模块，可被任意
模块安全导入。``core.py`` 继续 re-export 全部符号，故 ``from core import
KV_USERS`` 等既有写法（含现有测试）保持可用。
"""

import os as _os
import re as _re

from .counseling import EMOTION_KEYWORDS

# ============================================================
# KV 键名
# ============================================================
KV_USERS = "mingbu.users"
KV_PROFILE = "mingbu.profile"
KV_CONTEXT = "mingbu.context"

# 上下文保留的最大消息条数（旧接口遗留，新代码用 TURN_HISTORY）
MAX_CONTEXT_MSGS = 5

# ============================================================
# 服务端 / 插件标识
# ============================================================
MINGBU_API_URL = _os.environ.get("MINGBU_API_URL", "http://localhost:2963")

# 插件名（用于 AstrBot Plugin Pages 的 Web API 路由前缀，必须与目录名一致）
PLUGIN_NAME = "mingling"

# ============================================================
# 命理方法（命盘类 / 卜卦类）—— 用于每月后台测算分流
# ============================================================
# 命盘类：紫微 / 八字 —— 后台测算 大限 / 流年 / 流月
MINGPAN_METHODS = {"ziwei", "bazi", "combined"}
# 卜卦类：六爻 / 奇门 / 梅花 —— 后台卜问情况
BUGUA_METHODS = {"liuyao", "qimen", "meihua"}
# 全部可选方法（含 auto 自动推断）
MINGBU_METHODS_ALL = MINGPAN_METHODS | BUGUA_METHODS | {"auto"}
# 卜卦类中无独立服务端端点的（仅前端引擎，如梅花易数），桥接到 liuyao
BUGUA_BRIDGE_TO_LIUYAO = {"meihua"}

# ============================================================
# 超时
# ============================================================
# 系统级 LLM 生成超时（秒）。避免 LLM 卡死时阻塞整条协程
LLM_TEXT_TIMEOUT = 45

# ============================================================
# 预编译正则（避免在类体里反复构建/编译）
# ============================================================

# 情绪关键词聚合：合并所有情绪类型的关键词，去重，长词优先（避免短词误匹配）
_EMOTION_KEYWORDS_ALL = sorted(
    {kw for kws in EMOTION_KEYWORDS.values() for kw in kws},
    key=len, reverse=True,
)
EMOTION_KEYWORD_PATTERN = "|".join(_re.escape(kw) for kw in _EMOTION_KEYWORDS_ALL)
EMOTION_KEYWORD_REGEX = _re.compile(EMOTION_KEYWORD_PATTERN)  # 供程序化匹配/测试复用

# 出生年 / 属相 提取（原在方法内联，现预编译）
# v4.8.105 收紧：排除「范围年」误采——用户说"2022-2035年的感情运势"时，
# "-2035年" 的后段被负向后瞻 (?<!-) 排除；"2035年之后/2035年前" 这类
# 未来参照同样不是出生年，由负向前瞻 (?!之|以|前|后) 兜底。
# 注意：不能排除"年"后直接跟数字——那是月日（"1990年5月20日"），必须保留。
# 注意：此处正则只做「候选年份」粗提取，真正语义过滤（出生关键词）在
# main._extract_birth_year_from_msg 里做——那才是第一道闸。
BIRTH_YEAR_RE = _re.compile(r"(?<!-)(\d{4})\s*年(?!之|以|前|后)")
ZODIAC_RE = _re.compile(r"我属([鼠牛虎兔龙蛇马羊猴鸡狗猪])")
