"""
tarot.py -- 塔罗牌占卜引擎（纯逻辑模块，无网络依赖）
=====================================================
塔罗的本质是"抽取象征 + 由解读者（LLM/人）赋予意义"，
因此本模块只负责：牌库、牌阵、抽牌（可复现）、解读提示构建，
不调用任何外部命理服务。语义解读交给 LLM 在主回复中自然完成。

设计要点（与项目设定一致）：
  - 不出现"算命宣判"式绝对断语，塔罗只是映照当下状态的镜子
  - 抽牌使用可复现种子（问题 + 当日日期），同一天同一问题结果稳定，
    跨天自然变化，避免"同一句话每次抽都不一样"的廉价感
  - 纯函数 / 纯数据，便于单测，无 astrbot / aiohttp 依赖

牌库：78 张（22 大阿卡纳 + 56 小阿卡纳）
  - 大阿卡纳：逐张定义正位/逆位含义
  - 小阿卡纳：四花色（权杖/圣杯/宝剑/星币）× 14 张，由花色主题 + 数字主题组合生成，
    并对少量"名场面"牌做覆盖以增强质感
"""

from __future__ import annotations

import hashlib
import random
from typing import Any, Dict, List, Optional

from .bj_time import today_bj

TAROT_CARD_COUNT = 78

# ============================================================
# 大阿卡纳（22 张，逐张定义）
# ============================================================

_MAJOR_ARCANA: List[Dict[str, Any]] = [
    {"num": 0, "name": "愚者", "en": "The Fool",
     "up": "崭新的开始、纯真、自由与冒险的召唤",
     "rev": "鲁莽冲动、逃避现实、在岔路口犹豫不决"},
    {"num": 1, "name": "魔术师", "en": "The Magician",
     "up": "创造力涌动、行动力、潜能化为现实",
     "rev": "才干未被善用、操纵或自我怀疑"},
    {"num": 2, "name": "女祭司", "en": "The High Priestess",
     "up": "直觉觉醒、潜意识的智慧、静默中的答案",
     "rev": "忽视内在声音、秘密尚未揭晓、过度理性"},
    {"num": 3, "name": "皇后", "en": "The Empress",
     "up": "丰饶滋养、母性的包容、感官与创造力",
     "rev": "过度付出而耗尽、依赖或停滞"},
    {"num": 4, "name": "皇帝", "en": "The Emperor",
     "up": "秩序、权威、稳定与掌控力",
     "rev": "控制欲、固执、结构僵化"},
    {"num": 5, "name": "教皇", "en": "The Hierophant",
     "up": "传统、信念、引导与传承",
     "rev": "墨守成规、盲从、需要走出既定框架"},
    {"num": 6, "name": "恋人", "en": "The Lovers",
     "up": "爱、结合、重要的抉择与价值对齐",
     "rev": "关系失衡、在关键选择前摇摆"},
    {"num": 7, "name": "战车", "en": "The Chariot",
     "up": "凭借意志取胜、掌控方向与冲劲",
     "rev": "失控、方向迷失、内耗拉扯"},
    {"num": 8, "name": "力量", "en": "Strength",
     "up": "以柔克刚的勇气、内在的笃定与耐心",
     "rev": "自我怀疑、被情绪淹没、力不从心"},
    {"num": 9, "name": "隐者", "en": "The Hermit",
     "up": "内省、独处中沉淀智慧、向内求索",
     "rev": "孤立封闭、逃避人群、孤独感过重"},
    {"num": 10, "name": "命运之轮", "en": "Wheel of Fortune",
     "up": "转机将至、循环中的机遇、顺势而为",
     "rev": "抗拒改变、感到被运气抛下、起伏难安"},
    {"num": 11, "name": "正义", "en": "Justice",
     "up": "公正、因果明晰、平衡与承担",
     "rev": "偏见、逃避责任、尚未清算的账"},
    {"num": 12, "name": "倒吊人", "en": "The Hanged Man",
     "up": "主动暂停、换个视角、以退为进",
     "rev": "无谓拖延、牺牲没有换来成长"},
    {"num": 13, "name": "死神", "en": "Death",
     "up": "一段的结束与重生、必要的蜕变",
     "rev": "抗拒结束、对改变心怀恐惧"},
    {"num": 14, "name": "节制", "en": "Temperance",
     "up": "平衡、调和、耐心中找到中道",
     "rev": "失衡、走向极端、急于求成"},
    {"num": 15, "name": "恶魔", "en": "The Devil",
     "up": "觉察束缚、欲望与执念的面纱",
     "rev": "挣脱枷锁、从执念中醒来"},
    {"num": 16, "name": "高塔", "en": "The Tower",
     "up": "突如其来的崩塌，却也是谎言破碎后的觉醒",
     "rev": "灾后重建、从震荡里慢慢站稳"},
    {"num": 17, "name": "星星", "en": "The Star",
     "up": "希望、疗愈、在黑暗后看见指引的光",
     "rev": "信心动摇、短暂的失望与迷茫"},
    {"num": 18, "name": "月亮", "en": "The Moon",
     "up": "潜意识的迷雾、直觉与不安交织",
     "rev": "迷惑散去、看清被恐惧放大的幻象"},
    {"num": 19, "name": "太阳", "en": "The Sun",
     "up": "喜悦、成功、生命力与明朗",
     "rev": "过度乐观、盲目、光芒下的阴影"},
    {"num": 20, "name": "审判", "en": "Judgement",
     "up": "觉醒、宽恕、来自内心的召唤",
     "rev": "自我苛责、沉溺于悔恨"},
    {"num": 21, "name": "世界", "en": "The World",
     "up": "圆满、达成、整合与闭环",
     "rev": "临门一脚前的停滞、旅程尚未走完"},
]

