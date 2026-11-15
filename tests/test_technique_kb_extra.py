"""六爻/梅花/奇门/紫微技法库（technique_kb.py / ziwei_kb.py）单元测试。"""


def _liuyao_chart():
    return {
        "chart": {
            "originalGua": {"name": "天雷无妄"},
            "changedGua": {"name": "火泽睽"},
            "lines": [
                {"position": 1, "positionName": "初爻", "liuQin": "父母", "liuShen": "玄武", "moving": False},
                {"position": 4, "positionName": "四爻", "liuQin": "妻财", "liuShen": "青龙", "moving": True},
            ],
            "question": "该不该换工作",
        }
    }


def _ziwei_chart():
    return {
        "palaces": [
            {"name": "命宫", "isShenGong": True,
             "stars": [{"name": "紫微", "type": "major", "siHua": "权"},
                       {"name": "贪狼", "type": "major"},
                       {"name": "左辅", "type": "lucky"}]},
            {"name": "财帛宫", "isShenGong": False,
             "stars": [{"name": "武曲", "type": "major"}]},
            {"name": "官禄宫", "isShenGong": False,
             "stars": [{"name": "廉贞", "type": "major"}]},
            {"name": "夫妻宫", "isShenGong": False,
             "stars": [{"name": "天府", "type": "major", "siHua": "忌"}]},
            {"name": "福德宫", "isShenGong": False, "stars": []},
            {"name": "疾厄宫", "isShenGong": False, "stars": []},
            {"name": "迁移宫", "isShenGong": False, "stars": []},
        ],
        "daXians": [{"palaceName": "命宫", "startAge": 4, "endAge": 13}],
    }


def test_build_liuyao_knowledge_grounding():
    from technique_kb import build_liuyao_knowledge_grounding

    out = build_liuyao_knowledge_grounding(_liuyao_chart()["chart"])
    assert "六爻技法" in out
    for kw in ("世应", "六亲", "用神", "六神", "动变", "旺衰", "应期"):
        assert kw in out, f"缺少六爻技法：{kw}"
    assert "八卦类象" in out            # 八卦类象已附
    assert "天雷无妄" in out or "雷" in out  # 卦象对应的八卦类象
    assert "妻财" in out                # 动爻六亲提示
    # 六十四卦卦象（卦辞判曰）
    assert "六十四卦卦象" in out
    assert "元亨利贞" in out            # 无妄卦辞
    assert "火泽睽" in out              # 变卦


def test_hexagram_kb_complete():
    """六十四卦卦象库应完整（64 卦，每卦含五行/卦辞/象辞/判曰）。"""
    from technique_kb import HEXAGRAM_KB

    assert len(HEXAGRAM_KB) == 64
    for name, entry in HEXAGRAM_KB.items():
        assert entry.get("wx"), f"{name} 缺五行"
        assert entry.get("ci"), f"{name} 缺卦辞"
        assert entry.get("xiang"), f"{name} 缺象辞"
        assert entry.get("pan"), f"{name} 缺判曰"


def test_hexagram_lookup():
    """六十四卦查询应支持全称与简称。"""
    from technique_kb import _format_hexagram, _hexagram

    assert _hexagram("乾为天")["xiang"] == "天行健，君子以自强不息。"
    assert _hexagram("无妄")["ci"].startswith("元亨利贞")
    assert _hexagram("泰")["pan"].startswith("天地交泰")
    assert _format_hexagram("天雷无妄").startswith("· 【天雷无妄】五行属木")
    assert _hexagram("不存在的卦") is None
    assert _format_hexagram("不存在的卦") == ""


def test_build_meihua_knowledge_grounding():
    from technique_kb import build_meihua_knowledge_grounding

    chart = {"benGua": {"name": "天地否"}, "huGua": {"name": "风山渐"}, "bianGua": {"name": "天水讼"}}
    out = build_meihua_knowledge_grounding(chart)
    assert "梅花易数" in out
    for kw in ("体用", "生克", "主互变", "卦气", "外应", "应期"):
        assert kw in out, f"缺少梅花技法：{kw}"
    assert "八卦类象" in out


def test_build_qimen_knowledge_grounding():
    from technique_kb import build_qimen_knowledge_grounding

    chart = {"dun": "阳遁", "yuan": "上元", "juNumber": 5,
             "zhiFuStar": "天英", "zhiShiDoor": "景门"}
    out = build_qimen_knowledge_grounding(chart)
    assert "奇门遁甲" in out
    for kw in ("盘式", "用神", "生克", "三奇", "吉凶格", "伏反吟", "应期"):
        assert kw in out, f"缺少奇门技法：{kw}"
    assert "阳遁" in out and "天英" in out  # 局象要点


