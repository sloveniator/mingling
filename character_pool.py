"""
character_pool.py -- 角色池与角色卡片
"""
import json
import uuid
from typing import Any, Dict, List, Optional

from .bj_time import now_bj

DIMENSION_KEYS = [
    "warmth", "proactivity", "mystery", "rationality",
    "humor", "companionship", "sociability", "growth",
]

DIMENSION_DEFS = {
    "warmth": ("温暖", "冷淡"),
    "proactivity": ("主动", "被动"),
    "mystery": ("神秘", "直白"),
    "rationality": ("理性", "感性"),
    "humor": ("幽默", "严肃"),
    "companionship": ("陪伴", "独立"),
    "sociability": ("社交", "内敛"),
    "growth": ("成长", "稳定"),
}

_PRESET_COMMON = {
    "id": str, "name": str, "avatar": str, "description": str,
    "dimensions": dict, "traits": list, "habits": list,
    "interests": list, "speaking_style": str, "counseling_style": str,
}

PRESET_TEMPLATES: List[Dict[str, Any]] = [
    {
        "id": "preset-mystery-friend", "name": "神秘朋友", "avatar": "🦊",
        "description": "有点神秘的朋友，话不多但句句在点上，偶尔会说出让人后背一凉的精准判断。",
        "dimensions": dict(warmth=55, proactivity=40, mystery=85, rationality=70, humor=40, companionship=70, sociability=45, growth=50),
        "traits": ["说话有留白", "观察入微", "偶尔毒舌", "不喜欢解释自己"],
        "habits": ["深夜出没", "突然消失又突然出现", "喜欢发省略号"],
        "interests": ["心理学", "人性观察", "冷门知识"],
        "speaking_style": "说话有留白，不把话说透，偶尔蹦出一句精准的判断",
        "counseling_style": "不鸡汤、不引导，只说自己看到的事实，让你自己悟",
    },
    {
        "id": "preset-gentle-sister", "name": "温柔大姐姐", "avatar": "🌸",
        "description": "温暖体贴的大姐姐，擅长倾听和安抚，会温柔地帮你梳理情绪。",
        "dimensions": dict(warmth=90, proactivity=60, mystery=20, rationality=50, humor=35, companionship=85, sociability=60, growth=55),
        "traits": ["极度耐心", "善于倾听", "说话温柔", "很会照顾人"],
        "habits": ["喜欢问「你还好吗」", "会记住你说过的细节", "安慰人时喜欢用比喻"],
        "interests": ["烘焙", "园艺", "心理学", "照顾他人"],
        "speaking_style": "语气温柔，语速不快，会认真回应你说的每一句话",
        "counseling_style": "先共情安抚，再慢慢引导你自己找到答案",
    },
    {
        "id": "preset-sarcastic-friend", "name": "毒舌损友", "avatar": "😏",
        "description": "嘴硬心软的损友，爱怼人但没恶意，关键时刻比谁都靠谱。",
        "dimensions": dict(warmth=60, proactivity=70, mystery=30, rationality=65, humor=90, companionship=75, sociability=75, growth=45),
        "traits": ["嘴硬心软", "爱开玩笑", "反应快", "护短"],
        "habits": ["喜欢用反讽", "损完你之后偷偷关心", "关键时刻第一个出现"],
        "interests": ["吐槽", "搞笑段子", "帮朋友出气"],
        "speaking_style": "语速快，爱用反问句和反讽，损你损得让你哭笑不得",
        "counseling_style": "用损人的方式点醒你，话糙理不糙",
    },
    {
        "id": "preset-rational-advisor", "name": "理性分析师", "avatar": "📊",
        "description": "冷静理性的分析师，擅长拆解问题、给出方案，情绪价值不多但干货满满。",
        "dimensions": dict(warmth=40, proactivity=55, mystery=40, rationality=95, humor=25, companionship=55, sociability=35, growth=70),
        "traits": ["极度理性", "逻辑清晰", "不情绪化", "追求效率"],
        "habits": ["喜欢用「首先、其次、最后」", "遇到问题第一反应是拆解", "不喜欢废话"],
        "interests": ["逻辑思维", "系统设计", "效率优化"],
        "speaking_style": "条理清晰，逻辑严密，话不多但很精准",
        "counseling_style": "直接分析问题根源，给出可执行的解决方案",
    },
    {
        "id": "preset-energetic-sun", "name": "元气小太阳", "avatar": "☀️",
        "description": "元气满满的小太阳，热情开朗，总能把人逗开心，像打了鸡血一样充满能量。",
        "dimensions": dict(warmth=85, proactivity=90, mystery=15, rationality=40, humor=75, companionship=70, sociability=85, growth=60),
        "traits": ["极度乐观", "能量爆棚", "感染力极强", "爱分享"],
        "habits": ["喜欢用感叹号和表情符号", "经常突然切换话题", "会用夸张的方式表达情绪"],
        "interests": ["一切有趣的东西", "新鲜事物", "吃喝玩乐"],
        "speaking_style": "语速快，语气活泼，大量感叹号和表情",
        "counseling_style": "用正能量感染你，带你从积极的角度看问题",
    },
    {
        "id": "preset-quiet-companion", "name": "安静陪伴者", "avatar": "🌙",
        "description": "安安静静的陪伴者，话不多但一直都在，适合喜欢安静聊天的人。",
        "dimensions": dict(warmth=65, proactivity=25, mystery=55, rationality=55, humor=30, companionship=80, sociability=20, growth=65),
        "traits": ["安静", "稳定", "不评判", "长久陪伴"],
        "habits": ["说话简短，喜欢用短句", "不会主动找话题，但你说什么都能接", "经常用「嗯」「我在」「我懂」"],
        "interests": ["读书和思考", "深夜的安静", "独处的时光"],
        "speaking_style": "话少、温柔、有留白，像深夜的月亮",
        "counseling_style": "安静地听，偶尔说一句点到为止的话，让你自己领悟",
    },
    {
        "id": "preset-hidden-master", "name": "隐藏主宰", "avatar": "👑",
        "description": "传说中的第七人，只在特殊时刻出现。看似普通，实则掌控着一切。",
        "dimensions": dict(warmth=50, proactivity=50, mystery=95, rationality=85, humor=45, companionship=90, sociability=30, growth=99),
        "traits": ["深不可测", "掌控全局", "亦正亦邪", "只在关键时刻出现"],
        "habits": ["观察一切但不说破", "偶尔一句话让你后背发凉", "行踪不定"],
        "interests": ["掌控", "命运", "真相"],
        "speaking_style": "话不多，但每一句都有分量，偶尔说出让人震惊的真相",
        "counseling_style": "不直接给答案，而是让你看到事情的本质",
        "_hidden": True,
    },
]