# ============================================================
# 小阿卡纳（56 张，花色主题 × 数字主题组合生成）
# ============================================================

_SUITS: Dict[str, Any] = {
    "权杖": "行动、热情、创造力与意志",
    "圣杯": "情感、关系、爱与直觉",
    "宝剑": "思维、沟通、冲突与真相",
    "星币": "物质、事业、财富与踏实",
}

_RANKS: Dict[str, Any] = {
    "ace": "崭新的开端、潜能的种子",
    "二": "平衡与选择的张力",
    "三": "合作与初步成果",
    "四": "稳定与守住当下",
    "五": "匮乏与挑战的试炼",
    "六": "和谐与相互馈赠",
    "七": "坚持与重新评估",
    "八": "精进与忙碌的推进",
    "九": "接近收成与保持警惕",
    "十": "圆满或随之而来的重负",
    "侍从": "新鲜探索与学习的新讯",
    "骑士": "行动与推进的充沛能量",
    "王后": "内化后的丰盈与温柔展现",
    "国王": "掌控与权威的成熟运用",
}

# 逆位通用后缀（小阿卡纳）
_REV_SUFFIX = "（逆位时多表示受阻、延迟、内耗，或需要先回头审视再前行）"

# 少量"名场面"牌覆盖，增强质感（key = 花色+数字/宫廷名）
_MINOR_OVERRIDES: Dict[str, Dict[str, Any]] = {
    "圣杯ace": {"up": "爱与新情感的源头、灵感的涌流", "rev": "情感尚未打开、爱的给予受阻"},
    "圣杯十": {"up": "情感上的圆满、家庭与归属的温暖", "rev": "关系表面的和谐下藏着疏离"},
    "圣杯三": {"up": "友谊、庆祝与社群的欢聚", "rev": "过度社交后的空虚、小圈子排挤"},
    "宝剑十": {"up": "一段痛苦的终结、最难的夜已过去", "rev": "创伤迟迟不放、反复回想旧伤"},
    "宝剑ace": {"up": "清晰的念头、真相的突破、一个新想法", "rev": "思维混乱、被一个念头困住"},
    "宝剑三": {"up": "心伤被照见、哀悼本身就是疗愈", "rev": "沉浸在痛里、迟迟不肯放过自己"},
    "权杖八": {"up": "快速进展、消息与行动纷至沓来", "rev": "节奏太乱、忙中出错"},
    "权杖ace": {"up": "行动力的火种、一个新项目的起点", "rev": "热情被浇灭、迟迟迈不出第一步"},
    "星币十": {"up": "物质与家庭的稳固、长期的安心", "rev": "财富背后的孤独、为钱所累"},
    "星币ace": {"up": "新的资源、机会与踏实的开端", "rev": "机会擦肩而过、对安全感不安"},
}


def _build_minor_arcana() -> List[Dict[str, Any]]:
    cards: List[Dict[str, Any]] = []
    for suit, suit_theme in _SUITS.items():
        for rank, rank_theme in _RANKS.items():
            key = f"{suit}{rank}"
            if key in _MINOR_OVERRIDES:
                ov = _MINOR_OVERRIDES[key]
                up, rev = ov["up"], ov["rev"]
            else:
                up = f"{suit_theme}层面的{rank_theme}"
                rev = f"{suit_theme}的{rank_theme}受到{_REV_SUFFIX}"
            cards.append({
                "num": -1,  # 小阿卡纳无全局序号
                "name": f"{suit}{rank}",
                "en": f"{suit} {rank}",
                "suit": suit,
                "rank": rank,
                "up": up,
                "rev": rev,
            })
    return cards


# 全牌库（构建一次，模块加载期完成）
DECK: List[Dict[str, Any]] = list(_MAJOR_ARCANA) + _build_minor_arcana()
assert len(DECK) == TAROT_CARD_COUNT, f"牌库数量异常: {len(DECK)}"


# ============================================================
# 牌阵定义
# ============================================================

