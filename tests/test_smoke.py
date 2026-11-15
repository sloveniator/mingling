"""
tests/test_smoke.py -- 纯逻辑冒烟测试（不依赖真实 AstrBot 运行时）
=====================================================================
覆盖：
  - 命盘格式化（八字/紫微/六爻）
  - 宫位/大运流年提取（P1 去重后的共用函数）
  - 情绪检测 / 关系模式常量与状态机
  - 人格序列化往返
  - 暗盘伪装：洞察文本零命理术语（产品命门）
  - 架构守卫：梦境模块不引用任何暗盘通道（"暗盘不进梦境"约束）
  - 连接池复用：MingbuClient 复用同一 session、可关闭重建（P0）

运行：.venv/Scripts/python -m pytest tests/ -q
"""
import asyncio
import inspect
from datetime import datetime

import dream  # 用于架构守卫检查
from astrology import current_da_yun_liu_nian, extract_key_palaces
from client import MingbuClient
from counseling import EmotionalTracker
from formatters import (
    _context_summary,
    extract_liuyao_terms,
    extract_qimen_terms,
    format_bazi,
    format_liuyao,
    format_qimen,
    format_signals,
    format_technique_grounding,
    format_ziwei,
)
from persona import PersonaState, Relationship
from silent_divination import SilentDiviner

# 命理禁用词（暗盘伪装铁律：朋友口吻中绝不能出现）
FORBIDDEN = ["命盘", "八字", "紫微", "占卜", "卜卦", "卦象",
             "运势", "排盘", "起卦", "算命", "流年"]


# ------------------------------------------------------------
# 格式化
# ------------------------------------------------------------

def test_format_bazi_smoke():
    data = {
        "chart": {
            "birthInfo": {"name": "测试", "year": 1990, "month": 5, "day": 20,
                          "hour": 8, "gender": "male"},
            "dayMaster": {"gan": "甲", "element": "木", "yinYang": "yang"},
            "wuXing": {"木": 2, "火": 1},
            "strength": {"level": "身旺"},
            "pattern": {"name": "建禄格"},
            "yongShen": {"yongShen": "火", "xiShen": "土", "jiShen": "金"},
            "currentDaYun": {"gan": "丙", "zhi": "寅"},
            "currentLiuNian": {"gan": "庚", "zhi": "子"},
        }
    }
    out = format_bazi(data)
    assert "八字命盘" in out
    assert "测试" in out
    assert "身旺" in out


def test_format_bazi_wuxing_count_and_dayun_ganzhi():
    """真实后端契约：wuXing 为 {count:{...}} 结构、大运使用单个 ganZhi 字段（童限为空）。"""
    data = {
        "chart": {
            "birthInfo": {"name": "测试", "year": 1990, "month": 5, "day": 20,
                          "hour": 4, "gender": "male"},
            "year": {"gan": "庚", "zhi": "午", "naYin": "路旁土"},
            "month": {"gan": "辛", "zhi": "巳", "naYin": "白蜡金"},
            "day": {"gan": "乙", "zhi": "酉", "naYin": "泉中水"},
            "hour": {"gan": "庚", "zhi": "辰", "naYin": "白蜡金"},
            "dayMaster": {"gan": "乙", "element": "木", "yinYang": "阴"},
            "wuXing": {"count": {"金": 4, "木": 1, "水": 0, "火": 2, "土": 1},
                       "dominant": ["金"], "weak": ["水"]},
            "strength": {"level": "中和偏旺"},
            "pattern": {"name": "正官格"},
            "daYun": [
                {"ganZhi": "", "startAge": 1, "endAge": 6, "isTongXian": True},
                {"ganZhi": "壬午", "startAge": 7, "endAge": 16},
            ],
        }
    }
    out = format_bazi(data)
    # 五行：从 wuXing.count 读取非零项
    assert "金×4" in out
    assert "木×1" in out
    assert "水×0" not in out  # 零计数不显示
    # 大运：优先 ganZhi 字段；童限阶段空 ganZhi 显示「童限」
    assert "1-6岁" in out and "童限" in out
    assert "7-16岁" in out and "壬午" in out