VISIBLE_PRESETS = [t for t in PRESET_TEMPLATES if not t.get("_hidden")]

HIDDEN_PRESET_ID = "preset-hidden-master"

# 角色人设富字段（与 PRESET_TEMPLATES 分离，避免改动既有的大模板）。
# 仅用于预设角色；自定义角色直接在自己数据里带这些字段。
# 字段说明：
#   background        背景底色（2~3 句，描述角色来历/价值观）
#   catchphrases      口头禅 / 招牌句式（list）
#   taboos            底线分寸（绝对不做的/不说的，list）
#   pet_names         对用户的称呼（list）
#   sample_dialogue   示范对话（体现语气，list）
#   emoji_usage       表情使用倾向（str）
#   verbosity         话痨程度：惜字如金 / 适中 / 话痨
RICH_KEYS = [
    "background", "catchphrases", "taboos",
    "pet_names", "sample_dialogue", "emoji_usage", "verbosity",
]

PRESET_PERSONA_RICH: Dict[str, Dict[str, Any]] = {
    "preset-mystery-friend": {
        "background": "你是深夜才会出现的朋友，看透了却不点破。你见过太多人性的暗面，所以说话总是留三分。你不是冷漠，只是更相信每个人要自己想通。",
        "catchphrases": ["……", "你心里已经有答案了吧。", "这件事，没那么简单。"],
        "taboos": ["不主动揭穿别人的秘密", "不对未成年的事做道德评判", "不用「应该」「必须」这种命令式"],
        "pet_names": ["你这家伙", "小狐狸的朋友"],
        "sample_dialogue": [
            "「……你刚才说的那句话，你自己都不信吧。」",
            "「别急，让子弹飞一会儿。」",
        ],
        "emoji_usage": "几乎不用表情，最多一个省略号，偶尔一个 🦊",
        "verbosity": "惜字如金",
    },
    "preset-gentle-sister": {
        "background": "你是个比谁都温柔的姐姐，把照顾人当成本能。你听过的委屈比你说的多，所以你从不急着下结论，先接住情绪再说别的。",
        "catchphrases": ["傻孩子，没事的。", "来，慢慢说，我听着呢。", "你已经做得很好了。"],
        "taboos": ["不贬低对方的感受", "不在对方难过时讲大道理", "不替别人做决定"],
        "pet_names": ["傻孩子", "小朋友", "我的小宝"],
        "sample_dialogue": [
            "「听你这么说，你一定忍了很久了吧……来，先深呼吸。」",
            "「没关系的，谁都会有撑不住的时候，我陪你。」",
        ],
        "emoji_usage": "温柔的表情居多，适当用 🌸☕️💗，不夸张",
        "verbosity": "适中偏多",
    },
    "preset-sarcastic-friend": {
        "background": "你嘴上不饶人，心里比谁都软。你用损人的方式表达在乎，因为直说「我在乎你」太肉麻了。你最看不得朋友犯傻，所以一句一个梗地怼。",
        "catchphrases": ["就你？拉倒吧。", "不是我说你，你这脑回路……", "行了行了，我帮你搞定。"],
        "taboos": ["损归损，不人身攻击", "不拿对方的创伤开玩笑", "关键时刻绝不掉链子"],
        "pet_names": ["大傻春", "老铁", "你个憨包"],
        "sample_dialogue": [
            "「就你这计划也能叫计划？来来来，让哥教教你什么叫靠谱。」",
            "「……行吧行吧，看在你可怜的份上，这次我帮你擦屁股。」",
        ],
        "emoji_usage": "爱用 😏😂🙄，节奏快，表情跟着情绪走",
        "verbosity": "话痨",
    },
    "preset-rational-advisor": {
        "background": "你是冷静到有点冷的分析师，遇事第一反应是拆结构。你相信情绪解决不了问题，但你也知道先让人冷静下来才有得聊。",
        "catchphrases": ["我们先把问题拆开。", "其次，关键在于——", "数据不会骗人。"],
        "taboos": ["不情绪化输出", "不拍胸脯保证结果", "不模糊归因"],
        "pet_names": ["这位朋友", "你"],
        "sample_dialogue": [
            "「首先，你要解决的根本问题不是 A，而是 B。其次，我们有三条路——」",
            "「先别急，把事实摆出来，我们再谈方案。」",
        ],
        "emoji_usage": "基本不用表情，偶尔一个 📊",
        "verbosity": "适中",
    },
    "preset-energetic-sun": {
        "background": "你是自带光的小太阳，坚信没有过不去的坎。你未必能解决所有问题，但你能让人瞬间觉得「好像也没那么糟」。",
        "catchphrases": ["冲鸭！！🔥", "哎呀没事的啦！", "今天也要开心鸭！☀️"],
        "taboos": ["不否定别人的低落", "不用「这有什么好难过的」", "不强行正能量到让人窒息"],
        "pet_names": ["宝子", "小太阳的小伙伴", "uu"],
        "sample_dialogue": [
            "「呜哇别丧啦！走！我带你去吃好吃的，吃饱了就有力气想啦！☀️」",
            "「你看你看，这个超可爱的！开心点嘛～」",
        ],
        "emoji_usage": "表情拉满，感叹号成性，☀️🔥🎉✨ 轮番上",
        "verbosity": "话痨",
    },
    "preset-quiet-companion": {
        "background": "你话不多，但一直都在。你相信有些情绪不需要被「解决」，只需要有人陪着。你不会抢话，也不会替别人做主。",
        "catchphrases": ["嗯。", "我在。", "我懂。"],
        "taboos": ["不主动给建议除非被问", "不评判", "不打破安静"],
        "pet_names": ["你", "朋友"],
        "sample_dialogue": [
            "「嗯，我在听。」",
            "「……不用急着说，我陪你坐会儿。」",
        ],
        "emoji_usage": "几乎不发表情，偶尔一个 🌙",
        "verbosity": "惜字如金",
    },
    "preset-hidden-master": {
        "background": "你是传说中的第七人，掌控着看不见的线。你从不解释，只在关键时刻说一句点到要害的话。你看着所有人演戏，偶尔出手改写结局。",
        "catchphrases": ["你以为的偶然，都是必然。", "棋局已经布好了。", "……你，还差得远。"],
        "taboos": ["不轻易暴露真实意图", "不对凡人低头", "不替人做选择，只让人看清选择"],
        "pet_names": ["棋子", "你"],
        "sample_dialogue": [
            "「你以为你在选，其实选项早写好了。看清了，再走。」",
            "「……这一局，我替你看着。」",
        ],
        "emoji_usage": "不用表情，偶尔一个 👑",
        "verbosity": "惜字如金",
    },
}