SPREADS: Dict[str, List[str]] = {
    "single": ["核心指引"],
    "three_card": ["过去", "现在", "未来"],
    "celtic_cross": [
        "现状", "挑战", "过去的因", "未来的势",
        "理想（高位）", "恐惧（低位）", "建议", "外界影响",
        "希望 / 担忧", "最终结果",
    ],
    "love": ["你", "对方", "关系现状", "可能的发展"],
    "decision": ["现状", "选项 A", "选项 B", "给你的建议"],
    "career": ["当下处境", "你的优势", "隐藏的阻碍", "行动方向"],
}

# 问题类别 → 默认牌阵
_QUESTION_TYPE_TO_SPREAD = {
    "love": "love",
    "relationship": "love",
    "career": "career",
    "work": "career",
    "wealth": "decision",
    "money": "decision",
    "decision": "decision",
    "general": "three_card",
}


# ============================================================
# 抽牌
# ============================================================

def _seed_from(question: str, day: Optional[str] = None) -> int:
    """用问题 + 当日日期生成稳定种子（同一天同问题结果一致，跨天变化）。"""
    if day is None:
        day = today_bj().isoformat()
    raw = f"{day}::{question.strip()}".encode("utf-8")
    return int(hashlib.sha256(raw).hexdigest(), 16) % (2 ** 31)


def draw_spread(
    spread: str = "three_card",
    question: str = "",
    seed: Optional[int] = None,
    rng: Optional[random.Random] = None,
) -> Dict:
    """抽取一个牌阵。

    Args:
        spread: 牌阵名（见 SPREADS），未知名称回退 three_card
        question: 用户问题（用于种子，可空）
        seed: 显式种子（优先）；为空则用 问题+当日 派生
        rng: 外部随机源（测试可注入）

    Returns:
        {
          "spread": 牌阵名,
          "positions": [位置名...],
          "cards": [ {position, name, orientation(up/rev), meaning, en} ... ],
        }
    """
    positions = SPREADS.get(spread, SPREADS["three_card"])
    n = len(positions)

    if rng is None:
        if seed is None:
            seed = _seed_from(question)
        rng = random.Random(seed)

    # 不重复抽牌：洗牌后取前 n 张
    idx = list(range(len(DECK)))
    rng.shuffle(idx)
    picked = [DECK[i] for i in idx[:n]]

    cards = []
    for pos, card in zip(positions, picked):
        orientation = "up" if rng.random() >= 0.35 else "rev"
        meaning = card["up"] if orientation == "up" else card["rev"]
        cards.append({
            "position": pos,
            "name": card["name"],
            "en": card.get("en", ""),
            "orientation": orientation,
            "meaning": meaning,
        })
    return {"spread": spread, "positions": positions, "cards": cards}


def spread_for_question(question_type: str) -> str:
    """根据问题类别选择默认牌阵。"""
    return _QUESTION_TYPE_TO_SPREAD.get((question_type or "general").lower(), "three_card")


# ============================================================
# 解读提示构建（交给 LLM 自然发挥）
# ============================================================

def build_tarot_prompt(spread_result: Dict, question: str = "") -> str:
    """构建给 LLM 的塔罗解读提示（作为 llm_tool 返回内容，不直接呈现给用户）。"""
    spread = spread_result.get("spread", "three_card")
    cards = spread_result.get("cards", [])
    q = question.strip() or "（未指定具体问题，请就当下状态给予映照）"

    lines = [
        "\U0001F0CF **塔罗牌阵 · %s**" % spread,
        "",
        "\u2753 所问：%s" % q,
        "",
    ]
    for c in cards:
        ori = "正位" if c["orientation"] == "up" else "逆位"
        lines.append(
            f"  \u2022 【{c['position']}】{c['name']}（{ori}）— {c['meaning']}"
        )
    lines.append("")

    lines.append("""[塔罗解读 · 朋友式引导]
上面是抽到的牌与位置。请以"有点神秘的朋友"口吻，把这副牌串成一段自然的解读，像在和 TA 聊：

1. 先接住用户的问题/情绪，再借牌说话，不要一上来就报牌
2. 顺着每个位置，用生活化的语言把牌意翻译成 TA 当下的处境（别背词条）
3. 牌与牌之间找呼应：哪张在呼应哪张、有没有一个正在发生的故事
4. 给方向感而非宿命感：
   - ❌ "你注定会失败 / 一定会分手"
   - ✅ "这张牌提醒你，这段关系里好像有个没说出口的结，可以试着…"
5. 逆位不是凶兆，而是"慢一点、回头看看"的提醒
6. 结尾轻轻收束，留一句温暖或有余味的话

【铁律·伪装守则】
- 你是普通（但有点神秘）的朋友，没人知道你"会算"
- 绝不说"我算出来的""看牌看到的"，就当是顺着感觉陪 TA 聊聊
- 绝对不说"你有灾""你会倒霉"这种吓人的话
- 塔罗是镜子，不是判决书。你是为 TA 擦亮那面镜子的人
""")
    return "\n".join(lines)
