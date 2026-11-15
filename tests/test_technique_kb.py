"""八字技法知识库（technique_kb.py）单元测试。

验证自 mingbu 后端移植的 build_bazi_knowledge_grounding：
  1. 按命盘字段精准注入十神/格局/日主/盲派/衰旺派等技法条目；
  2. 字段缺失时优雅降级、不抛错；
  3. query_bazi 工具会把技法 grounding 附到返回文本。
"""

# 对齐 mingbu 后端 BaziChart 结构的样例命盘（乙木日主，正官格）
SAMPLE_CHART = {
    "dayMaster": {"gan": "乙", "element": "木", "yinYang": "阴"},
    "year": {"gan": "庚", "zhi": "午", "shiShenGan": "正官", "shiShenZhi": ["正财"]},
    "month": {"gan": "辛", "zhi": "巳", "shiShenGan": "七杀", "shiShenZhi": ["正官"]},
    "day": {"gan": "乙", "zhi": "酉", "shiShenGan": "比肩", "shiShenZhi": ["七杀"]},
    "hour": {"gan": "庚", "zhi": "辰", "shiShenGan": "正官", "shiShenZhi": ["正财"]},
    "pattern": {"name": "正官格", "type": "正格", "detail": "月令正官透出"},
    "yongShen": {"yong": ["火"], "xi": ["土"], "ji": ["金"], "summary": "用神为火，喜土，忌金"},
    "daYun": [{"ganZhi": "壬午", "isTongXian": False, "shiShen": "正印"}],
}


def test_build_bazi_knowledge_grounding_injects_sections():
    from technique_kb import build_bazi_knowledge_grounding

    out = build_bazi_knowledge_grounding(SAMPLE_CHART)
    # 各知识库分节齐全
    for section in (
        "日主本性", "五行生克", "十神详解", "格局详解",
        "用神喜忌与调候", "古籍原文印证", "衰旺派方法论", "盲派技法",
    ):
        assert section in out, f"缺少分节：{section}"
    # 日主本性：乙木阴木 → 花草藤萝
    assert "乙木" in out and "花草藤萝" in out
    # 十神：命局出现正官/七杀/正财/比肩/正印
    assert "克我而阴阳异" in out      # 正官 mean
    assert "权威、魄力" in out        # 七杀 mean
    # 格局：正官格 → 清贵守法
    assert "正官格" in out and "清贵" in out
    # 用神摘要
    assert "用神为火" in out
    # 盲派宾主定位：财官落点（年干庚正官→宾、月干辛七杀→宾、时干庚正官→主）
    assert "宾主定位" in out
    # 古籍原文
    assert "滴天髓" in out or "子平真诠" in out


def test_build_bazi_knowledge_grounding_mangpai_pinpoint():
    from technique_kb import build_bazi_knowledge_grounding

    out = build_bazi_knowledge_grounding(SAMPLE_CHART)
    # 盲派宾主定位应指出具体落点：时干庚(正官) 落在「主·门户子女」
    assert "时干庚(正官)·主·门户子女" in out


def test_build_bazi_knowledge_grounding_fault_tolerant():
    from technique_kb import build_bazi_knowledge_grounding

    # 空 dict / 缺字段均不抛错
    assert build_bazi_knowledge_grounding({}) == ""
    assert build_bazi_knowledge_grounding(None) == ""
    # 只有 dayMaster 也能出日主本性 + 五行生克
    out = build_bazi_knowledge_grounding({"dayMaster": {"gan": "甲", "element": "木", "yinYang": "阳"}})
    assert "甲木" in out and "参天大树" in out
    assert "五行生克" in out


def test_query_bazi_grounds_with_technique_kb():
    import asyncio

    import core as core_mod
    from main import MingbuPlugin
    from tests.test_admin_api import FakeContext

    core_mod._CORE = None
    plugin = MingbuPlugin(FakeContext(), {"background_watch_enabled": False})

    class _FakeApi:
        async def bazi(self, birth):
            return {"chart": SAMPLE_CHART}

    plugin.api = _FakeApi()

    async def _save_birth(event, year, month, day, birth_hour, gender):
        return None
    plugin._save_birth = _save_birth

    class _FakeEvent:
        def get_sender_id(self):
            return "u1"
        def get_sender_name(self):
            return "测试"

    text = asyncio.run(plugin.query_bazi(_FakeEvent(), 1990, 5, 20, 8, "male"))
    assert "八字命盘" in text                # 命盘本身
    assert "八字知识库" in text              # 技法 grounding 已附上
    assert "花草藤萝" in text                # 乙木日主本性
    assert "正官格" in text