def test_format_ziwei_smoke():
    data = {
        "analysis": {
            "ziwei": {
                "birthInfo": {"year": 1990, "month": 5, "day": 20, "hour": 8,
                              "gender": "female"},
                "wuxingJuName": "水二局",
                "mingGongBranch": 2,    # 寅宫
                "shenGongBranch": 8,    # 申宫
                "palaces": [
                    {"branch": 0, "name": "命宫",
                     "stars": [{"name": "紫微", "type": "major"}]},
                    {"branch": 1, "name": "兄弟宫",
                     "stars": [{"name": "天机", "type": "major"}]},
                ],
                "daXians": [{"startAge": 0, "endAge": 9, "palaceName": "命宫"}],
            }
        }
    }
    out = format_ziwei(data)
    assert "紫微斗数命盘" in out
    assert "紫微" in out  # 星名允许出现在格式化输出里（这是命盘文本，非朋友口吻）
    assert "命宫：寅宫" in out
    assert "身宫：申宫" in out
    assert "0-9岁" in out


def test_format_liuyao_smoke():
    data = {
        "chart": {
            "originalGua": {"name": "乾为天"},
            "changedGua": {"name": "天风姤"},
            "question": "换工作好吗",
            "movingPositions": [2],
            "lines": [
                {"position": 1, "positionName": "初爻", "yang": True, "moving": False,
                 "liuQin": "兄弟", "liuShen": "青龙", "isShi": True, "isYing": False},
                {"position": 2, "positionName": "二爻", "yang": True, "moving": True,
                 "liuQin": "父母", "liuShen": "朱雀", "isShi": False, "isYing": False},
                {"position": 3, "positionName": "三爻", "yang": True, "moving": False,
                 "liuQin": "官鬼", "liuShen": "勾陈", "isShi": False, "isYing": False},
                {"position": 4, "positionName": "四爻", "yang": True, "moving": False,
                 "liuQin": "妻财", "liuShen": "螣蛇", "isShi": False, "isYing": True},
                {"position": 5, "positionName": "五爻", "yang": True, "moving": False,
                 "liuQin": "父母", "liuShen": "白虎", "isShi": False, "isYing": False},
                {"position": 6, "positionName": "上爻", "yang": True, "moving": False,
                 "liuQin": "兄弟", "liuShen": "玄武", "isShi": False, "isYing": False},
            ],
        }
    }
    out = format_liuyao(data)
    assert "六爻占卜" in out
    assert "乾为天" in out
    assert "天风姤" in out
    assert "动爻" in out
    assert "二爻" in out


def test_format_qimen_smoke():
    # 真实 QimenChart 形状（后端 /api/qimen 返回 { chart: {...} }）
    data = {
        "chart": {
            "year": 2026, "month": 8, "day": 16, "hour": 4,  # 辰时（branch 0-11）
            "jieqi": "立秋", "dun": "阴", "yuan": "中元", "juNumber": 4,
            "yearGanZhi": "丙午", "monthGanZhi": "丙申", "dayGanZhi": "癸巳", "hourGanZhi": "丙辰",
            "xunkou": "甲午", "zhiFuStar": "天英", "zhiShiDoor": "景门",
            "gongs": [
                {"luo": 1, "star": "天蓬", "door": "休门", "god": "值符",
                 "tianPan": "乙", "diPan": "丙", "isKong": False, "isMa": False},
                {"luo": 2, "star": "天芮", "door": "死门", "god": "腾蛇",
                 "tianPan": "辛", "diPan": "庚", "isKong": True, "isMa": False},
            ],
            "yongShen": {"riGanGong": 1, "shiGanGong": 4, "kaiMenGong": 8,
                         "shengMenGong": 3, "xiuMenGong": 1, "zhiFuGong": 1, "zhiShiGong": 4},
            "summary": "阴遁四局，时柱丙辰。",
        }
    }
    out = format_qimen(data)
    assert "奇门遁甲" in out
    assert "辰时" in out          # hour=4 → 辰时
    assert "阴遁4局" in out or "阴遁·4局" in out or "4局" in out
    assert "值符" in out
    assert "九宫" in out
    assert "宫1" in out
    assert "空亡" in out          # 宫2 空亡
    assert "用神落宫" in out
    assert "日干落宫1" in out


