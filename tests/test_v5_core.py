"""v5 核心逻辑单元测试（覆盖 Part 6 验收项）"""
import time

import pytest

# 测试桩由 tests/conftest.py 统一注入，这里不再硬编码开发者本机路径。
from config import CompanionConfig
from emotion_trace import EmotionTrace, detect_mood
from open_loops import OpenLoopManager
from private_gate import companion_scope, is_private
from relation_memory import RelationMemory
from relationship import RelationshipManager
from state_layer import StateLayerBuilder, estimate_tokens
from store import Store
from tool_visibility import ToolVisibilityContext, apply_tool_visibility
from topic_stack import TopicStack


# ========== 私聊门控（V4/V5 相关） ==========
def test_private_gate_friend_message():
    """F13前置：friend_message 应判定为私聊"""
    from astrbot.api.event import AstrMessageEvent
    event = AstrMessageEvent(message_str="hi", unified_msg_origin="u1", msg_type="friend_message")
    assert is_private(event) is True

def test_private_gate_group_message():
    """V4: 群聊消息应判定为非私聊"""
    from astrbot.api.event import AstrMessageEvent
    event = AstrMessageEvent(message_str="hi", unified_msg_origin="g1", msg_type="group_message")
    assert is_private(event) is False

def test_companion_scope_disabled():
    """V5: enabled=False 时 companion_scope 恒为 False"""
    from astrbot.api.event import AstrMessageEvent
    cfg = CompanionConfig(enabled=False)
    event = AstrMessageEvent(message_str="hi", unified_msg_origin="u1", msg_type="friend_message")
    assert companion_scope(cfg, event) is False

def test_companion_scope_enabled_private():
    """enabled=True + 私聊 = True"""
    from astrbot.api.event import AstrMessageEvent
    cfg = CompanionConfig(enabled=True)
    event = AstrMessageEvent(message_str="hi", unified_msg_origin="u1", msg_type="friend_message")
    assert companion_scope(cfg, event) is True


# ========== 情绪检测（F6） ==========
def test_detect_mood_sad():
    """F6: 情绪词'难过'应识别为 sad"""
    assert detect_mood("我今天很难过") == "sad"

def test_detect_mood_none():
    """无情绪词返回 None"""
    assert detect_mood("今天天气不错") is None


# ========== 关系阶段（F5/F7） ==========
@pytest.mark.asyncio
async def test_relationship_promote_warming():
    """F5: 亲密度突破 0.45 应升到 warming"""
    store = Store(None)
    mgr = RelationshipManager(store)
    umo = "test_u1"
    await mgr.nudge(umo, 0.5, "测试")
    rel = await mgr.get(umo)
    assert rel.stage == "warming"

@pytest.mark.asyncio
async def test_relationship_mark_hurt():
    """F7: mark_hurt 后 stage 应为 hurt"""
    store = Store(None)
    mgr = RelationshipManager(store)
    umo = "test_u2"
    await mgr.mark_hurt(umo, "测试受损")
    rel = await mgr.get(umo)
    assert rel.stage == "hurt"

@pytest.mark.asyncio
async def test_relationship_hurt_repair():
    """F7: hurt 状态 3 次正向后应恢复 warming"""
    store = Store(None)
    mgr = RelationshipManager(store)
    umo = "test_u3"
    await mgr.mark_hurt(umo, "测试")
    # 3 次正向 nudge
    for _ in range(3):
        result = await mgr.nudge(umo, 0.1, "修复")
    assert result["state"].stage == "warming"
    assert result["event"] is not None
    assert result["event"]["title"] == "关系修复"


# ========== 话题栈（F3/F4） ==========
@pytest.mark.asyncio
async def test_topic_stack_new_topic():
    """F3: 新话题入栈"""
    store = Store(None)
    ts = TopicStack(store)
    umo = "test_u4"
    topic = await ts.observe(umo, "今天面试很紧张")
    assert topic is not None
    assert "面试" in topic or "紧张" in topic

@pytest.mark.asyncio
async def test_topic_stack_resume():
    """F4: 聊新话题后回旧话题应触发 topic_resume"""
    store = Store(None)
    ts = TopicStack(store)
    umo = "test_u5"
    # 第一轮：聊面试
    topic1 = await ts.observe(umo, "今天面试很紧张")
    # 第二轮：聊天气
    topic2 = await ts.observe(umo, "今天天气不错")
    assert topic2 != topic1
    # 第三轮：回到面试（用完整旧话题子串"面试很紧张"确保命中）
    topic3 = await ts.observe(umo, "面试很紧张的事我想再说说")
    assert topic3 == topic1  # 应恢复旧话题


# ========== 未闭环话题（F1/F2） ==========
@pytest.mark.asyncio
async def test_open_loop_register():
    """F1: 注册未闭环话题"""
    store = Store(None)
    mgr = OpenLoopManager(store, window_days=7)
    umo = "test_u6"
    await mgr.register(umo, "面试", emotion="anxious")
    loops = await mgr.list_open(umo)
    assert len(loops) == 1
    assert loops[0].topic == "面试"

@pytest.mark.asyncio
async def test_open_loop_resolve():
    """F2: 闭环信号应标记 resolved"""
    store = Store(None)
    mgr = OpenLoopManager(store, window_days=7)
    umo = "test_u7"
    await mgr.register(umo, "面试", emotion="anxious")
    resolved = await mgr.observe_resolution(umo, "面试通过了！")
    assert len(resolved) == 1
    assert "面试" in resolved[0]
    loops = await mgr.list_open(umo)
    assert len(loops) == 0  # 已闭环的不在 list_open


