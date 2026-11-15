"""企微长文本字节分段回归测试（v4.8.116）

背景：企业微信客服消息 content 上限 2048 字节（UTF-8），AstrBot 核心 wecom
split_plain 按 2048 字符切——中文每字 3 字节，2048 字符 ≈ 6144 字节，每段
都超限被微信拒收。插件侧兜底：按 UTF-8 字节切段（每段 ≤1800B），每段字符数
必然 < 2048，AstrBot 不再二次切分，整段发送不超限。
"""
import pytest

from main import _split_utf8_bytes


def test_short_text_no_split():
    """短文本（< 1800B）不分段"""
    text = "收到，重新排八字盘"
    r = _split_utf8_bytes(text, 1800)
    assert len(r) == 1
    assert r[0] == text


def test_long_cn_chunks_under_limit_and_lossless():
    """超长中文分段：每段 ≤1800B、无损拼接、不切断多字节字符"""
    text = "。".join(
        [f"这是第{i}段很长的中文内容测试句子，包含标点符号。" for i in range(200)]
    )
    r = _split_utf8_bytes(text, 1800)
    assert len(r) > 1
    for chunk in r:
        assert len(chunk.encode("utf-8")) <= 1800
        chunk.encode("utf-8").decode("utf-8")  # 不切断 UTF-8 序列
    assert "".join(r) == text


def test_wecom_scenario_markdown_lossless():
    """用户实际场景（八字+六爻交叉验证 Markdown）分段无损"""
    text = (
        "收到，再重新排八字盘，结合六爻结果做交叉验证。\n\n"
        "### 再次八字盘【丙火日主】\n四柱：癸酉 己未 丙申 甲午\n"
        "五行：木1 火2 金3 水1 土1\n\n"
        "### 2026年下半年运势：\n| 月份 | 分析 | 结论 |\n"
        "| 8月-9月 | 印星当令 | 适合积累 |\n| 10月-11月 | 官星旺相 | 薪资提升 |\n\n"
        "### 综合结论：运势等级：★★★★☆（上升期，但需稳）\n"
        "建议：转向管理岗；不宜投资高风险项目；避免私下接触。\n"
        "需要？可补充更细化的选择建议，比如跳槽方向或财务配置。"
    ) * 8
    assert len(text.encode("utf-8")) > 1800
    r = _split_utf8_bytes(text, 1800)
    assert len(r) > 1
    for chunk in r:
        assert len(chunk.encode("utf-8")) <= 1800
    assert "".join(r) == text


def test_no_punct_hard_split_no_infinite_loop():
    """无标点超长文本硬切：不超限、不丢字、不死循环"""
    text = "无标点" * 3000
    r = _split_utf8_bytes(text, 1800)
    assert all(len(c.encode("utf-8")) <= 1800 for c in r)
    assert "".join(r) == text


def test_exact_boundary_no_split():
    """恰好 1800 字节不分段"""
    text = "字" * 600
    r = _split_utf8_bytes(text, 1800)
    assert len(r) == 1
    assert len(r[0].encode("utf-8")) == 1800


def test_emoji_safe_split():
    """emoji（4 字节字符）安全切分，不切断"""
    text = "😀" * 500 + "中文内容" * 100
    r = _split_utf8_bytes(text, 1800)
    assert all(len(c.encode("utf-8")) <= 1800 for c in r)
    assert "".join(r) == text


def test_each_chunk_below_2048_chars_for_astrbot_no_resplit():
    """核心保证：每段字节 ≤1800 → 字符数必然 < 2048，AstrBot split_plain 不再二次切分"""
    text = "。".join(
        [f"这是第{i}段很长的中文内容测试句子，包含标点符号。" for i in range(200)]
    )
    r = _split_utf8_bytes(text, 1800)
    for chunk in r:
        assert len(chunk) < 2048  # 字符数 < 2048 → 不触发 AstrBot 按字符切
        assert len(chunk.encode("utf-8")) <= 2048  # 字节数 ≤ 企业微信上限
