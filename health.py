"""
health.py -- 健康生活指导引擎（纯逻辑模块，无网络依赖）
=========================================================
提供日常作息、饮食、运动等方面的个性化建议生成。

设计要点（与项目设定一致）：
  - 不是医疗诊断：只给"生活方式建议"，并明确提醒"如有不适请就医"
  - 个性化：基于用户档案（作息/运动习惯/目标）生成，而非套话
  - 纯逻辑 + 提示构建：具体建议文本由本模块结构化生成，
    自然化表达交给 LLM 在主回复中完成
  - 无 astrbot / aiohttp 依赖，便于单测

档案字段（均为可选，缺省时给出通用建议）：
  age           年龄
  gender        性别 male/female/其他
  sleep_time    通常入睡时间（"23:30"）
  wake_time     通常起床时间（"07:00"）
  sleep_quality 自评睡眠质量 1-5
  exercise_freq 每周运动次数（0-7）
  diet_habits   饮食偏好/问题（自由文本，如"常吃外卖""偏甜"）
  goals         健康目标（list，如 ["减脂","改善睡眠"]）
  conditions    需留意的身体状况（list，如 ["久坐腰酸"]）
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

# 非医疗免责声明（所有健康建议都附带）
HEALTH_DISCLAIMER = (
    "以上仅为生活方式层面的通用建议，不能替代专业医疗诊断。"
    "如有持续不适、明确疾病或正在服药，请咨询医生或营养师。"
)


def _parse_hhmm(s: str) -> Optional[tuple]:
    """解析 '23:30' → (23, 30)；失败返回 None。"""
    if not s or ":" not in s:
        return None
    try:
        h, m = s.split(":")
        return (int(h), int(m))
    except (ValueError, TypeError):
        return None


def _sleep_duration_hours(sleep: str, wake: str) -> Optional[float]:
    """根据入睡/起床时间估算睡眠时长（小时）。"""
    a = _parse_hhmm(sleep)
    b = _parse_hhmm(wake)
    if not a or not b:
        return None
    sa = a[0] * 60 + a[1]
    wb = b[0] * 60 + b[1]
    if wb <= sa:
        wb += 24 * 60  # 跨午夜
    return round((wb - sa) / 60.0, 1)


@dataclass
class HealthProfile:
    """用户健康档案（全部可选）。"""
    age: Optional[int] = None
    gender: str = ""
    sleep_time: str = ""
    wake_time: str = ""
    sleep_quality: int = 0          # 1-5，0=未评
    exercise_freq: int = -1         # 0-7，-1=未知
    diet_habits: str = ""
    goals: List[str] = field(default_factory=list)
    conditions: List[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: dict) -> "HealthProfile":
        if not d:
            return cls()
        g = d.get("gender", "")
        if g and g not in ("male", "female"):
            g = "male" if g == "男" else ("female" if g == "女" else "")
        return cls(
            age=d.get("age"),
            gender=g,
            sleep_time=d.get("sleep_time", ""),
            wake_time=d.get("wake_time", ""),
            sleep_quality=int(d.get("sleep_quality", 0) or 0),
            exercise_freq=int(d.get("exercise_freq", -1) if d.get("exercise_freq") is not None else -1),
            diet_habits=d.get("diet_habits", ""),
            goals=list(d.get("goals", []) or []),
            conditions=list(d.get("conditions", []) or []),
        )

    def to_dict(self) -> dict:
        return {
            "age": self.age,
            "gender": self.gender,
            "sleep_time": self.sleep_time,
            "wake_time": self.wake_time,
            "sleep_quality": self.sleep_quality,
            "exercise_freq": self.exercise_freq,
            "diet_habits": self.diet_habits,
            "goals": self.goals,
            "conditions": self.conditions,
        }

    @property
    def has_basic(self) -> bool:
        return bool(self.sleep_time or self.wake_time or self.exercise_freq >= 0
                    or self.diet_habits or self.goals or self.age)


# ============================================================
# 建议生成（纯逻辑，返回结构化建议）
# ============================================================

def build_routine_advice(p: HealthProfile) -> List[str]:
    """日常作息建议。"""
    tips: List[str] = []
    dur = _sleep_duration_hours(p.sleep_time, p.wake_time)
    if dur is not None:
        if dur < 6.5:
            tips.append(
                f"你目前睡眠约 {dur} 小时，偏少。试着把入睡时间提前 30 分钟，"
                f"先把总睡眠拉到 7 小时左右，精神会明显不一样。"
            )
        elif dur > 9:
            tips.append(
                f"你睡眠约 {dur} 小时，偏多。适度早起一点，把白天点亮，"
                f"反而更有精神、晚上也更好睡。"
            )
        else:
            tips.append(f"你目前睡眠约 {dur} 小时，节奏不错，保持住。")
    # 入睡过晚提示
    st = _parse_hhmm(p.sleep_time)
    if st and st[0] >= 23:
        tips.append("23 点后入睡会错过人体褪黑素分泌高峰，尽量在 23 点前放下手机。")
    elif st and st[0] >= 1:
        tips.append("凌晨后才睡对第二天的精力损耗很大，今晚试着比昨天早一小时躺下。")
    tips.append("固定起床时间比固定入睡更重要——哪怕前一晚晚睡，也尽量同点起，作息会更稳。")
    tips.append("午间 10-20 分钟的小憩能回血，但别超过 30 分钟，否则晚上更难入睡。")
    return tips


def build_diet_advice(p: HealthProfile) -> List[str]:
    """饮食建议（结合习惯与目标）。"""
    tips: List[str] = []
    habits = (p.diet_habits or "").lower()
    if "外卖" in habits or "外送" in habits:
        tips.append("外卖可以偶尔，但尽量选少油少辣、有蔬菜的搭配；每周给自己留 2-3 顿清爽家常。")
    if "甜" in habits or "糖" in habits:
        tips.append("偏甜的话，试着把奶茶/甜点从'每天'降到'每周两三次'，用水果代替部分甜食。")
    if "辣" in habits:
        tips.append("重辣会刺激肠胃，肠胃敏感时换成清淡一两顿，给身体缓冲。")
    if not tips:
        tips.append("三餐尽量规律，每餐有蛋白质+蔬菜+主食的搭配，比纠结某种'超级食物'更实在。")
    if "减脂" in p.goals or "瘦身" in p.goals or "减肥" in p.goals:
        tips.append("减脂不必挨饿：把精制碳水换成粗粮、先吃菜再吃肉和饭，饱腹感更久。")
    if "增肌" in p.goals:
        tips.append("增肌期保证每日蛋白质（如蛋奶豆鱼肉），训练后一餐补一点碳水+蛋白。")
    if "改善肠胃" in p.goals or "胃" in (p.diet_habits or ""):
        tips.append("肠胃弱就少食多餐、细嚼慢咽，少冰少油，晚饭别吃太撑。")
    tips.append("每天喝够水（约 1.5-2L，按体型调整），是最低成本的健康投资。")
    return tips


def build_exercise_advice(p: HealthProfile) -> List[str]:
    """运动建议。"""
    tips: List[str] = []
    freq = p.exercise_freq
    if freq <= 0:
        tips.append("从'动起来'开始：每天快走 20-30 分钟，或做家务、爬楼都算数，先养成习惯。")
    elif freq < 3:
        tips.append(
            f"你每周运动约 {freq} 次，已经不错。试着加到 3 次，"
            f"穿插有氧（快走/骑车）和拉伸，身体会更稳。"
        )
    else:
        tips.append(f"你每周运动 {freq} 次，保持得很好！注意安排休息日，避免过量训练。")
    if "久坐" in " ".join(p.conditions) or "腰酸" in " ".join(p.conditions) or "颈椎" in " ".join(p.conditions):
        tips.append("久坐族每小时起身 2-3 分钟，做做扩胸和脖颈拉伸，能明显缓解腰酸颈椎紧。")
    tips.append("运动强度以'能说话但有点喘'为宜，别一上来就冲高强度，容易受伤也难坚持。")
    tips.append("睡眠和训练一样重要——练后好好睡，才是身体真正变强的时候。")
    return tips


def generate_health_plan(p: HealthProfile) -> Dict:
    """生成完整健康计划（结构化）。"""
    plan = {
        "has_profile": p.has_basic,
        "routine": build_routine_advice(p),
        "diet": build_diet_advice(p),
        "exercise": build_exercise_advice(p),
        "disclaimer": HEALTH_DISCLAIMER,
    }
    # 针对身体状况的专项提醒
    extra: List[str] = []
    for c in p.conditions:
        if "久坐" in c or "腰酸" in c or "颈椎" in c:
            extra.append(f"关于「{c}」：每隔一小时起来活动，工位加个腰靠/垫高屏幕会有帮助。")
        elif "失眠" in c or "睡不好" in c:
            extra.append(f"关于「{c}」：睡前一小时远离强光屏幕，热水泡脚或听白噪音常能助眠。")
        elif "焦虑" in c or "压力大" in c:
            extra.append(f"关于「{c}」：每天留 10 分钟单纯呼吸/发呆，比刷手机更能卸下紧绷。")
        else:
            extra.append(f"关于「{c}」：多留意身体信号，必要时寻求专业意见。")
    if extra:
        plan["conditions_tips"] = extra
    return plan


# ============================================================
# LLM 提示构建
# ============================================================

def health_plan_prompt(p: HealthProfile, plan: Dict) -> str:
    """构建给 LLM 的健康建议提示（作为 llm_tool 返回内容）。"""
    profile_bits = []
    if p.age:
        profile_bits.append(f"年龄 {p.age}")
    if p.gender:
        profile_bits.append("男" if p.gender == "male" else "女")
    if p.sleep_time and p.wake_time:
        profile_bits.append(f"作息 {p.sleep_time}→{p.wake_time}")
    if p.exercise_freq >= 0:
        profile_bits.append(f"每周运动 {p.exercise_freq} 次")
    if p.goals:
        profile_bits.append("目标：" + "、".join(p.goals))
    if p.diet_habits:
        profile_bits.append(f"饮食情况：{p.diet_habits}")
    if p.conditions:
        profile_bits.append("身体状况：" + "、".join(p.conditions))
    profile_txt = "；".join(profile_bits) if profile_bits else "（用户尚未提供具体信息，请给通用、温和的起步建议）"

    def _fmt(title: str, items: List[str]) -> str:
        if not items:
            return ""
        return f"{title}：\n" + "\n".join(f"  \u2022 {t}" for t in items)

    blocks = [
        _fmt("\u23F0 作息", plan.get("routine", [])),
        _fmt("\uD83C\uDF74 饮食", plan.get("diet", [])),
        _fmt("\uD83C\uDFCB 运动", plan.get("exercise", [])),
    ]
    if plan.get("conditions_tips"):
        blocks.append(_fmt("\uD83D\uDCED 专项提醒", plan["conditions_tips"]))
    advice_body = "\n\n".join(b for b in blocks if b)

    return f"""[健康生活指导·朋友式建议]
已知用户画像：{profile_txt}

以下是基于其画像生成的生活建议（已是结构化内容，请自然转述，别照念条目）：
{advice_body}

请以"关心 TA 的朋友"口吻，把这些建议揉成一段温暖的日常叮嘱：
1. 先肯定 TA 已经在意健康这件事本身（愿意调整就是好的开始）
2. 挑 2-3 条最贴合 TA 现状的说，别一次性全倒，免得有压力
3. 用'可以试试''慢慢来'这种口吻，不用'你必须'
4. 如果 TA 目标里有减脂/增肌/睡眠等，给一个今天就能做的小行动
5. 结尾点一句：健康是长期的事，不必一天完美，慢慢来就好

务必在结尾附上这句提醒（可换更口语的说法）：
「这些只是生活方式的小建议，不能代替医生。真不舒服记得去看医生哦。」

朋友相处原则：陪伴 > 说教；鼓励 > 苛责。
"""