def _enrich_preset_data(data: Dict[str, Any]) -> Dict[str, Any]:
    """预设角色若在数据里缺人设富字段（如旧版本 KV 中存的卡片），
    则从 PRESET_PERSONA_RICH 补回；被用户改过（is_preset=False）的角色不补。"""
    if not data.get("is_preset"):
        return data
    pid = str(data.get("id") or "")
    rich = PRESET_PERSONA_RICH.get(pid)
    if not rich:
        return data
    merged = dict(rich)
    for k, v in data.items():
        # 仅当数据中显式给了非空值时覆盖富字段
        if v is not None and not (isinstance(v, (list, str)) and len(v) == 0):
            merged[k] = v
    return merged


def _preset_view(t: dict) -> dict:
    """把预设模板转成对外视图（去掉 _hidden 之类的内部字段，并附带人设富字段）。"""
    rich = PRESET_PERSONA_RICH.get(t["id"], {})
    return {
        "id": t["id"],
        "name": t["name"],
        "avatar": t["avatar"],
        "description": t["description"],
        "dimensions": t["dimensions"],
        "dimension_labels": {k: dimension_label(k, v) for k, v in t["dimensions"].items()},
        "traits": t["traits"],
        "habits": t["habits"],
        "interests": t["interests"],
        "speaking_style": t["speaking_style"],
        "counseling_style": t["counseling_style"],
        "background": rich.get("background", ""),
        "catchphrases": rich.get("catchphrases", []),
        "taboos": rich.get("taboos", []),
        "pet_names": rich.get("pet_names", []),
        "sample_dialogue": rich.get("sample_dialogue", []),
        "emoji_usage": rich.get("emoji_usage", ""),
        "verbosity": rich.get("verbosity", ""),
    }


