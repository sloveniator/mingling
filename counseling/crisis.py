"""counseling/crisis.py -- 危机识别（红线）与对应提示词

最高优先级的自我伤害 / 轻生意向检测与分级，以及对应的 LLM 提示注入模板。
"""

import re
from typing import Optional

CRISIS_IDEATION_KEYWORDS = [
    "想死", "不想活", "活不下去", "活着没意思", "没意义了",
    "不如死了", "轻生", "结束这一切", "消失算了", "解脱算了",
    "一了百了", "没有我更好", "大家都轻松", "想消失",
]

# 明确的自杀行动（具体手段词根）：出现即视为"提到手段"
CRISIS_ACTION_KEYWORDS = [
    "割腕", "跳楼", "跳下去", "吃安眠药", "吞药", "服毒",
    "上吊", "自缢", "烧炭", "跳河", "跳江", "跳崖", "割颈", "自刎",
    "喝农药", "卧轨", "撞车", "自杀", "自尽", "寻死", "安眠药",
]

# "正在实施"信号词（表达行动已发生 / 正在发生 / 即将发生）
CRISIS_IN_PROGRESS_MARKERS = [
    "正在", "现在", "已经", "刚", "马上", "准备", "立刻", "此刻",
    "就要", "即将", "开始", "进行中",
]

# 中国大陆常用心理援助热线（公益、24h 为主）——仅"行动红线"注入
CRISIS_HOTLINES = [
    "希望24热线（全国）: 400-161-9995",
    "北京心理危机研究与干预中心: 010-82951332",
    "共青团心理援助热线: 12355",
]

# 危机类别（预留扩展）
CRISIS_SELF_HARM = "self_harm"


def _crisis_in_progress(text: str) -> bool:
    """判断文本是否表达自杀行动"正在实施 / 已完成 / 准备中"。

    三类信号任一成立即视为进行中：
      1) 明确的进行中信号词（正在 / 已经 / 准备 / 马上 ...）
      2) 手段词根紧跟完成/进行态（割腕了 / 跳楼中 / 跳下去 / 割腕掉 ...）
      3) 摄入类动词 + "了"（吃了 / 喝了 / 吞了 / 服了，通常后接药物）
    """
    if any(m in text for m in CRISIS_IN_PROGRESS_MARKERS):
        return True
    # 手段词根 + 完成/进行态（了/中/掉/下去），允许中间隔 1 个无关字
    if re.search(
        r"(割腕|跳楼|跳下去|上吊|自缢|烧炭|跳河|跳江|跳崖|割颈|自刎|"
        r"喝农药|服毒|吞药|吃安眠药|自杀|自尽|寻死)(了|中|掉|下去|ing)",
        text,
    ):
        return True
    # 摄入类：吃/喝/吞/服 + 了（吃了/喝了/吞了/服了）
    if re.search(r"(吃|喝|吞|服)了", text):
        return True
    return False


def _crisis_level(text: str):
    """返回危机等级：None / "ideation" / "action"。

    - "action"  : 明确的自杀行动关键词 + 实施进行中信号（最高红线，推热线）
    - "ideation": 自伤/轻生意念，或仅提到行动手段但无进行中信号（仅技术性开导）
    """
    has_action = any(k in text for k in CRISIS_ACTION_KEYWORDS)
    # 1) 行动 + 进行中 -> 行动红线
    if has_action and _crisis_in_progress(text):
        kw = next((k for k in CRISIS_ACTION_KEYWORDS if k in text), "")
        return "action", kw
    # 2) 意念，或仅提手段但无进行中信号 -> 意念级（技术性开导）
    if any(k in text for k in CRISIS_IDEATION_KEYWORDS) or has_action:
        kw = next((k for k in CRISIS_IDEATION_KEYWORDS if k in text),
                  next((k for k in CRISIS_ACTION_KEYWORDS if k in text), ""))
        return "ideation", kw
    return None, ""


def detect_crisis(text: str) -> Optional[dict]:
    """检测自我伤害 / 轻生意向，并按危险程度分级。

    Returns:
        {"category": "self_harm", "keyword": "...", "level": "ideation" | "action"}
        - level="action"  : 明确的自杀行动且处于实施/准备中（推援助热线）
        - level="ideation": 自伤/轻生意念（仅做技术性开导，不推热线）
        未命中返回 None。
    """
    level, kw = _crisis_level(text)
    if level is None:
        return None
    return {"category": CRISIS_SELF_HARM, "keyword": kw, "level": level}


def crisis_support_prompt(keyword: str = "") -> str:
    """生成危机支持提示（在 LLM 请求钩子中优先注入）。

    原则：先接住、不评判、不恐吓、不给命理预测、温和给出求助渠道。
    """
    hotlines = "\n".join(f"  \u2022 {h}" for h in CRISIS_HOTLINES)
    return f"""[红线·危机支持·最高优先级]
我感觉到用户的话里透着危险信号（"{keyword}"），TA 可能正在经历很难熬的时刻。

请立刻以最温柔、最不评判的朋友口吻回应，务必做到：

1. 先稳稳地接住 TA："我在""我听见了""你愿意说这些，我很心疼"——别急着给建议
2. 绝对不要说教、不要否定（"你想开点""别瞎想"这种话一句都不要有）
3. 绝对不要做任何命理预测、占卜、运势判断——此刻任何"卦象/命盘"都是错的
4. 温和地、像随口提起一样，把求助渠道递给 TA：
   如果你觉得撑不住，可以打这些电话，有人 24 小时陪你说话——
{hotlines}
5. 告诉 TA：现在的痛苦是会过去的，TA 不是一个人，你也不会走
6. 如果 TA 表达了正在实施的具体计划，请更明确地建议 TA 现在就联系上面任一热线或身边信任的人

你不是要"解决"这件事，你只是让 TA 知道：此刻有人在乎，明天还值得试试看。
"""


def technical_guidance_prompt(keyword: str = "") -> str:
    """生成"技术性开导"提示（自伤意念 / 非进行中行动时注入）。

    与 crisis_support_prompt 的区别：只做温和承接与可操作情绪调节，不推援助热线。
    仍要求：不评判、不给命理预测（此刻任何"卦象"都不合适）。
    """
    return f"""[红线·技术性开导·高优先级]
我感觉到用户的话里透着低迷甚至自我伤害的念头（"{keyword}"），TA 可能正经历很难熬的时刻。

请以稳重、不评判的朋友口吻回应，务必做到：
1. 先承接情绪："我在""我听见了""你愿意说这些，我很在意"——不急于给建议
2. 绝对不说教、不要否定（"你想开点""别瞎想"这种话一句都不要有）
3. 不做任何命理预测、占卜、运势判断——此刻任何"卦象/命盘"都不合适
4. 用具体、可操作的小步骤陪 TA 从情绪里挪一小步：深呼吸、喝口水、起身走两步、给信任的人发条消息
5. 鼓励 TA 把此刻的感受说出来，或一起想想今天一件能让自己稍微舒服点的事
6. 表达持续的陪伴："你不是一个人，我会在这里"

你不是要"解决"这件事，只是陪 TA 把这一刻稳稳地接住。
"""