def test_format_signals_and_context():
    assert format_signals([]) == ""
    sig = format_signals([{
        "dimension": "事业", "verdict": "两强相济",
        "consistent": True, "baziView": "官旺", "ziweiView": "官禄见权",
    }])
    assert "交叉印证" in sig
    assert "事业" in sig
    assert "两强相济" in sig
    assert "一致" in sig
    assert _context_summary([]) == ""
    assert "用户近期在聊" in _context_summary(["hi", "there"])


# ------------------------------------------------------------
# P1 去重：宫位/大运流年提取
# ------------------------------------------------------------

def test_extract_key_palaces():
    analysis = {
        "ziwei": {
            "palaces": [
                {"name": "命宫", "stars": [{"name": "紫微", "type": "major"}]},
                {"name": "夫妻宫", "stars": [{"name": "太阳", "type": "major"},
                                            {"name": "太阴", "type": "major"}]},
                {"name": "福德宫", "stars": []},   # 空宫
                {"name": "奴仆宫", "stars": [{"name": "巨门", "type": "major"}]},  # 非关键，忽略
            ]
        }
    }
    res = extract_key_palaces(analysis)
    assert res["命宫"] == "紫微"
    assert res["夫妻宫"] == "太阳、太阴"
    assert res["福德宫"] == "空宫"
    assert "奴仆宫" not in res


def test_current_da_yun_liu_nian():
    # 后端 buildBaziChart 序列化的是 daYun 数组 + birthInfo，不挂 currentDaYun
    cur = datetime.now().year
    analysis = {
        "bazi": {
            "birthInfo": {"year": cur - 30},  # 当前 30 岁
            "daYun": [
                {"ganZhi": "甲子", "startAge": 0, "endAge": 9, "isTongXian": True,
                 "liuNian": []},
                {"ganZhi": "乙丑", "startAge": 10, "endAge": 19, "isTongXian": False,
                 "liuNian": []},
                {"ganZhi": "丙寅", "startAge": 20, "endAge": 29, "isTongXian": False,
                 "liuNian": []},
                {"ganZhi": "丁卯", "startAge": 30, "endAge": 39, "isTongXian": False,
                 "liuNian": [
                     {"ganZhi": "甲辰", "year": cur - 6},
                     {"ganZhi": "庚子", "year": cur},
                 ]},
            ],
        }
    }
    res = current_da_yun_liu_nian(analysis)
    assert res["da_yun"] == "丁卯"      # 30 岁落在第 4 步大运
    assert res["liu_nian"] == "庚子"    # 本年流年
    assert current_da_yun_liu_nian({}) == {}
    # 缺出生年 / 空 daYun 时不抛错，返回空
    assert current_da_yun_liu_nian({"bazi": {}}) == {}
    assert current_da_yun_liu_nian({"bazi": {"daYun": []}}) == {}


# ------------------------------------------------------------
# 情绪 / 关系 / 人格
# ------------------------------------------------------------

def test_emotion_detect():
    t = EmotionalTracker()
    found = t.detect_emotions("我最近好焦虑，压力很大，快撑不住了")
    assert "stress" in found


def test_relationship_modes_and_state_machine():
    # 常量可用性
    assert Relationship.MODE_ATTACHED == "attached"
    assert Relationship.MODE_HURT == "hurt"
    assert Relationship.MODE_NORMAL in Relationship.MODES

    r = Relationship("u1")
    r.score = 80
    r._recompute_mode()
    assert r.mode == Relationship.MODE_ATTACHED
    r.score = 50
    r._recompute_mode()
    assert r.mode == Relationship.MODE_WARMING
    r.score = 10
    r._recompute_mode()
    assert r.mode == Relationship.MODE_NORMAL
    r.nudge("hurt")
    assert r.mode == Relationship.MODE_HURT
    r.nudge("apology")  # 和解应解除受伤态
    assert r.mode != Relationship.MODE_HURT


def test_relationship_hurt_is_sticky():
    """受伤态不应因普通 interact 立即回弹（粘性）。"""
    r = Relationship("u1")
    r.score = 80
    r.nudge("hurt")
    assert r.mode == Relationship.MODE_HURT
    # 普通正面互动不应把受伤态改回 attached
    r.interact(positive=True)
    assert r.mode == Relationship.MODE_HURT
    # 安慰到阈值才解除
    r.score = 40
    r.nudge("comfort")
    assert r.mode == Relationship.MODE_NORMAL