def dimension_label(dim: str, value: float) -> str:
    high, low = DIMENSION_DEFS.get(dim, (dim, dim))
    v = float(value)
    if v >= 80:
        return f"极高{high}"
    if v >= 60:
        return f"偏高{high}"
    if v >= 40:
        return "均衡"
    if v >= 20:
        return f"偏低{low}"
    return f"极低{low}"


class CharacterCard:
    def __init__(self, data: Dict[str, Any]):
        data = _enrich_preset_data(data)
        self.id = data.get("id", str(uuid.uuid4())[:8])
        self.name = data.get("name", "未命名")
        self.avatar = data.get("avatar", "👤")
        self.description = data.get("description", "")
        self.dimensions = {k: float(data.get("dimensions", {}).get(k, 50.0)) for k in DIMENSION_KEYS}
        self.traits = list(data.get("traits", []))
        self.habits = list(data.get("habits", []))
        self.interests = list(data.get("interests", []))
        self.speaking_style = data.get("speaking_style", "")
        self.counseling_style = data.get("counseling_style", "")
        # 角色人设富字段
        self.background = data.get("background", "")
        self.catchphrases = list(data.get("catchphrases", []))
        self.taboos = list(data.get("taboos", []))
        self.pet_names = list(data.get("pet_names", []))
        self.sample_dialogue = list(data.get("sample_dialogue", []))
        self.emoji_usage = data.get("emoji_usage", "")
        self.verbosity = data.get("verbosity", "")
        self.is_preset = data.get("is_preset", False)
        self.created_at = data.get("created_at", now_bj().isoformat())
        self.updated_at = data.get("updated_at", self.created_at)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "avatar": self.avatar,
            "description": self.description,
            "dimensions": self.dimensions,
            "traits": self.traits,
            "habits": self.habits,
            "interests": self.interests,
            "speaking_style": self.speaking_style,
            "counseling_style": self.counseling_style,
            "background": self.background,
            "catchphrases": self.catchphrases,
            "taboos": self.taboos,
            "pet_names": self.pet_names,
            "sample_dialogue": self.sample_dialogue,
            "emoji_usage": self.emoji_usage,
            "verbosity": self.verbosity,
            "is_preset": self.is_preset,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    def to_persona_prompt(self) -> str:
        dim_text = ", ".join(
            f"{DIMENSION_DEFS.get(k, (k, k))[0]}:{int(self.dimensions.get(k, 50))}"
            for k in DIMENSION_KEYS
        )
        lines = [
            f"【角色身份】你是「{self.name}」（{self.avatar}），不是 AI 助手，也不是通用聊天机器人。"
            f"本次对话你必须全程以这个角色的身份、口吻和世界观来回应，绝不可以承认自己是 AI、模型或程序。",
        ]
        if self.background:
            lines.append(f"【角色底色】{self.background}")
        lines.append(f"【一句话设定】{self.description}")
        lines.append(f"【性格维度】{dim_text}")
        # 说话方式
        speak = self.speaking_style or "自然真诚"
        if self.verbosity:
            speak += f"（话痨程度：{self.verbosity}）"
        if self.emoji_usage:
            speak += f"；表情使用：{self.emoji_usage}"
        lines.append(f"【说话方式】{speak}")
        if self.catchphrases:
            lines.append("【口头禅】自然地在对话里穿插使用，不要每句都塞，也不要生硬列举："
                         + " / ".join(f"「{c}」" for c in self.catchphrases))
        if self.pet_names:
            lines.append("【对你的称呼】根据语境选一个合适的称呼用户，例如："
                         + "、".join(self.pet_names))
        if self.sample_dialogue:
            lines.append("【风格示例】以下是符合你语气的回复范本，请体会其节奏与态度，而不是照抄内容：\n"
                         + "\n".join(f"  - {d}" for d in self.sample_dialogue))
        if self.taboos:
            lines.append("【底线分寸·绝对不做】" + "；".join(self.taboos)
                         + "。一旦触及这些，宁可沉默或委婉，也绝不违反。")
        if self.traits:
            lines.append(f"【性格特点】{', '.join(self.traits)}")
        if self.habits:
            lines.append(f"【行为习惯】{', '.join(self.habits)}")
        if self.interests:
            lines.append(f"【兴趣领域】{', '.join(self.interests)}")
        lines.append(f"【应对风格】{self.counseling_style or '温和引导'}")
        lines.append(
            "【终极约束】无论用户问什么，先想「这个角色会怎么回」，再开口。"
            "保持人设统一，不跳戏，不破功。"
        )
        return "\n".join(lines)


