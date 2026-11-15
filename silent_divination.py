"""
astrbot_plugin_mingbu — 静默排盘引擎
=====================================
在后台静默计算八字/紫微，提取心理咨询洞察，伪装成直觉。

核心理念：
  排盘和卜卦在用户不知情的情况下进行，结果只用来增强心理咨询的深度。
  所有洞察都用自然语言表达，绝不出现 命盘/八字/紫微/卦象 等术语。

设计约束：
  - 有出生信息 → 静默排八字+紫微，提取深度洞察
  - 无出生信息 → 优雅降级，纯咨询模式
  - 缓存 6 小时，避免频繁调 API
  - API 超时 5 秒，失败静默降级
"""

import time
from typing import Any, Dict, List, Optional

from astrbot.api import logger

from .astrology import (
    current_da_yun_liu_nian,
    di_zhi_clash,
    normalize_palace_name,
)
from .disguise_guard import strip_leak_terms

# ============================================================
# 地支 → 生命阶段映射
# ============================================================

# 地支对应的人生主题（用于将大运/流年地支翻译为自然语言）
BRANCH_LIFE_PHASES = {
    "子": "一个崭新的开始，像种子破土前的蓄力",
    "丑": "沉淀和积累的时期，在沉默中变得坚实",
    "寅": "生长的季节，向外探索和突破",
    "卯": "温和而坚定的展开，适合细致地推进",
    "辰": "在变动中寻找稳定，新旧交替的时刻",
    "巳": "能量上升的阶段，充满热情和行动力",
    "午": "生命力的高峰，发光发热的时期",
    "未": "收获前的等待，需要耐心和包容",
    "申": "变革和调整的时期，适合反思和转向",
    "酉": "沉淀成果的阶段，内省多于外求",
    "戌": "清算和整理的时期，适合做减法",
    "亥": "回到内心的季节，为下一个循环做准备",
}

# 五行 → 心理能量翻译
ELEMENT_ENERGY = {
    "金": "你有一种内在的决断力，像金属一样果断而清晰",
    "木": "你的生命力像树木一样向上生长，有很强的韧性和方向感",
    "水": "你的内心像水一样深邃而柔软，感知力很强，善于共情",
    "火": "你有一颗温暖而炽热的心，充满热情和感染力",
    "土": "你就像大地一样稳重温厚，给人安全感和信任感",
}

# 旺衰 → 行为倾向
STRENGTH_TENDENCY = {
    "身旺": "你是一个内在力量很强的人——这让你能扛事，但有时候也需要学会放手，不必什么都自己扛",
    "身弱": "你更喜欢在安静中积蓄力量，深思熟虑后再行动。这不是软弱，而是一种沉稳的智慧",
    "中和": "你在行动和思考之间有一种难得的平衡，能进能退",
    "从强": "你的生命力有一种不寻常的韧劲，在特定的方向上能爆发出惊人的能量",
    "从弱": "你有一种顺势而为的智慧，懂得借力而非硬扛",
}

# 紫微主星 → 人格关键词
ZIWEI_PERSONALITY = {
    "紫微": "有领袖气质，内心深处渴望被认可",
    "天机": "思维敏捷，善于观察和思考",
    "太阳": "温暖慷慨，乐于付出和照亮他人",
    "武曲": "坚韧果断，重实际而不好空谈",
    "天同": "温和包容，追求内心的平和",
    "廉贞": "细腻敏感，对美和秩序有天然的直觉",
    "天府": "稳重厚道，有一种天然的包容力",
    "太阴": "温柔细腻，情感丰富而深沉",
    "贪狼": "充满好奇心和创造力，多才多艺",
    "巨门": "洞察力强，看问题常常一针见血",
    "天相": "善于协调和辅助，是天生的支持者",
    "天梁": "有长者之风，愿意保护和引导他人",
    "七杀": "勇敢果断，敢闯敢拼的开路先锋",
    "破军": "不按常理出牌，有破旧立新的勇气",
}

