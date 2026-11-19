from __future__ import annotations

import time

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.provider import ProviderRequest
from astrbot.api.star import Context, Star

from admin_timeline import AdminTimelineAPI
from config import CompanionConfig
from emotion_trace import EmotionTrace, detect_mood
from open_loops import OpenLoopManager
from private_gate import companion_scope, is_private
from proactive_v2 import ProactiveEngine
from relation_memory import RelationMemory
from relationship import RelationshipManager
from state_layer import StateLayerBuilder
from store import Store
from tool_visibility import ToolVisibilityContext, apply_tool_visibility
from topic_stack import TopicStack


class MinglingPlugin(Star):
    """命灵 v5：1V1 状态层插件。"""

    def __init__(self, context: Context, config: dict | None = None):
        super().__init__(context)
        self._cfg = CompanionConfig.from_raw(config or {})
        self._store = Store(self)
        self._emotion = EmotionTrace(self._store)
        self._relationship = RelationshipManager(self._store)
        self._topics = TopicStack(self._store)
        self._loops = OpenLoopManager(self._store, window_days=self._cfg.open_loop_window_days)
        self._memory = RelationMemory(self._store, self._cfg)
        self._state_layer = StateLayerBuilder(
            self._cfg, self._emotion, self._relationship,
            self._topics, self._loops, self._memory,
        )
        self._proactive = ProactiveEngine(
            self.context, self._store, self._cfg,
            self._emotion, self._relationship,
            self._loops, self._topics, self._memory,
        )
        self._admin = AdminTimelineAPI(self._store, self._cfg)

        # 同一消息可能触发多次 on_llm_request，用 (umo, text[:50], 分钟) 去重
        self._last_observe_key: tuple | None = None

    # ---------- 生命周期 ----------
    async def initialize(self) -> None:
        if not self._cfg.enabled:
            logger.info("[mingling] private_companion_mode 关闭，插件不注册任何钩子")
            return
        self._register_cron()
        self._admin.register(self.context)
        logger.info("[mingling] 1V1 状态层已启用")

    async def terminate(self) -> None:
        logger.info("[mingling] terminated")

    def _register_cron(self) -> None:
        try:
            manager = self.context.cron_manager
        except AttributeError:
            logger.warning("[mingling] cron_manager 不可用，主动消息将跳过")
            return

        async def _tick_all() -> None:
            if not self._cfg.proactive_enabled:
                return
            for umo in await self._store.list_known_umos():
                try:
                    await self._proactive.tick(umo)
                except Exception:
                    import traceback
                    logger.error(f"[mingling] proactive tick failed: {umo}\n{traceback.format_exc()}")

        try:
            manager.add_basic_job(
                name="mingling_proactive_tick",
                cron="0 * * * *",
                handler=_tick_all,
                description="命灵主动消息检查",
            )
        except TypeError:
            try:
                manager.add_basic_job("mingling_proactive_tick", "0 * * * *", _tick_all)
            except Exception:
                import traceback
                logger.error(f"[mingling] 注册 cron 失败\n{traceback.format_exc()}")

    # ---------- 核心：观察 + StateLayer 注入（唯一 LLM 钩子） ----------
    @filter.on_llm_request()
    async def inject_state_layer(self, event: AstrMessageEvent, req: ProviderRequest):
        if not companion_scope(self._cfg, event):
            return

        umo = event.unified_msg_origin
        user_text = (event.message_str or "").strip()

        # 1. 观察用户消息（去重）
        dedup_key = (umo, user_text[:50], int(time.time() // 60))
        if dedup_key != self._last_observe_key:
            self._last_observe_key = dedup_key
            await self._observe_user_message(umo, user_text)

        # 2. 工具可见性
        has_bound_chart = bool(await self._store.load(umo, "chart_bound", False))
        counseling_active = bool(await self._store.load(umo, "counseling_active", False))
        apply_tool_visibility(
            req,
            ToolVisibilityContext(
                is_private=True,
                has_bound_chart=has_bound_chart,
                counseling_active=counseling_active,
            ),
            self._cfg,
        )

        # 3. 构建并追加 StateLayer
        body = await self._state_layer.build(umo, user_text)
        wrapped = self._state_layer.wrap(body)
        if not wrapped:
            return
        # 关键：+= 追加，不触碰 req.contexts
        req.system_prompt = (req.system_prompt or "") + wrapped

        await self._store.append_timeline(umo, "state_layer", {
            "tokens": len(body), "body": body,
        })

    async def _observe_user_message(self, umo: str, text: str) -> None:
        now = time.time()
        await self._store.remember_umo(umo)
        await self._store.save(umo, "last_user_msg_at", now)
        await self._proactive.record_active_hour(umo, now)
        await self._proactive.mark_user_replied(umo, now)

        if not text:
            return

        mood = detect_mood(text)
        if mood:
            await self._store.save(umo, "last_mood", mood)
            await self._store.save(umo, "last_mood_at", now)

        if self._cfg.topic_stack_enabled:
            topic = await self._topics.observe(umo, text)
            if self._cfg.open_loop_enabled and topic:
                await self._loops.register(umo, topic, emotion=mood or "neutral")

        if self._cfg.open_loop_enabled:
            await self._loops.observe_resolution(umo, text)

    # ---------- 手动命令：调试 ----------
    @filter.command("命灵状态")
    async def cmd_status(self, event: AstrMessageEvent):
        if not companion_scope(self._cfg, event):
            return
        if not is_private(event):
            return
        umo = event.unified_msg_origin
        rel = await self._relationship.get(umo)
        emo = await self._emotion.load(umo)
        topic = await self._topics.current(umo)
        loops = await self._loops.list_open(umo)
        text = (
            f"关系阶段：{rel.stage}（亲密度 {rel.intimacy:.2f}）\n"
            f"情绪：愉悦 {emo.pleasantness:.2f} / 紧张 {emo.tension:.2f} "
            f"/ 唤醒 {emo.arousal:.2f} / 确定 {emo.certainty:.2f}\n"
            f"当前话题：{topic or '无'}\n"
            f"未闭环话题：{len(loops)} 个"
        )
        yield event.plain_result(text)
