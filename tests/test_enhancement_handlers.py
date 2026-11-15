"""增强链路处理器注册回归测试（锁定 P0 修复）。

历史问题
--------
``stars/enhancements.py`` 的 5 个被动消息处理器（图片转述 / 语音转写 /
合并转发 / 撤回感知 / 私聊防抖）原本只挂了 ``@filter.event_message_type``
**条件**装饰器，缺 **入口**装饰器。

按 AstrBot 的分发契约（见官方文档与 astrbot.core.star.register 注册逻辑）：
只有带入口标记的函数才会被登记进 ``star_handlers_registry`` 并被事件分发
挂载；``event_message_type`` 仅追加过滤条件（或在新版中直接充当入口），但
裸用而缺入口时处理器不会被挂载。因此这 5 个方法在生产环境被 **0 触发**——
图片/语音/转发/撤回/防抖整组增强功能处于「代码写了、测试没覆盖、生产不跑」
的失效状态。

跨版本入口差异（已用 ``_message_handler`` 自适应封装）：
  * 旧版 AstrBot：``@filter.on_message()`` 作入口；
  * AstrBot 4.27+：``on_message`` 已被移除，改以
    ``@filter.event_message_type(EventMessageType.ALL)`` 作入口。

本测试桩忠实复刻 4.27.2 语义（**不提供** ``on_message``，``event_message_type``
即为消息入口），因此以下断言走的是「4.27+ 入口路径」——这正是 v4.8.12
在线上崩掉的真实路径。若处理器未能被注册为消息处理器（缺入口），
``__astrbot_event_type__`` 不会是 ``OnMessage``，测试即失败，从而锁死 P0。
"""
import asyncio

from astrbot_plugin_mingbu.main import MingbuPlugin

# 5 个被动消息处理器
ENHANCEMENT_HANDLERS = [
    "_on_friend_message_debounce",
    "_on_image_vision",
    "_on_voice_message",
    "_on_merged_forward",
    "_on_message_recall",
]


def _entry_type(method):
    return getattr(method, "__astrbot_event_type__", None)


def test_enhancement_handlers_registered_as_message_handlers():
    """5 个增强处理器必须带消息入口标记（OnMessage），否则 AstrBot 不会挂载。

    在 4.27+ 桩语义下，该标记由 ``event_message_type`` 入口装饰器打上；
    在旧版则由其 ``on_message`` 入口打上。两种路径都须使处理器被挂载。
    """
    for name in ENHANCEMENT_HANDLERS:
        method = getattr(MingbuPlugin, name)
        assert _entry_type(method) == "OnMessage", (
            f"{name} 未被注册为消息处理器（缺少入口标记）：\n"
            f"  实际 __astrbot_event_type__ = {_entry_type(method)!r}\n"
            "  按 AstrBot 契约，缺入口（on_message 或 event_message_type）的方法"
            "不会被事件分发挂载，将 0 触发。"
        )


def test_image_vision_handler_carries_event_type_filter():
    """图片处理器应同时带 event_message_type 条件过滤（ALL 或具体类型）。"""
    method = getattr(MingbuPlugin, "_on_image_vision")
    filters = getattr(method, "__astrbot_event_filters__", [])
    assert any(kind == "event_message_type" for kind, _ in filters), (
        "_on_image_vision 应带有 event_message_type 条件过滤"
    )


def test_debounce_handler_merges_and_records():
    """防抖合并后应把连续补话写入记忆（不再是空实现）。"""
    # 用 __new__ 绕过 MingbuStar.__init__（无需核心引擎），直接注入依赖
    star = MingbuPlugin.__new__(MingbuPlugin)

    recorded = []

    class _FakeMemory:
        async def add(self, uid, kind, text):
            recorded.append((uid, kind, text))

    star.memory = _FakeMemory()
    star._debounce_buffer = {
        "u1": {
            "texts": ["在吗", "帮我看下这个", "谢谢"],
            "ids": [],
            "event": None,
            "timer": None,
        }
    }

    asyncio.run(star._process_debounced("u1"))

    assert recorded, "防抖合并后未写入任何记忆（疑似空实现）"
    uid, kind, text = recorded[0]
    assert uid == "u1"
    assert kind == "OBSERVATION"
    assert "连续补话合并" in text
    assert "在吗" in text and "帮我看下这个" in text and "谢谢" in text