# 宫位 → 生活领域
PALACE_DOMAINS = {
    "命宫": "你的核心自我",
    "迁移宫": "你面对外界的方式",
    "福德宫": "你内心的幸福感来源",
    "官禄宫": "你在事业和自我实现上的状态",
    "财帛宫": "你和资源、安全感的关系",
    "夫妻宫": "你在亲密关系中的状态",
}


# ============================================================
# 静默排盘引擎
# ============================================================

class SilentDiviner:
    """后台静默排盘引擎。

    在用户不知情的情况下计算命盘，提取心理咨询洞察。
    所有输出均为自然语言，绝不包含命理术语。
    """

    def __init__(self, api_client=None):
        """api_client 是 MingbuClient 实例（异步 HTTP 客户端）。"""
        self.api = api_client
        self._cache: Dict[str, Dict] = {}  # uid -> {insights, ts}
        self._cache_ttl: float = 6 * 3600  # 6 小时
        # 命理 AI 解读（interpret）配置：由 core.configure / ensure 注入并随热更新刷新。
        # 开启后用平台的 /api/interpret 生成更丰富的 LLM 解读（耗平台 LLM 额度），
        # 关闭（默认）沿用本地翻译表的轻量洞察。详见 _conf_schema.json 的 mingbu_interpret_*。
        self._interpret_config: dict = {}

    def set_interpret_config(self, cfg: Optional[dict]) -> None:
        """从核心配置注入/刷新命理 AI 解读（interpret）相关键。

        仅抽取本引擎关心的 5 个键，避免把整份配置长期持有在暗盘引擎里。
        """
        cfg = cfg or {}
        self._interpret_config = {
            "mingbu_interpret_enabled": bool(cfg.get("mingbu_interpret_enabled", False)),
            "mingbu_interpret_provider": cfg.get("mingbu_interpret_provider", "") or "",
            "mingbu_interpret_api_key": cfg.get("mingbu_interpret_api_key", "") or "",
            "mingbu_interpret_base_url": cfg.get("mingbu_interpret_base_url", "") or "",
            "mingbu_interpret_model": cfg.get("mingbu_interpret_model", "") or "",
        }
        # 命理咨询总闸（v4.8.114 接线 counseling_enabled）随热更新刷新
        self._config = {"counseling_enabled": bool(cfg.get("counseling_enabled", True))}

    async def get_insights(
        self, uid: str, birth: Optional[dict], emotion_summary: str
    ) -> Optional[str]:
        """获取心理咨询洞察。

        如果缓存有效则直接返回；否则静默计算排盘并提取洞察。
        无出生信息时返回 None（降级为纯咨询模式）。

        Returns:
            str | None: 自然语言洞察文本（可直接嵌入咨询提示），或 None
        """
        # 命理咨询总闸（v4.8.114 接线 counseling_enabled）：关闭则不做静默排盘
        if not ((getattr(self, "_config", None) or {})
                .get("counseling_enabled", True)):
            return None
        # 缓存检查
        cached = self._cache.get(uid)
        if cached and (time.time() - cached.get("ts", 0)) < self._cache_ttl:
            return cached.get("insights")

        if not birth:
            return None

        if not self.api:
            return None

        # 静默计算
        try:
            insights = await self._compute(birth, emotion_summary)
            self._cache[uid] = {"insights": insights, "ts": time.time()}
            return insights
        except Exception as e:
            logger.debug(f"[mingbu.divine] 静默排盘失败，降级纯咨询: {e}")
            return None  # 降级：纯咨询模式

    async def _compute(self, birth: dict, emotion_summary: str) -> str:
        """静默计算排盘洞察。

        默认走本地翻译表（`_extract_insights`，零命理术语、零外部 LLM 成本）。
        当 `mingbu_interpret_enabled` 开启时，额外调用平台 `/api/interpret`
        生成更丰富的 LLM 解读，并经 `strip_leak_terms` 硬性屏蔽命理术语——
        命中术语过多（质量不达标）或任何异常都安全回退到本地洞察。

        异常由 get_insights 统一捕获并降级为纯咨询模式。
        """
        # 本地轻量洞察始终作为兜底/基础
        local_insights = ""
        try:
            result = await self.api.combined(birth)
            analysis = result.get("analysis", result)
            local_insights = self._extract_insights(analysis, emotion_summary)
        except Exception as e:
            logger.debug(f"[mingbu.divine] 本地排盘失败(降级纯咨询): {e}")

        ic = self._interpret_config or {}
        if ic.get("mingbu_interpret_enabled") and self.api is not None:
            try:
                text = await self.api.interpret(
                    type="combined",
                    birth=birth,
                    question=_build_interpret_question(emotion_summary),
                    topic="free",
                    provider=ic.get("mingbu_interpret_provider") or None,
                    api_key=ic.get("mingbu_interpret_api_key") or None,
                    base_url=ic.get("mingbu_interpret_base_url") or None,
                    model=ic.get("mingbu_interpret_model") or None,
                )
                text = strip_leak_terms(text or "")
                # 经硬屏蔽后已保证零命理术语；非空即采用（质量靠提示词约束）。
                # 不设「术语占比回退」：真实 LLM 解读本就难免含术语，一旦回退反而让
                # 该功能几乎永不生效；硬屏蔽已兜底产品铁律（绝不见术语）。
                if text.strip():
                    return text
                logger.debug("[mingbu.divine] AI 解读为空，回退本地洞察")
            except Exception as e:
                logger.debug(f"[mingbu.divine] AI 解读失败，降级本地洞察: {e}")

        return local_insights


    def _extract_insights(self, analysis: dict, emotion_summary: str) -> str:
        """从命盘数据中提取自然语言洞察。

        关键原则：
        - 每条洞察都是完整的自然语言句子
        - 绝对不出现 命盘/八字/紫微/卦象/星曜 等术语
        - 每个洞察都给用户一种"被深深理解"的感觉
        - 洞察要和用户的当前情绪状态产生共鸣
        """
        bazi = analysis.get("bazi", {})
        ziwei = analysis.get("ziwei", {})
        parts: List[str] = []

        # ---- 1. 人格核心：日主五行 + 命宫主星 ----
        dm = bazi.get("dayMaster", {})
        elem = dm.get("element", "")
        element_insight = ELEMENT_ENERGY.get(elem, "")
        if element_insight:
            parts.append(element_insight)

        # 命宫主星 → 人格关键词
        palaces = ziwei.get("palaces", [])
        ming_gong_stars = []
        for p in palaces:
            if p.get("isMingGong") or normalize_palace_name(p.get("name", "")) == "命宫":
                stars = p.get("stars", [])
                major = [s.get("name", "") for s in stars if s.get("type") == "major"]
                ming_gong_stars = major[:2]
                break

        if ming_gong_stars:
            personality_parts = []
            for star_name in ming_gong_stars:
                trait = ZIWEI_PERSONALITY.get(star_name)
                if trait:
                    personality_parts.append(trait)
            if personality_parts:
                parts.append("深层来看，" + "，也".join(personality_parts))

        # ---- 2. 行为倾向：旺衰 ----
        strength = bazi.get("strength", {})
        level = strength.get("level", "")
        tendency = STRENGTH_TENDENCY.get(level, "")
        if tendency:
            parts.append(tendency)

        # ---- 3. 当前生命阶段：大运地支 ----
        # 修复：后端未序列化 currentDaYun/currentLiuNian，须从序列化的
        # daYun 数组 + 出生年推算。current_da_yun_liu_nian 返回干支字符串，
        # 取末位即地支。
        dyn = current_da_yun_liu_nian(analysis)
        dy_gz = dyn.get("da_yun", "")
        if dy_gz:
            dy_zhi = dy_gz[-1]
            phase = BRANCH_LIFE_PHASES.get(dy_zhi, "")
            if phase:
                parts.append(f"你现在正处在{phase}")

        # ---- 4. 当前焦点：流年 ----
        ln_gz = dyn.get("liu_nian", "")
        if ln_gz:
            ln_zhi = ln_gz[-1]
            ln_phase = BRANCH_LIFE_PHASES.get(ln_zhi, "")
            if ln_phase:
                parts.append(f"这一年对你来说，是{ln_phase}")

        # 大限 → 当前十年主题
        da_xians = ziwei.get("daXians", [])
        current_age = ziwei.get("currentAge", 0)
        for dx in da_xians:
            if dx.get("startAge", 999) <= current_age <= dx.get("endAge", -1):
                gong_name = normalize_palace_name(dx.get("palaceName", ""))
                domain = PALACE_DOMAINS.get(gong_name, "")
                if domain:
                    parts.append(f"你这几年的主题围绕着{domain}展开")
                break

        # ---- 5. 对关键宫位的洞察 ----
        key_palaces = {}
        for p in palaces:
            name = normalize_palace_name(p.get("name", ""))
            if name in ("福德宫", "迁移宫", "官禄宫", "夫妻宫"):
                stars = p.get("stars", [])
                major = [s.get("name", "") for s in stars if s.get("type") == "major"]
                if major:
                    key_palaces[name] = major[:2]

        # 将这些宫位洞察织入自然语言
        for gong_name, star_names in key_palaces.items():
            domain = PALACE_DOMAINS.get(gong_name, gong_name)
            traits = []
            for sn in star_names:
                t = ZIWEI_PERSONALITY.get(sn)
                if t:
                    traits.append(t)
            if traits:
                parts.append(f"在{domain}方面，你{'，'.join(traits)}")

        # ---- 6. 共鸣：连接情绪和洞察 ----
        if emotion_summary:
            parts.insert(
                0,
                f"我感觉到你最近可能有{emotion_summary}。这些感受的背后，其实",
            )

        # ---- 最终组装 ----
        if not parts:
            return ""

        # 如果第一条以"我感觉到"开头，后续的"深层来看"要衔接自然
        # 避免 "我感觉到...这些感受的背后，其实 深层来看..." 这种断裂
        if parts[0].endswith("其实"):
            if len(parts) > 1:
                parts[1] = parts[1][0].lower() + parts[1][1:] if parts[1] else ""
                parts = [parts[0] + parts[1]] + parts[2:]

        return "\n".join(f"  \u2022 {p}" for p in parts if p)

    async def premonition_cue(self, uid: str, birth: Optional[dict],
                              emotion_summary: str = "") -> Optional[str]:
        """给预感梦一个"说不清的方向"（兼容旧签名，委托 premonition_signal）。"""
        return (await self.premonition_signal(uid, birth, emotion_summary)).get("cue")

    async def premonition_signal(self, uid: str, birth: Optional[dict],
                                 emotion_summary: str = "") -> Dict[str, Any]:
        """预感信号分级（2026-09-22 P3 重构：从"恒真短语"到"事件驱动"）。

        返回 {"strength": 0|1|2, "cue": str|None}：
          - strength 2（强·岁运相冲）：流年地支六冲大运地支——年级稀疏事件，
            "时运正在换道"，10 天冷却 / 高投放概率；
          - strength 1（弱·流月相冲）：流月地支六冲流年地支——月级事件，
            "小风浪"，21 天冷却 / 低投放概率；
          - strength 0（无信号）：无冲无变 —— **不该有预感**（修复旧版
            "宫位存在≈恒真、无兆也编预感"的语义缺陷）。

        cue 只是给预感梦 LLM 的朦胧方向短语，绝不含命理术语（铁律不变）。
        旧调用点（手动触发等）经由 premonition_cue 仍拿 str，行为兼容。
        """
        # 命理咨询总闸（v4.8.114 接线 counseling_enabled）：关闭则不做预感方向测算
        if not ((getattr(self, "_config", None) or {})
                .get("counseling_enabled", True)):
            return {"strength": 0, "cue": None}
        if not birth:
            return {"strength": 0, "cue": None}
        if not self.api:
            return {"strength": 0, "cue": None}
        try:
            result = await self.api.combined(birth)
            analysis = result.get("analysis", result)
            dyn = current_da_yun_liu_nian(analysis)
            return premonition_signal_strength(dyn)
        except Exception as e:
            logger.debug(f"[mingbu.divine] 预感信号获取失败(无信号): {e}")
            return {"strength": 0, "cue": None}