def test_build_ziwei_knowledge_grounding():
    from ziwei_kb import build_ziwei_knowledge_grounding

    out = build_ziwei_knowledge_grounding(_ziwei_chart())
    assert "紫微斗数" in out
    # 7 条全局原则（含补全的 4 条）
    for kw in ("命盘事实优先", "知识导向不是机械判词", "避免绝对断事", "四化作为动态引动", "本命大限流年分层"):
        assert kw in out, f"缺少原则：{kw}"
    # 命宫/身宫主星：紫微、贪狼
    assert "紫微" in out and "贪狼" in out
    # 高价值宫位
    assert "财帛宫" in out and "官禄宫" in out and "夫妻宫" in out
    # 四化
    assert "化权" in out and "化忌" in out
    # 四化入宫（化权入命宫、化忌入夫妻宫）
    assert "化权入命宫" in out and "化忌入夫妻宫" in out
    # 辅星（左辅）
    assert "左辅右弼" in out
    # 组合格局（紫微在命宫）
    assert "紫微在命宫" in out
    # 运限（有大限数据）
    assert "大限阶段主题" in out
    # 古籍应证
    assert "古籍应证" in out


def test_ziwei_knowledge_completeness():
    """紫微知识库应完整包含后端 entries 的全部类别（不遗漏）。"""
    from ziwei_kb import (
        COMBO_ENTRIES,
        FORTUNE_ENTRIES,
        KLINE_ENTRIES,
        MATCH_ENTRIES,
        PALACE_ENTRIES,
        PRINCIPLES,
        SIHUA_ENTRIES,
        SIHUA_PALACE_ENTRIES,
        STAR_ENTRIES,
        SUPPORT_ENTRIES,
    )

    assert len(PRINCIPLES) == 7
    assert len(STAR_ENTRIES) == 14
    assert len(PALACE_ENTRIES) == 12
    assert len(SIHUA_ENTRIES) == 4
    assert len(SIHUA_PALACE_ENTRIES) == 17
    assert len(SUPPORT_ENTRIES) == 6
    assert len(FORTUNE_ENTRIES) == 5
    assert len(MATCH_ENTRIES) == 6
    assert len(KLINE_ENTRIES) == 5
    assert len(COMBO_ENTRIES) == 29


def test_ziwei_learned_lookup():
    """learned 古籍应证库（concepts/geju/qa）应可查询。"""
    from ziwei_kb import lookup_concept, lookup_geju, lookup_qa

    assert "古籍应证" in lookup_concept("紫微")
    assert "古籍格局" in lookup_geju("杀破狼")
    assert lookup_qa("紫微") != ""
    # 未命中返回空串
    assert lookup_concept("不存在的星") == ""
    assert lookup_geju("不存在的格局") == ""


def test_ziwei_grounding_fault_tolerant():
    from ziwei_kb import build_ziwei_knowledge_grounding

    assert build_ziwei_knowledge_grounding({}) == ""
    assert build_ziwei_knowledge_grounding(None) == ""
    assert build_ziwei_knowledge_grounding({"palaces": []}) == ""


def test_trigram_of_name():
    from technique_kb import _trigrams_of_name

    assert _trigrams_of_name("乾为天") == ("乾", "乾")
    assert _trigrams_of_name("天地否") == ("乾", "坤")
    assert _trigrams_of_name("火泽睽") == ("离", "兑")
    assert _trigrams_of_name("天雷无妄") == ("乾", "震")
    assert _trigrams_of_name("") is None
    assert _trigrams_of_name("泽") is None  # 单字无法取上下卦


def test_expanded_kb_completeness():
    """扩充知识库应完整：神煞/纳音/刑冲合害/奇门九星八门八神/紫微杂曜六吉六煞。"""
    from technique_kb import (
        NAYIN_KB,
        QIMEN_DOORS_KB,
        QIMEN_GODS_KB,
        QIMEN_STARS_KB,
        SHENSHA_KB,
        ZHICHAO_KB,
    )
    from ziwei_kb import LUCKY_STAR_KB, MISC_STAR_KB, SHA_STAR_KB

    assert len(SHENSHA_KB) == 10      # 神煞
    assert len(NAYIN_KB) == 30        # 六十甲子纳音
    assert len(ZHICHAO_KB) == 7       # 刑冲合害
    assert len(QIMEN_STARS_KB) == 9   # 奇门九星
    assert len(QIMEN_DOORS_KB) == 8   # 奇门八门
    assert len(QIMEN_GODS_KB) == 8    # 奇门八神
    assert len(MISC_STAR_KB) == 11    # 紫微杂曜
    assert len(LUCKY_STAR_KB) == 7    # 六吉（含禄存）
    assert len(SHA_STAR_KB) == 6      # 六煞