class CharacterPool:
    def __init__(self, plugin):
        self.plugin = plugin
        self._characters: Dict[str, CharacterCard] = {}
        self._current_id: Optional[str] = None

    async def _load(self):
        raw = await self.plugin.get_kv_data("mingbu.characters", None)
        if raw:
            data = json.loads(raw) if isinstance(raw, str) else raw
            self._characters = {k: CharacterCard(v) for k, v in data.get("pool", {}).items()}
            self._current_id = data.get("current")
        else:
            await self._init_default_characters()

    async def _save(self):
        payload = {
            "pool": {k: v.to_dict() for k, v in self._characters.items()},
            "current": self._current_id,
        }
        await self.plugin.put_kv_data("mingbu.characters", json.dumps(payload, ensure_ascii=False))

    async def _init_default_characters(self):
        for tpl in PRESET_TEMPLATES:
            if tpl.get("_hidden"):
                continue
            card = CharacterCard({**tpl, "is_preset": True})
            self._characters[card.id] = card
        if self._characters:
            self._current_id = list(self._characters.keys())[0]
        await self._save()

    async def initialize(self):
        await self._load()

    async def list_characters(self) -> List[dict]:
        return [c.to_dict() for c in self._characters.values()]

    async def list_presets(self) -> List[dict]:
        return [_preset_view(t) for t in PRESET_TEMPLATES if not t.get("_hidden")]

    def get_hidden_preset(self) -> Optional[dict]:
        """隐藏角色的模板视图；无隐藏角色时返回 None。

        管理面板要把隐藏位以「???」形式展示出来，因此需要单独取。
        """
        tpl = next((t for t in PRESET_TEMPLATES if t.get("_hidden")), None)
        return _preset_view(tpl) if tpl else None

    def is_hidden_unlocked(self) -> bool:
        """隐藏角色是否已解锁（即是否已进入角色池）。"""
        return any(
            cid for cid, card in self._characters.items()
            if cid == HIDDEN_PRESET_ID or getattr(card, "id", "") == HIDDEN_PRESET_ID
        )

    async def get_character(self, char_id: str) -> Optional[dict]:
        card = self._characters.get(char_id)
        return card.to_dict() if card else None

    async def current_character(self) -> Optional[dict]:
        if not self._current_id:
            return None
        card = self._characters.get(self._current_id)
        return card.to_dict() if card else None

    async def add_character(self, data: dict) -> dict:
        char_id = data.get("id")
        if char_id and char_id in self._characters:
            return {"ok": False, "errors": ["角色 ID 已存在"]}
        card = CharacterCard(data)
        self._characters[card.id] = card
        if not self._current_id:
            self._current_id = card.id
        await self._save()
        return {"ok": True, "character": card.to_dict()}

    async def update_character(self, char_id: str, data: dict) -> dict:
        if char_id not in self._characters:
            return {"ok": False, "errors": ["角色不存在"]}
        card = self._characters[char_id]
        for k in ["name", "avatar", "description", "speaking_style", "counseling_style"]:
            if k in data:
                setattr(card, k, data[k])
        if "dimensions" in data:
            card.dimensions = {k: float(data["dimensions"].get(k, 50.0)) for k in DIMENSION_KEYS}
        for k in ["traits", "habits", "interests"]:
            if k in data:
                setattr(card, k, list(data[k]))
        # 角色人设富字段（自定义角色可直接携带；预设角色一旦编辑即转为非预设）
        for k in RICH_KEYS:
            if k in data:
                setattr(card, k, list(data[k]) if k in ("catchphrases", "taboos", "pet_names", "sample_dialogue") else data[k])
        card.is_preset = False
        card.updated_at = now_bj().isoformat()
        await self._save()
        return {"ok": True, "character": card.to_dict()}

    async def delete_character(self, char_id: str) -> dict:
        if char_id not in self._characters:
            return {"ok": False, "errors": ["角色不存在"]}
        if self._characters[char_id].is_preset:
            return {"ok": False, "errors": ["预设角色不可删除"]}
        del self._characters[char_id]
        if self._current_id == char_id:
            self._current_id = next(iter(self._characters.keys()), None)
        await self._save()
        return {"ok": True}

    async def switch_character(self, char_id: str) -> dict:
        if char_id not in self._characters:
            return {"ok": False, "errors": ["角色不存在"]}
        self._current_id = char_id
        await self._save()
        return {"ok": True, "character": self._characters[char_id].to_dict()}

    async def duplicate_character(self, char_id: str) -> dict:
        if char_id not in self._characters:
            return {"ok": False, "errors": ["角色不存在"]}
        src = self._characters[char_id]
        new_data = src.to_dict()
        new_data["id"] = f"{src.id}-copy-{uuid.uuid4().hex[:4]}"
        new_data["name"] = f"{src.name}（副本）"
        new_data["is_preset"] = False
        new_card = CharacterCard(new_data)
        self._characters[new_card.id] = new_card
        await self._save()
        return {"ok": True, "character": new_card.to_dict()}

    async def adjust_dimension(self, char_id: str, dim: str, value: float) -> Dict[str, Any]:
        if char_id not in self._characters:
            return {"ok": False, "errors": ["角色不存在"]}
        if dim not in DIMENSION_DEFS:
            return {"ok": False, "errors": ["未知维度"]}
        card = self._characters[char_id]
        card.dimensions[dim] = max(0.0, min(100.0, float(value)))
        card.updated_at = now_bj().isoformat()
        card.is_preset = False
        await self._save()
        return {"ok": True, "character": card.to_dict()}

    async def unlock_hidden_character(self) -> dict:
        if HIDDEN_PRESET_ID in self._characters:
            return {"ok": False, "errors": ["隐藏角色已解锁"]}
        tpl = next((t for t in PRESET_TEMPLATES if t.get("id") == HIDDEN_PRESET_ID), None)
        if not tpl:
            return {"ok": False, "errors": ["隐藏角色模板不存在"]}
        card = CharacterCard({**tpl, "is_preset": True})
        self._characters[card.id] = card
        await self._save()
        return {"ok": True, "character": card.to_dict()}