# ========== 关系记忆（anchors_for） ==========
@pytest.mark.asyncio
async def test_relation_memory_anchors():
    """记忆锚点召回"""
    store = Store(None)
    cfg = CompanionConfig()
    mgr = RelationMemory(store, cfg)
    umo = "test_u8"
    await mgr.remember(umo, "面试", "用户提到面试很紧张", mtype="episodic", emotion_peak=0.8)
    anchors = await mgr.anchors_for(umo, "面试", limit=2)
    assert len(anchors) == 1
    assert anchors[0]["topic"] == "面试"
    assert anchors[0]["anchor"] is True  # 情绪峰值 0.8 > 0.7 阈值


# ========== StateLayer 构建（V2/G2/F14） ==========
@pytest.mark.asyncio
async def test_state_layer_budget():
    """G2/F14: StateLayer token 数 ≤ 220"""
    store = Store(None)
    cfg = CompanionConfig(state_layer_max_tokens=220)
    emotion = EmotionTrace(store)
    relationship = RelationshipManager(store)
    topics = TopicStack(store)
    loops = OpenLoopManager(store, window_days=7)
    memory = RelationMemory(store, cfg)
    builder = StateLayerBuilder(cfg, emotion, relationship, topics, loops, memory)

    umo = "test_u9"
    # 注入多个话题 + 记忆
    await topics.observe(umo, "今天面试很紧张，和妈妈吵架了")
    await memory.remember(umo, "面试", "用户面试紧张", emotion_peak=0.8)
    await memory.remember(umo, "妈妈", "和妈妈吵架", emotion_peak=0.9)
    await loops.register(umo, "面试", emotion="anxious")

    body = await builder.build(umo, "今天面试结果出来了")
    tokens = estimate_tokens(body)
    assert tokens <= 220, f"StateLayer 超预算: {tokens} tokens"

@pytest.mark.asyncio
async def test_state_layer_wrap():
    """StateLayer 边界标记"""
    store = Store(None)
    cfg = CompanionConfig()
    builder = StateLayerBuilder(cfg, EmotionTrace(store), RelationshipManager(store),
                                 TopicStack(store), OpenLoopManager(store),
                                 RelationMemory(store, cfg))
    wrapped = builder.wrap("测试内容")
    assert wrapped.startswith("\n<mingling_state>\n")
    assert wrapped.endswith("\n</mingling_state>\n")


# ========== 工具可见性（F11/F12/F13） ==========
def test_tool_visibility_private_with_chart():
    """F11: 私聊 + 已绑盘 = 命理工具激活"""
    class MockTool:
        def __init__(self, name):
            self.name = name
            self.active = False

    class MockFuncTool:
        tools = [MockTool("query_bazi"), MockTool("psychological_counsel")]

    class MockReq:
        func_tool = MockFuncTool()

    cfg = CompanionConfig(divination_tools_enabled=True, counseling_tools_enabled=True)
    ctx = ToolVisibilityContext(is_private=True, has_bound_chart=True, counseling_active=True)
    apply_tool_visibility(MockReq(), ctx, cfg)

    assert MockReq().func_tool.tools[0].active is True  # query_bazi
    assert MockReq().func_tool.tools[1].active is True  # psychological_counsel

def test_tool_visibility_private_no_chart():
    """F12: 私聊 + 未绑盘 = 命理工具不激活"""
    class MockTool:
        def __init__(self, name):
            self.name = name
            self.active = True

    class MockFuncTool:
        tools = [MockTool("query_bazi")]

    class MockReq:
        func_tool = MockFuncTool()

    cfg = CompanionConfig()
    ctx = ToolVisibilityContext(is_private=True, has_bound_chart=False, counseling_active=False)
    apply_tool_visibility(MockReq(), ctx, cfg)

    assert MockReq().func_tool.tools[0].active is False  # query_bazi

def test_tool_visibility_group_all_off():
    """F13: 群聊 = 所有命灵工具不激活"""
    class MockTool:
        def __init__(self, name):
            self.name = name
            self.active = True

    class MockFuncTool:
        tools = [MockTool("query_bazi"), MockTool("psychological_counsel"), MockTool("query_situation")]

    class MockReq:
        func_tool = MockFuncTool()

    cfg = CompanionConfig()
    ctx = ToolVisibilityContext(is_private=False, has_bound_chart=True, counseling_active=True)
    apply_tool_visibility(MockReq(), ctx, cfg)

    tools = MockReq().func_tool.tools
    assert tools[0].active is False  # query_bazi
    assert tools[1].active is False  # psychological_counsel
    assert tools[2].active is False  # query_situation (ALWAYS_ON 但群聊也应关闭)


# ========== Token 估算 ==========
def test_estimate_tokens_chinese():
    """中文 token 估算"""
    text = "今天面试很紧张，和妈妈吵架了"
    tokens = estimate_tokens(text)
    assert tokens > 0
    assert tokens < 50  # 合理范围

def test_estimate_tokens_english():
    """英文 token 估算"""
    text = "Hello world, this is a test"
    tokens = estimate_tokens(text)
    assert tokens > 0
    assert tokens < 20