def test_bazi_grounding_injects_shensha_nayin_zichao():
    from technique_kb import build_bazi_knowledge_grounding

    chart = {
        "dayMaster": {"gan": "乙", "element": "木", "yinYang": "阴"},
        "year": {"gan": "庚", "zhi": "午", "shiShenGan": "正官", "shiShenZhi": ["正财"], "naYin": "路旁土"},
        "month": {"gan": "辛", "zhi": "巳", "shiShenGan": "七杀", "shiShenZhi": ["正官"], "naYin": "白蜡金"},
        "day": {"gan": "乙", "zhi": "酉", "shiShenGan": "比肩", "shiShenZhi": ["七杀"], "naYin": "泉中水"},
        "hour": {"gan": "庚", "zhi": "辰", "shiShenGan": "正官", "shiShenZhi": ["正财"], "naYin": "白蜡金"},
        "pattern": {"name": "正官格", "type": "正格"},
        "yongShen": {"summary": "用神为火"},
        "daYun": [{"ganZhi": "壬午", "isTongXian": False, "shiShen": "正印"}],
    }
    out = build_bazi_knowledge_grounding(chart)
    assert "神煞" in out and "天乙贵人" in out
    assert "刑冲合害" in out and "六合" in out and "三刑" in out
    assert "纳音" in out and "路旁土" in out and "白蜡金" in out


def test_qimen_grounding_injects_stars_doors_gods():
    from technique_kb import build_qimen_knowledge_grounding

    out = build_qimen_knowledge_grounding({"dun": "阳遁", "juNumber": 5, "zhiFuStar": "天英", "zhiShiDoor": "景门"})
    assert "九星" in out and "天蓬" in out and "天英" in out
    assert "八门" in out and "生门" in out and "开门" in out
    assert "八神" in out and "值符" in out and "九天" in out


def test_ziwei_grounding_injects_misc_and_individual_stars():
    from ziwei_kb import build_ziwei_knowledge_grounding

    chart = {
        "palaces": [
            {"name": "命宫", "isShenGong": True, "stars": [
                {"name": "紫微", "type": "major"},
                {"name": "红鸾", "type": "minor"},
                {"name": "文昌", "type": "lucky"},
                {"name": "擎羊", "type": "sha"},
            ]},
        ],
        "daXians": [],
    }
    out = build_ziwei_knowledge_grounding(chart)
    assert "杂曜" in out and "红鸾" in out
    assert "六吉六煞详解" in out and "文昌" in out and "擎羊" in out


def test_ziwei_sihua_contract():
    """后端 /api/combined 的 star.siHua 即生年四化（单字 禄/权/科/忌）；

    须正确读取 siHua 并注入【四化】与【四化入宫】段落；自化(selfHua/selfHuaList)
    语义不同，不可被误读为生年四化。
    """
    from ziwei_kb import build_ziwei_knowledge_grounding

    chart = {
        "palaces": [
            {"name": "命宫",
             "stars": [{"name": "紫微", "type": "major", "siHua": "权"}]},
            {"name": "财帛宫",
             "stars": [{"name": "天机", "type": "major", "siHua": "禄"}]},
        ],
        "daXians": [],
    }
    out = build_ziwei_knowledge_grounding(chart)
    assert "【紫微知识库 · 四化】" in out
    assert "化权" in out        # 紫微 siHua=权 → 四化段落
    assert "化禄" in out        # 天机 siHua=禄 → 四化段落
    assert "【紫微知识库 · 四化入宫】" in out   # 化权入命宫 + 化禄入财帛宫 均命中

    # 仅含自化(selfHua/selfHuaList)、无生年四化 siHua → 不应生成四化相关段落
    chart2 = {
        "palaces": [
            {"name": "命宫",
             "stars": [{"name": "天机", "type": "major",
                        "selfHua": "忌",
                        "selfHuaList": [{"siHua": "禄", "direction": "离心"}]}]},
        ],
        "daXians": [],
    }
    out2 = build_ziwei_knowledge_grounding(chart2)
    assert "【紫微知识库 · 四化】" not in out2
    assert "【紫微知识库 · 四化入宫】" not in out2