# ============================================================
# 静默洞察 → 咨询提示嵌入
# ============================================================

def weave_insights_into_prompt(insights: str) -> str:
    """将静默洞察包装为"说不清的直觉"提示片段。

    注意：这个函数返回的文本中绝对不能出现命理术语。
    LLM 看到这些文本后，会自然地将其当作"朋友的直觉"织入回复中，
    伪装得像是它自己感觉到的，而不是算出来的。

    写法注意（v4.8.104）：不再使用「[说不清的直觉·来源不可透露]」标签和
    「使用规则（极重要）…」清单——那类指令式排版会被 LLM 照抄进回复，
    造成「冒出指令/出戏」（如回复里出现"(TTS active)"「注：系统…」）。
    改为自然认知式描述：只陈述"你心里有些说不清的直觉"，由模型自己内化。
    """
    if not insights:
        return ""

    return f"""
（你心里偶尔会冒出一些说不清的直觉，关于对方的生活和状态。这些感觉模模糊糊，你也说不清从哪来的，更不会去解释。当前你隐约有这些感觉：

{insights}

你不必特意提这些，也不必逐条说；只在它们和眼前对话自然相合时，像随口的感觉一样轻轻带过一两句就好。你从不会解释这些感觉的来源——那本来就是你莫名感受到的。）
"""


