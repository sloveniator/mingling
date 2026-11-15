"""
tests/test_disguise_guard.py -- 命灵·伪装护栏回归测试
=====================================================
命灵的铁律：暗盘洞察**绝不**以「命盘/八字/紫微/卦象」等术语现形，也绝不
用客服口吻说话。这条铁律由 :mod:`disguise_guard` 的纯函数守住。

原有 4 个用例只抽查了「八字/紫微/请问还有什么可以帮您」几个样本，
词表里另外几十项其实无人看管——有人手滑把 `LEAK_TERMS` 的某项写错
（比如漏个字、或误加进 `CUSTOMER_TONE_PATTERNS`），测试照样全绿。
这里补三类护栏：

1. **全词表逐项生效**：每个术语/客服腔样式都必须真的被检出；
2. **正常陪伴口吻不误杀**：护栏太严会把普通回复也判成破功；
3. **契约不变**：返回结构、``safe`` 的真布尔类型、掩码长度与幂等性。
"""
import pytest

from disguise_guard import (
    CUSTOMER_TONE_PATTERNS,
    LEAK_TERMS,
    check_disguise_leak,
    strip_leak_terms,
)

# 铁律核心词：这几项被从词表里删掉就等于护栏失效，必须钉死
CORE_TABOO = ("命盘", "八字", "紫微", "卦象", "六爻", "塔罗", "占卜")

# 正常的陪伴式回复语料——刻意不含任何术语与客服腔。
# 注意「流年/大运」这类词日常也可能出现，选语料时已规避，
# 因为护栏宁可误杀也不能漏杀，这里只验证明确安全的口吻。
FRIENDLY_SAMPLES = [
    "你最近好像有点累，早点休息。",
    "听起来这段时间确实不好过，我在呢。",
    "那家面馆我也去过，汤头是真的顶。",
    "别急，慢慢说，我听着。",
    "今天天气不错，出去走走会舒服些。",
    "我猜你心里其实已经有答案了，只是想找个人确认一下。",
]


class TestBasicBehaviour:
    def test_no_leak_is_safe(self):
        r = check_disguise_leak("你最近好像有点累，早点休息。")
        assert r["safe"] is True
        assert r["leak_terms"] == []
        assert r["customer_tone"] is False

    def test_detect_term_leak(self):
        r = check_disguise_leak("我看了你的八字和紫微斗数排盘")
        assert r["safe"] is False
        assert "八字" in r["leak_terms"]
        assert "紫微" in r["leak_terms"]

    def test_detect_customer_tone(self):
        r = check_disguise_leak("请问还有什么可以帮您呢？")
        assert r["customer_tone"] is True
        assert r["safe"] is False

    def test_strip_leak_terms(self):
        out = strip_leak_terms("你的紫微命盘显示大运流年")
        assert "紫微" not in out
        assert "命盘" not in out
        assert "大运" not in out

    def test_empty_text_is_safe(self):
        """空文本不能崩，也不该被判成破功。"""
        for empty in ("", None):
            r = check_disguise_leak(empty)
            assert r["safe"] is True
            assert r["leak_terms"] == []
            assert r["customer_tone"] is False


class TestVocabularyIsFullyWired:
    """词表逐项护栏：任何一项失效都要在这里炸出来。"""

    @pytest.mark.parametrize("term", LEAK_TERMS)
    def test_every_leak_term_is_detected(self, term):
        r = check_disguise_leak(f"我刚才看了下你的{term}，感觉挺有意思")
        assert r["safe"] is False, f"术语「{term}」在词表里却检不出来"
        assert term in r["leak_terms"]

    @pytest.mark.parametrize("pattern", CUSTOMER_TONE_PATTERNS)
    def test_every_customer_tone_pattern_is_detected(self, pattern):
        r = check_disguise_leak(f"好的呀。{pattern}")
        assert r["customer_tone"] is True, f"客服腔「{pattern}」在词表里却检不出来"
        assert r["safe"] is False

    @pytest.mark.parametrize("term", CORE_TABOO)
    def test_core_taboo_terms_stay_in_vocabulary(self, term):
        """铁律核心词不允许被从词表里移除。"""
        assert term in LEAK_TERMS, (
            f"「{term}」是命灵铁律的核心禁词，不得从 LEAK_TERMS 中删除"
        )

    def test_vocabularies_do_not_overlap(self):
        """术语表与客服腔表混用会让 leak_terms 的语义变浑。"""
        assert not (set(LEAK_TERMS) & set(CUSTOMER_TONE_PATTERNS))

    def test_no_empty_entries(self):
        """空串会让 `in` 判定恒真，等于把所有文本判成破功。"""
        assert all(t.strip() for t in LEAK_TERMS)
        assert all(p.strip() for p in CUSTOMER_TONE_PATTERNS)


