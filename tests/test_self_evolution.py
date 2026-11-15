"""命灵·轻量自我进化单测（每晚事实提炼）。"""
from self_evolution import build_reflection_prompt, parse_facts


def test_build_reflection_prompt():
    p = build_reflection_prompt("用户：我周末去爬山")
    assert "周末去爬山" in p
    assert "JSON" in p


def test_parse_facts_bare():
    assert parse_facts('["a", "b"]') == ["a", "b"]


def test_parse_facts_fenced():
    assert parse_facts('```json\n["x"]\n```') == ["x"]


def test_parse_facts_with_junk():
    assert parse_facts('好的：["事实1", "事实2"] 完毕') == ["事实1", "事实2"]


def test_parse_facts_invalid():
    assert parse_facts("不是 json") == []
    assert parse_facts("") == []
