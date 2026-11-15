"""util.py -- 跨模块共享小工具
=================================
目前提供后台任务的「异常可观测」守护 spawn()，消除裸 asyncio.create_task
带来的静默失败隐患（详见 架构分析与优化建议.md 第②条）。
"""
from __future__ import annotations

import asyncio
from typing import Coroutine

from astrbot.api import logger

from .metrics import record_failure


def inject_text_part(req, text: str):
    """向 ``req.extra_user_content_parts`` 注入一段文本，兼容 AstrBot 版本差异。

    AstrBot 4.27.4 起，``ProviderRequest.assemble_context()`` 会对该列表逐项调用
    ``part.model_dump_for_context()``——纯字符串会抛
    ``AttributeError: 'str' object has no attribute 'model_dump_for_context'``，
    导致整个 agent run 崩溃（internal.py 捕获后仅打日志，用户收不到回复）。
    历史代码（hooks/* 多处）直接 append 字符串，在旧版 AstrBot 可用、新版必崩。

    本函数优先构造官方 ``TextPart``（``astrbot.api.message_components`` 或
    ``astrbot.core.agent.message``），取不到（测试桩/旧版）时回退为纯字符串——
    回退路径仅用于测试环境与旧版兼容，真实 AstrBot 4.27.4 走 TextPart。

    Args:
        req: AstrBot ``ProviderRequest`` 实例（需有 extra_user_content_parts 列表）。
        text: 要注入的文本内容。

    Returns:
        实际追加的元素（TextPart 或 str），供测试断言。
    """
    if not hasattr(req, "extra_user_content_parts"):
        return None
    parts = req.extra_user_content_parts
    if parts is None:
        parts = []
        req.extra_user_content_parts = parts
    part = _make_text_part(text)
    parts.append(part)
    return part


def _make_text_part(text: str):
    """构造 AstrBot TextPart；不可用时回退带 model_dump_for_context 的兜底对象。

    AstrBot 4.27.4 的 assemble_context() 要求元素有 model_dump_for_context() 方法。
    测试桩没有 TextPart 时,返回一个同形兜底对象,保证「注入的元素在真实 AstrBot
    下不崩、在测试下也能断言」。
    """
    try:
        from astrbot.core.agent.message import TextPart  # 官方路径
        return TextPart(text=text)
    except Exception:  # noqa: BLE001
        pass
    try:
        from astrbot.api.message_components import TextPart  # 兼容路径
        return TextPart(text=text)
    except Exception:  # noqa: BLE001
        pass

    class _TextPartFallback:
        """测试桩/旧版兜底:提供 assemble_context 需要的 model_dump_for_context。"""

        def __init__(self, text):
            self.type = "text"
            self.text = text

        def model_dump_for_context(self):
            return {"type": "text", "text": self.text}

        def __contains__(self, item):
            return item in self.text

        def __str__(self):
            return self.text

        def __repr__(self):
            return f"TextPart(text={self.text!r})"

    return _TextPartFallback(text)


def spawn(coro: Coroutine, name: str = "") -> "asyncio.Task":
    """启动一个后台任务，并为其挂上异常兜底回调。

    裸 ``asyncio.create_task(coro)`` 返回的 Task 若抛异常且从未被 await，
    只会在事件循环回收时以 ``Task exception was never retrieved`` 警告抛出，
    极易被淹没，导致后台任务（情绪重算、人格落盘、定时梦境/成长等）持续失败
    而无人察觉——这正是历史上 P0「记忆写入静默失败」的同源隐患。

    本助手统一把异常转成「日志(含堆栈) + metrics 失败计数」，让后台故障可见、
    可观测，杜绝静默失效复发。

    Args:
        coro: 要执行的协程。
        name: 任务可读名称，用于日志与 metrics 维度（建议见名知意）。

    Returns:
        已创建的 asyncio.Task（仍可被调用方持有/取消）。
    """
    task_name = name or "mingbu_task"
    task = asyncio.create_task(coro, name=task_name)

    def _done(task: "asyncio.Task") -> None:
        try:
            task.result()
        except asyncio.CancelledError:
            # 取消属于正常生命周期，不计入失败
            pass
        except Exception as e:  # noqa: BLE001 - 后台任务需兜住一切异常
            logger.warning(f"[mingbu] 后台任务 {name} 失败: {e}", exc_info=True)
            try:
                record_failure(name or "background_task")
            except Exception:
                pass

    task.add_done_callback(_done)
    return task