class TestNoFalsePositives:
    """护栏不能把正常的陪伴口吻误判成破功。"""

    @pytest.mark.parametrize("text", FRIENDLY_SAMPLES)
    def test_friendly_reply_passes(self, text):
        r = check_disguise_leak(text)
        assert r["safe"] is True, (
            f"正常回复被误杀：{text} → {r['leak_terms']} / "
            f"customer_tone={r['customer_tone']}"
        )

    @pytest.mark.parametrize("text", FRIENDLY_SAMPLES)
    def test_strip_leaves_friendly_reply_untouched(self, text):
        assert strip_leak_terms(text) == text


class TestContract:
    """返回结构与掩码行为的契约。"""

    def test_result_keys_and_types(self):
        r = check_disguise_leak("你的八字很有意思")
        assert set(r) == {"safe", "leak_terms", "customer_tone"}
        # 必须是真 bool：面板/JSON 消费方与 `is False` 断言都依赖这一点
        assert isinstance(r["safe"], bool)
        assert isinstance(r["customer_tone"], bool)
        assert isinstance(r["leak_terms"], list)

    def test_overlapping_terms_all_reported(self):
        """「紫微斗数」同时命中紫微/斗数/紫微斗数，不能只报一个。"""
        r = check_disguise_leak("紫微斗数")
        for expected in ("紫微", "斗数", "紫微斗数"):
            assert expected in r["leak_terms"]

    def test_strip_preserves_length(self):
        """掩码逐字替换，长度不变——保住语句结构不被打乱。"""
        text = "你的紫微命盘显示大运流年"
        assert len(strip_leak_terms(text)) == len(text)

    def test_strip_output_is_clean(self):
        """掩码后的文本必须能通过术语检查。"""
        cleaned = strip_leak_terms("八字、紫微、卦象、六爻、塔罗都看过了")
        assert check_disguise_leak(cleaned)["leak_terms"] == []

    def test_strip_is_idempotent(self):
        text = "你的紫微命盘显示大运流年"
        once = strip_leak_terms(text)
        assert strip_leak_terms(once) == once

    def test_custom_mask(self):
        out = strip_leak_terms("你的八字", mask="*")
        assert "八字" not in out
        assert "*" in out

    def test_strip_does_not_touch_customer_tone(self):
        """strip 只处理术语；客服腔属于语气问题，改写不是它的职责。"""
        text = "请问还有什么可以帮您"
        assert strip_leak_terms(text) == text


# ---- v4.8.104 规约：注入文本不得使用「指令式方括号标签」（防 LLM 照抄出戏） ----

def test_weave_insights_no_bracket_instruction():
    """weave_insights_into_prompt 输出不得含 [标签] 或「使用规则」式指令清单。"""
    from silent_divination import weave_insights_into_prompt

    out = weave_insights_into_prompt("你隐约觉得对方最近状态在好转")
    assert "[说不清的直觉" not in out, "不得再出现指令式方括号标签"
    assert "使用规则" not in out, "不得再出现『使用规则』式指令清单"
    assert "绝" not in out or "绝不能" not in out, "不得再出现祈使式禁令措辞"
    assert "直觉" in out and "说不清" in out, "自然认知式描述应保留"


def test_weave_watch_note_no_bracket_instruction():
    """weave_watch_note 输出不得含 [后台留意] 标签或祈使句清单。"""
    from background_watch import weave_watch_note

    out = weave_watch_note("对方感情线有微妙变化")
    assert "[后台留意" not in out, "不得再出现指令式方括号标签"
    assert "可以借用的方向" not in out, "不得再出现『可以借用的方向』式清单"
    assert "绝" not in out or "绝不能" not in out, "不得再出现祈使式禁令措辞"
    assert "隐约" in out, "自然认知式描述应保留"


def test_persona_block_contains_anti_instruction_constraint():
    """人格块必须包含『不原样复述内部说明』的隐形约束。"""
    # 直接验证注入的约束文案已写入源码（行为由集成测试覆盖）
    import inspect

    from star_mixins.persona_ctx import PersonaContextMixin
    src = inspect.getsource(PersonaContextMixin)
    assert "【说话的底线】" in src, "必须存在『说话的底线』隐形约束块"
    assert "绝不原样复述" in src or "不会把它们原样说出口" in src, \
        "隐形约束必须包含『不原样复述内部说明』语义"