def test_persona_roundtrip():
    p = PersonaState("小灵")
    p.mood = 77.0
    p.curiosities = ["一个梗"]
    p.melons = ["一个瓜"]
    d = p.to_dict()
    p2 = PersonaState.from_dict(d)
    assert p2.name == "小灵"
    assert p2.mood == 77.0
    assert p2.curiosities == ["一个梗"]
    assert p2.melons == ["一个瓜"]


# ------------------------------------------------------------
# 暗盘伪装：洞察文本零命理术语（产品命门）
# ------------------------------------------------------------

def _sample_analysis():
    cur = datetime.now().year
    return {
        "bazi": {
            "birthInfo": {"year": cur - 28},
            "dayMaster": {"element": "金"},
            "strength": {"level": "身旺"},
            # 后端序列化的是 daYun 数组（非 currentDaYun）
            "daYun": [
                {"ganZhi": "丙寅", "startAge": 20, "endAge": 29, "isTongXian": False,
                 "liuNian": [{"ganZhi": "庚子", "year": cur}]},
            ],
        },
        "ziwei": {
            "palaces": [
                {"name": "命宫", "isMingGong": True,
                 "stars": [{"name": "紫微", "type": "major"}]},
                {"name": "福德宫", "stars": [{"name": "太阳", "type": "major"}]},
            ],
        },
    }


def test_silent_insights_no_forbidden_terms():
    """暗盘洞察（即"朋友的直觉"实际内容）不得出现任何命理术语。"""
    insights = SilentDiviner(None)._extract_insights(_sample_analysis(), "压力")
    assert insights, "洞察不应为空"
    for w in FORBIDDEN:
        assert w not in insights, f"暗盘洞察意外包含禁用词：{w}"


# ------------------------------------------------------------
# 架构守卫：梦境引用暗盘但不泄露术语
# ------------------------------------------------------------

def test_dream_diviner_access_safe():
    """dream.py 引用 diviner 是允许的，但不能在梦境输出中泄露术语。

    新设计：dream.py 通过 self.plugin.diviner 周期性使用暗盘洞察，
    但输出（夜梦 + 预感梦）不得出现命理术语。
    """
    src = inspect.getsource(dream)
    # 确认 dream.py 确实引用了 diviner（新设计的核心意图）
    assert "diviner" in src, "dream.py 需要使用 diviner"
    # P3 起预感信号经 diviner.premonition_signal 接口获取（_premonition_signal_from_diviner），
    # 旧 premonition_cue 已迁移，这里钉住当前接口名。
    assert "premonition_signal" in src, "dream.py 应通过 premonition_signal 获取预感信号"
    assert "get_insights" in src, "dream.py 应通过 get_insights 获取暗盘洞察"
    # 但不应直接引用 silent_divination 模块顶层名称或内部函数
    for token in ["weave_insights", "_extract_insights",
                  "silent_divination", "SilentDiviner"]:
        assert token not in src, f"dream.py 不应直接引用暗盘模块函数：{token}"


# ------------------------------------------------------------
# P0：连接池复用
# ------------------------------------------------------------

def test_mingbu_client_session_reuse():
    c = MingbuClient("http://localhost:1")  # 不真正发起请求

    async def go():
        s1 = await c._ensure_session()
        s2 = await c._ensure_session()
        assert s1 is s2, "session 应被复用而非反复创建"
        await c.close()
        assert c._session is None
        s3 = await c._ensure_session()
        assert s3 is not None and not s3.closed

    asyncio.run(go())


# ------------------------------------------------------------
# 技法知识库（/api/techniques）检索词提取 & 条目格式化
# ------------------------------------------------------------

def test_extract_liuyao_terms():
    chart = {
        "originalGua": {"name": "天雷无妄"},
        "changedGua": {"name": "火泽睽"},
        "lines": [
            {"position": 1, "liuQin": "父母", "liuShen": "玄武"},
            {"position": 4, "liuQin": "妻财", "liuShen": "青龙"},
            {"position": 5, "liuQin": "妻财", "liuShen": "青龙"},  # 重复应去重
        ],
    }
    terms = extract_liuyao_terms(chart)
    assert "天雷无妄" in terms
    assert "火泽睽" in terms
    assert "父母" in terms and "妻财" in terms
    assert "玄武" in terms and "青龙" in terms
    # 去重
    assert terms.count("妻财") == 1
    assert terms.count("青龙") == 1