def msg_type_is(msg_obj, expected_value):
    """判断消息对象 type 是否为预期值（'GroupMessage' / 'FriendMessage'）。

    AstrBot 的 MessageType 是 Enum（astrbot/core/platform/message_type.py），
    msg_obj.type 返回枚举成员；历史代码用字符串比较恒为 False，导致群聊/私聊
    分流失效、私聊能力（羁绊/角色收集/onboarding）全部跳过。本函数先归一化
    枚举到 value 再比较，同时兼容字符串型 type（测试桩/旧版本）。
    """
    t = getattr(msg_obj, "type", None)
    if t is None:
        return False
    try:
        from astrbot.core.platform.message_type import MessageType
    except Exception:  # noqa: BLE001 - 旧版/测试环境无此模块时退回字符串比较
        MessageType = None
    if MessageType is not None and isinstance(t, MessageType):
        return t.value == expected_value
    return t == expected_value

# 剥离 audio 类分片时保留的消息内容类型（其余一律视为语音载体，剔除）。
# 注意：STT 转写文本（[语音转写] …）在 AstrBot 里是 text 分片，天然被保留，
# 剥离 audio 分片只是去掉冗余的音频载体，不丢任何文字语义。
_AUDIO_PART_TYPES = ("audio", "input_audio", "audio_url", "input_audio_file")


def strip_audio_parts_from_messages(messages) -> list:
    """就地剥离请求体中所有 audio 类内容分片，保留 text 等其它分片。

    背景：历史语音消息被 AstrBot 序列化时，可能以 audio 分片进入 LLM 请求体；
    非全模态主模型（如 SiliconFlow 的 Qwen3-VL 系列网关只放行 text+image_url）
    会因此 400 并每轮落 fallback。STT 转写已把语音内容转成 text（[语音转写] …），
    剥离 audio 分片纯属去冗余，不丢语义。

    规则：
    - 仅处理 OpenAI 风格 ``messages``（list[dict]，content 为 list[dict]）。
    - 每条消息的 content 中，type 属于 ``_AUDIO_PART_TYPES`` 的分片被剔除；
      其余分片（text / image_url / 字符串 / None）原样保留。
    - 若剔除后 content 变为空列表，则替换为该条消息的字符串原文（若有），
      否则置为空字符串——绝不留下空列表让模型拒绝。
    - 非预期结构（非 list、content 非 list）原样返回，避免误伤。

    Args:
        messages: AstrBot ``RequestBody.messages``（OpenAI 风格消息列表）。

    Returns:
        同一个 ``messages`` 列表（已就地修改）；结构异常时原样返回。
    """
    if not isinstance(messages, list):
        return messages
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        kept = [p for p in content if not _is_audio_part(p)]
        if len(kept) == len(content):
            continue
        if kept:
            msg["content"] = kept
        else:
            # 全被剥离：用消息原文兜底，绝不留下空列表
            orig = msg.get("text") or msg.get("content_text") or ""
            msg["content"] = orig if isinstance(orig, str) else ""
    return messages


def _is_audio_part(part) -> bool:
    """判断一个 content 分片是否为 audio 类（应被剥离）。"""
    if not isinstance(part, dict):
        return False
    ptype = part.get("type")
    if ptype in _AUDIO_PART_TYPES:
        return True
    # 部分实现用 input_audio 的 data 字段标记音频
    if ptype == "input_audio":
        return True
    return False


async def cleanup_stale_cron_jobs(cron_manager, name: str, keep_job_id: str) -> int:
    """清理历史启动遗留的同名旧 basic cron job，返回删除数量。

    背景（v4.9.0 排查发现）：AstrBot basic cron job 的 handler 是内存函数
    （lambda/closure），无法随 persistent job 持久化——每次插件重启都必须
    重新 add_basic_job；而 db 层 create_cron_job 是纯 INSERT、不按 name
    去重。不清理的话，同名旧 job 随每次重启无限积累（sync_from_db 因缺
    handler 会跳过调度，不至于重复执行，但死记录永远躺在库里）。

    Args:
        cron_manager: AstrBot ``CronJobManager``（context.cron_manager）。
        name: 插件 job 的 name（如 "mingbu_proactive"）。
        keep_job_id: 本次刚注册成功的 job_id（保留它，删其余同名）。

    Returns:
        实际删除的旧 job 数量；清理过程任何异常只告警不抛出（不影响主流程）。
    """
    removed = 0
    try:
        for j in await cron_manager.list_jobs(job_type="basic"):
            if getattr(j, "name", "") == name and getattr(j, "job_id", "") != keep_job_id:
                await cron_manager.delete_job(j.job_id)
                removed += 1
        if removed:
            logger.info(f"[mingbu] 已清理同名旧 cron job ×{removed}（{name}）")
    except Exception as e:  # noqa: BLE001 - 清理失败只记录，不影响主流程
        logger.warning(f"[mingbu] 清理同名旧 cron job 失败（{name}）: {e}")
    return removed