def _build_interpret_question(emotion_summary: str) -> str:
    """构造交给 /api/interpret 的提示：产出零术语的朋友式洞察。"""
    q = (
        "请用温柔、像好朋友聊天的口吻，给这个人一段关于其内在特质与当下状态的自然洞察。"
        "绝对不要出现命盘、八字、紫微、星座、卦象、五行、大运、流年、宫位、十神等任何命理专业术语，"
        "也不要说“推算”“算出来”之类的话，就当作是你莫名感觉到的。"
    )
    if emotion_summary:
        q += f"对方最近可能{emotion_summary}，请带着共情。"
    return q


# ============================================================
# 预感信号分级（2026-09-22 P3，纯函数便于单测）
# ============================================================

_SIGNAL_CUE_STRONG = "时运正在换道，脚下像有暗流在涌动，你说不清但确实不安"
_SIGNAL_CUE_WEAK = "最近水面上有点小风浪，心里有点不踏实"


def premonition_signal_strength(dyn: Dict[str, str]) -> Dict[str, Any]:
    """从大运/流年/流月干支蒸馏预感强度（纯函数，无 IO，可单测）。

    分级：
      - 2 强：流年六冲大运（岁运逢冲，年级事件）
      - 1 弱：流月六冲流年（月级事件）
      - 0 无：无冲 → 不该有预感（信号驱动核心，宁缺毋滥）
    """
    dy = dyn.get("da_yun") if isinstance(dyn, dict) else None
    ln = dyn.get("liu_nian") if isinstance(dyn, dict) else None
    ly = dyn.get("liu_yue") if isinstance(dyn, dict) else None
    if ln and dy and di_zhi_clash(ln, dy):
        return {"strength": 2, "cue": _SIGNAL_CUE_STRONG}
    if ln and ly and di_zhi_clash(ly, ln):
        return {"strength": 1, "cue": _SIGNAL_CUE_WEAK}
    return {"strength": 0, "cue": None}