def test_extract_qimen_terms():
    chart = {
        "zhiFuStar": "天蓬",
        "zhiShiDoor": "休门",
        "gongs": [
            {"star": "天冲", "door": "伤门", "god": "腾蛇"},
        ],
        "yongShen": {"career": "开门"},
    }
    terms = extract_qimen_terms(chart)
    assert "天蓬" in terms and "休门" in terms
    assert "天冲" in terms and "伤门" in terms and "腾蛇" in terms
    assert "开门" in terms
    # 通用易理基础词兜底，确保命中奇门 common 条目
    for kw in ("用神", "旺衰", "五行", "生克"):
        assert kw in terms


def test_format_technique_grounding():
    entries = [
        {"book": "增删卜易", "category": "用神", "content": "妻财为用神，旺相则财可得"},
        {"book": "", "category": "", "content": "世应相生，谋事可成"},
        {"book": "卜筮正宗", "category": "世应", "content": ""},  # 空内容应跳过
    ]
    out = format_technique_grounding(entries)
    assert "命理技法依据" in out
    assert "增删卜易" in out and "妻财为用神" in out
    assert "世应相生" in out  # 无 book/category 的条目也保留
    assert "卜筮正宗" not in out  # 空内容条目被跳过


def test_format_technique_grounding_empty():
    assert format_technique_grounding([]) == ""
    assert format_technique_grounding(None) == ""


# ------------------------------------------------------------
# 复盘审查修复的回归测试（v4.8.68）
# ------------------------------------------------------------

def test_format_bazi_yongshen_array_contract():
    """后端用神字段为 yong/xi/ji（五行数组），须正确 join 展示，并兼容旧契约。"""
    data = {
        "chart": {
            "birthInfo": {"name": "测试", "year": 1990, "month": 5, "day": 20, "hour": 4, "gender": "male"},
            "dayMaster": {"gan": "乙", "element": "木", "yinYang": "yin"},
            "wuXing": {"count": {"金": 4, "木": 1, "水": 0, "火": 2, "土": 1}},
            "strength": {"level": "中和偏旺"},
            "pattern": {"name": "正官格"},
            "yongShen": {"yong": ["金", "土"], "xi": ["金", "土", "火"], "ji": ["木", "水"]},
            "daYun": [],
        }
    }
    out = format_bazi(data)
    assert "用神：金、土" in out
    assert "喜神：金、土、火" in out
    assert "忌神：木、水" in out
    # 不应出现 Python 列表的引号/方括号
    assert "['" not in out and "[" not in out


def test_format_bazi_yongshen_legacy_contract():
    """旧契约（yongShen/xiShen/jiShen 字符串）仍应能展示。"""
    data = {
        "chart": {
            "birthInfo": {"name": "测试", "year": 1990, "month": 5, "day": 20, "hour": 4, "gender": "male"},
            "dayMaster": {"gan": "乙", "element": "木", "yinYang": "yin"},
            "yongShen": {"yongShen": "火", "xiShen": "土", "jiShen": "金"},
            "daYun": [],
        }
    }
    out = format_bazi(data)
    assert "用神：火" in out


def test_format_bazi_minggong_shengong_taiyuan():
    """命宫/身宫/胎元（后端 mingGong/shenGong/taiYuan 干支字符串）应展示。"""
    data = {
        "chart": {
            "birthInfo": {"name": "测试", "year": 1990, "month": 5, "day": 20, "hour": 4, "gender": "male"},
            "dayMaster": {"gan": "乙", "element": "木", "yinYang": "yin"},
            "mingGong": "甲申", "shenGong": "丙戌", "taiYuan": "壬申",
            "daYun": [],
        }
    }
    out = format_bazi(data)
    assert "命宫：甲申" in out
    assert "身宫：丙戌" in out
    assert "胎元：壬申" in out


def test_format_ziwei_minggong_in_zi():
    """命宫/身宫在「子」（branch=0）时应正常显示，不被 if mg 跳过。"""
    from formatters import format_ziwei
    data = {
        "birthInfo": {"name": "测试", "year": 1990, "month": 1, "day": 1, "hour": 0, "gender": "male"},
        "mingGongBranch": 0, "shenGongBranch": 0, "palaces": [],
    }
    out = format_ziwei(data)
    assert "命宫：子宫" in out
    assert "身宫：子宫" in out
