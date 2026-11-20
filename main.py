from __future__ import annotations

import inspect
import os
import sys
import time
from typing import Any

# AstrBot 以包形式加载本文件（data.plugins.mingling.main），而下列业务模块
# 使用短名导入。把插件目录加入 sys.path，保证包加载与 devtools 直接加载
# 两种方式都能解析到同一份模块。
_PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))
if _PLUGIN_DIR not in sys.path:
    sys.path.insert(0, _PLUGIN_DIR)

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.provider import ProviderRequest
from astrbot.api.star import Context, Star

from admin_timeline import AdminTimelineAPI
from command_support import (
    build_chart_prompt,
    build_growth_prompt,
    build_gua_prompt,
    build_health_prompt,
    clock_to_shichen,
    format_birth,
    parse_birth,
)
from config import CompanionConfig
from emotion_trace import EmotionTrace, detect_mood
from intent_router import (
    detect_task,
    is_distress,
    normalize,
    should_exit_counseling,
    should_start_counseling,
)
from llm_default import ask_default
from open_loops import OpenLoopManager
from private_gate import companion_scope, is_private
from proactive_v2 import ProactiveEngine
from relation_memory import RelationMemory
from relationship import RelationshipManager
from semantic_router import TASK_INTENTS, should_classify
from semantic_router import classify as classify_intent
from state_layer import StateLayerBuilder
from store import Store
from tool_visibility import ToolVisibilityContext, apply_tool_visibility
from topic_stack import TopicStack


class MinglingPlugin(Star):
    """命灵 v5：1V1 状态层插件。"""

    def __init__(self, context: Context, config: dict | None = None):
        super().__init__(context)
        # 显式持有 AstrBot 配置对象：管理面板保存时调用其 save_config()。
        self.config = config if config is not None else {}
        self._raw_config = dict(config or {})
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
        self._admin = AdminTimelineAPI(self._store, self._cfg, self)
        self._client = None

        # 同一消息可能触发多次 on_llm_request，用 (umo, text[:50], 分钟) 去重
        self._last_observe_key: tuple | None = None

    # ---------- 生命周期 ----------
    async def initialize(self) -> None:
        if not self._cfg.enabled:
            logger.info("[mingling] enabled=False，插件不注册任何钩子")
            return
        await self._register_cron()
        self._admin.register(self.context)
        logger.info("[mingling] 1V1 状态层已启用")

    async def terminate(self) -> None:
        if self._client is not None:
            try:
                await self._client.close()
            except Exception:
                pass
            self._client = None
        logger.info("[mingling] terminated")

    async def _delete_existing_jobs(self, manager, name: str) -> None:
        """删除同名旧任务，避免每次重启重复插入非持久化 cron。"""
        list_jobs = getattr(manager, "list_jobs", None)
        delete_job = getattr(manager, "delete_job", None)
        if not callable(list_jobs) or not callable(delete_job):
            return
        jobs = list_jobs()
        if inspect.isawaitable(jobs):
            jobs = await jobs
        for job in jobs or []:
            if getattr(job, "name", "") != name:
                continue
            result = delete_job(getattr(job, "job_id", ""))
            if inspect.isawaitable(result):
                await result

    async def _add_cron_job(self, manager, *, name: str, expression: str,
                            handler, description: str) -> None:
        """兼容 AstrBot 不同版本的 cron 注册签名（cron/cron_expression，同步/异步）。"""
        await self._delete_existing_jobs(manager, name)
        params = inspect.signature(manager.add_basic_job).parameters
        kwargs = {"name": name, "handler": handler, "description": description}
        if "cron_expression" in params:
            kwargs["cron_expression"] = expression
        elif "cron" in params:
            kwargs["cron"] = expression
        else:
            raise TypeError("add_basic_job 缺少 cron/cron_expression 参数")
        if "timezone" in params:
            kwargs["timezone"] = "Asia/Shanghai"
        result = manager.add_basic_job(**kwargs)
        if inspect.isawaitable(result):
            await result

    async def _register_cron(self) -> None:
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
            await self._add_cron_job(
                manager,
                name="mingling_proactive_tick",
                expression="0 * * * *",
                handler=_tick_all,
                description="命灵主动消息检查",
            )
        except Exception:
            import traceback
            logger.error(f"[mingling] 注册 cron 失败\n{traceback.format_exc()}")

        async def _growth_all() -> None:
            if not self._cfg.growth_engine_enabled:
                return
            for umo in await self._store.list_known_umos():
                try:
                    await self._build_growth_note(umo)
                except Exception:
                    import traceback
                    logger.error(
                        f"[mingling] growth reflect failed: {umo}\n{traceback.format_exc()}"
                    )

        try:
            await self._add_cron_job(
                manager,
                name="mingling_growth_weekly",
                expression="0 3 * * 1",
                handler=_growth_all,
                description="命灵每周成长反思",
            )
        except Exception:
            import traceback
            logger.error(f"[mingling] 注册成长 cron 失败\n{traceback.format_exc()}")

    # ---------- 核心：观察 + StateLayer 注入（唯一 LLM 钩子） ----------
    @filter.on_llm_request()
    async def inject_state_layer(self, event: AstrMessageEvent, req: ProviderRequest):
        if not companion_scope(self._cfg, event):
            return

        umo = event.unified_msg_origin
        user_text = (event.message_str or "").strip()

        # 1. 观察用户消息（去重）
        await self._observe_once(umo, user_text)

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
        growth = await self._store.load(umo, "growth_note", {})
        if isinstance(growth, dict) and growth.get("text"):
            body = (body + "\n" + (
                "你最近的成长觉察：" + str(growth["text"])[:220]
            )).strip()

        sess = await self._store.load(umo, "counseling_session", {})
        if isinstance(sess, dict) and sess.get("active"):
            topic = str(sess.get("topic") or "当下的困扰")
            body = (body + "\n" + (
                f"用户正在进行「开解」会话，主题：{topic}。请以朋友式陪伴："
                "先共情、多提问、少下结论；不评判、不诊断、不贴标签；"
                "如出现自伤/轻生信号，优先给安全支持并建议联系专业援助。"
            )).strip()
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
        positive = any(k in text for k in ("开心", "喜欢", "谢谢", "太好了", "哈哈", "顺利", "成功"))
        if mood:
            await self._store.save(umo, "last_mood", mood)
            await self._store.save(umo, "last_mood_at", now)
            deltas = {
                "sad": {"pleasantness": -0.35, "arousal": -0.10},
                "angry": {"pleasantness": -0.30, "tension": 0.40, "arousal": 0.30},
                "anxious": {"tension": 0.40, "certainty": -0.30},
            }
            try:
                await self._emotion.nudge(umo, deltas.get(mood, {}), text[:80])
            except Exception:
                pass
        elif positive:
            try:
                await self._emotion.nudge(umo, {"pleasantness": 0.25, "tension": -0.10}, text[:80])
            except Exception:
                pass

        # 关系识别：日常互动小幅累积，情绪/正向互动额外加分
        try:
            await self._relationship.nudge(
                umo, 0.01 + (0.03 if (mood or positive) else 0.0), "对话互动"
            )
        except Exception:
            pass

        topic = None
        if self._cfg.topic_stack_enabled:
            topic = await self._topics.observe(umo, text)
            if self._cfg.open_loop_enabled and topic:
                await self._loops.register(umo, topic, emotion=mood or "neutral")

        # 关系记忆：把有情绪峰值或新话题的互动写成锚点，供后续状态层引用
        if self._cfg.relation_memory_enabled and topic:
            try:
                await self._memory.remember(
                    umo, topic, text[:200],
                    mtype="emotion" if mood else "episodic",
                    emotion_peak=0.8 if mood else (0.5 if positive else 0.2),
                )
            except Exception:
                pass

        if self._cfg.open_loop_enabled:
            await self._loops.observe_resolution(umo, text)

    async def _observe_once(self, umo: str, text: str) -> None:
        """同一分钟同一消息只观察一次，避免 regex 入口与 LLM 钩子重复计数。"""
        key = (umo, text[:50], int(time.time() // 60))
        if key == self._last_observe_key:
            return
        self._last_observe_key = key
        await self._observe_user_message(umo, text)

    async def _start_counsel(self, umo: str, text: str) -> str:
        topic = (text or "此刻的感受").strip()[:60]
        await self._store.save(umo, "counseling_session", {
            "active": True, "topic": topic, "started_at": time.time(),
        })
        await self._store.save(umo, "counseling_active", True)
        if is_distress(text):
            return (
                "我在。听起来你现在不太好受。\n"
                "不用急着说清楚，想到哪说到哪——最让你难受的是哪一部分？"
            )
        return "好，我在。你想从哪件事说起？"

    async def _end_counsel(self, umo: str, text: str = "") -> str:
        await self._store.save(umo, "counseling_session", {
            "active": False, "ended_at": time.time(),
        })
        await self._store.save(umo, "counseling_active", False)
        if "谢谢" in (text or ""):
            return "不客气。今天先到这儿，照顾好自己，想接着聊随时找我。"
        return "好，那我们先停在这儿。你能说出来已经很不容易了，之后想聊随时找我。"

    async def _run_task(self, task: str, event: AstrMessageEvent, text: str):
        handlers: dict[str, Any] = {
            "status": self.cmd_status,
            "chart": self.cmd_paipan,
            "divination": self.cmd_bugua,
            "tarot": self.cmd_tarot,
            "my_chart": self.cmd_my_chart,
            "set_chart": self.cmd_set_profile,
            "method": self.cmd_method,
            "mood": self.cmd_mood,
            "health": self.cmd_health,
            "growth": self.cmd_growth,
        }
        handler = handlers.get(task)
        if handler is None:
            return
        if task in {"chart", "divination", "tarot", "set_chart", "method", "health"}:
            gen = handler(event, text)
        else:
            gen = handler(event)
        async for item in gen:
            yield item

    # ---------- 自然语言入口：所有私聊消息 ----------
    @filter.regex(r"(?s).+")
    async def auto_route(self, event: AstrMessageEvent):
        """按语义进入功能或开解状态；不依赖斜杠命令。"""
        if not self._cfg.enabled or not is_private(event):
            return
        raw = (getattr(event, "message_str", "") or "").strip()
        text = normalize(raw)
        if not text:
            return

        umo = event.unified_msg_origin
        await self._observe_once(umo, text)

        task = detect_task(text)
        distress = is_distress(text)
        # 纯情绪表达优先于“查心情”这类泛功能；健康诉求仍按任务处理
        if task == "mood" and distress:
            task = None
        active = bool(await self._store.load(umo, "counseling_active", False))
        exit_phrase = should_exit_counseling(text)

        # 混合模式：正则未命中时，用默认模型做一次轻量语义分类兜底。
        # 正则已命中（task/distress/exit）不调用模型，避免额外成本。
        if (
            self._cfg.semantic_router_enabled
            and not (task or distress or exit_phrase)
            and should_classify(
                text, active, min_chars=self._cfg.semantic_router_min_chars
            )
        ):
            decision = await classify_intent(
                self.context,
                text,
                active,
                timeout=self._cfg.semantic_router_timeout,
            )
            if decision and decision.confidence >= self._cfg.semantic_router_confidence:
                if decision.intent in TASK_INTENTS:
                    task = decision.intent
                if decision.counsel_action == "exit":
                    exit_phrase = True
                elif decision.counsel_action == "enter":
                    distress = True

        # 非开解态听到“不想聊了/没事了”只当普通语义，不反向开启开解；
        # 但同一句里如果有明确任务，仍继续处理任务。
        if not active and exit_phrase and not task:
            return

        if active:
            # 自然退出：先收束，再决定是否继续处理同一句里的具体任务
            if exit_phrase:
                closing = await self._end_counsel(umo, text)
                yield event.plain_result(closing)
                if task:
                    async for item in self._run_task(task, event, text):
                        yield item
                event.stop_event()
                return

            # 转向具体任务：先接住情绪，再处理任务，并结束开解态
            if task:
                await self._end_counsel(umo, text)
                lead = (
                    "我先接住你刚才的情绪，等你想细说时我都在。现在先把这件事处理好。"
                    if distress else "好，我们先把这件事处理好。"
                )
                yield event.plain_result(lead)
                async for item in self._run_task(task, event, text):
                    yield item
                event.stop_event()
                return

            # 仍在倾诉：不打断，交给带开解提示的默认模型继续回应
            return

        # 非开解态：明确功能意图优先；若同时有情绪，先简短承接
        if task:
            if distress:
                yield event.plain_result("我先接住你现在的感受，等你想细说时我都在。")
            handled = False
            async for item in self._run_task(task, event, text):
                handled = True
                yield item
            if handled:
                event.stop_event()
            return

        # 情绪困扰/想倾诉：自然进入开解，不显示任何模式或命令
        if distress or should_start_counseling(text):
            opening = await self._start_counsel(umo, text)
            yield event.plain_result(opening)
            event.stop_event()
            return

    # ---------- 内部功能实现（无斜杠注册） ----------
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

    # ============================================================
    # 命理 / 咨询 / 成长命令（v5 原生；LLM 统一走 AstrBot 默认模型）
    # ============================================================
    def _cmd_arg(self, event: AstrMessageEvent, arg: str, command: str) -> str:
        text = (arg or "").strip()
        if text:
            return text
        raw = (getattr(event, "message_str", "") or "").strip()
        for prefix in (f"/{command}", command):
            if raw.startswith(prefix):
                return raw[len(prefix):].strip()
        return ""

    async def _get_birth(self, umo: str) -> dict:
        raw = await self._store.load(umo, "birth_profile", {})
        return raw if isinstance(raw, dict) else {}

    async def _save_birth(self, umo: str, birth: dict) -> None:
        await self._store.save(umo, "birth_profile", birth)
        await self._store.save(umo, "chart_bound", True)

    def _get_client(self):
        if self._client is None:
            from client import MingbuClient

            base = str(self._raw_config.get("mingbu_api_url") or "http://localhost:2963")
            self._client = MingbuClient(base_url=base)
        return self._client

    async def _ask(self, prompt: str, *, session_id: str = "mingling_cmd") -> str:
        return await ask_default(self.context, prompt, session_id=session_id)

    async def _birth_payload(self, birth: dict) -> dict:
        return {
            "year": int(birth.get("year", 0)),
            "month": int(birth.get("month", 0)),
            "day": int(birth.get("day", 0)),
            "hour": clock_to_shichen(int(birth.get("hour", 0))),
            "gender": birth.get("gender", "male"),
            "name": birth.get("name", ""),
        }

    async def cmd_paipan(self, event: AstrMessageEvent, question: str = ""):
        if not self._cfg.enabled or not is_private(event):
            return
        if not self._cfg.counseling_enabled:
            yield event.plain_result("⚠️ 命理咨询功能当前未开启。")
            return
        umo = event.unified_msg_origin
        birth = await self._get_birth(umo)
        if not birth:
            yield event.plain_result(
                "📋 还没有你的命盘档案。\n"
                "能先告诉我你的公历出生年月日、具体钟点和性别吗？\n"
                "比如：1990年5月20日早上8点，男。"
            )
            return
        q = self._cmd_arg(event, question, "排盘")
        try:
            data = await self._get_client().combined(await self._birth_payload(birth))
        except Exception as e:
            logger.warning(f"[mingling] 排盘后端不可用: {e}")
            yield event.plain_result(
                f"📋 你的档案：{format_birth(birth)}\n"
                "命理计算服务暂时不可用，稍后再试；档案我已记住。"
            )
            return
        text = await self._ask(
            build_chart_prompt("八字+紫微", birth, data, q),
            session_id=f"mingling_chart_{event.get_sender_id()}",
        )
        if text:
            yield event.plain_result(text)
        else:
            yield event.plain_result(
                f"📋 已为 {format_birth(birth)} 排出命盘，但默认模型暂时不可用，"
                "稍后再试即可。"
            )

    async def cmd_bugua(self, event: AstrMessageEvent, question: str = ""):
        if not self._cfg.enabled or not is_private(event):
            return
        if not self._cfg.counseling_enabled:
            yield event.plain_result("⚠️ 命理咨询功能当前未开启。")
            return
        q = self._cmd_arg(event, question, "卜卦")
        if not q:
            yield event.plain_result(
                "🔮 请直接告诉我你想占卜的具体问题，例如：\n"
                "• 我该不该换工作？\n"
                "• 这次考试能过吗？\n"
                "• 我和 TA 的感情发展如何？"
            )
            return
        try:
            data = await self._get_client().liuyao(q, method="random")
        except Exception as e:
            logger.warning(f"[mingling] 卜卦后端不可用: {e}")
            yield event.plain_result("🔮 起卦服务暂时不可用，稍后再试。")
            return
        text = await self._ask(
            build_gua_prompt(q, data),
            session_id=f"mingling_gua_{event.get_sender_id()}",
        )
        yield event.plain_result(text or "🔮 卦已起，但默认模型暂时不可用，稍后再试。")

    async def cmd_tarot(self, event: AstrMessageEvent, question: str = ""):
        if not self._cfg.enabled or not is_private(event):
            return
        if not self._cfg.tarot_enabled:
            yield event.plain_result("⚠️ 塔罗功能当前未开启。")
            return
        from formatters import format_tarot
        from tarot import build_tarot_prompt, draw_spread, spread_for_question

        q = self._cmd_arg(event, question, "塔罗") or "我近期的整体状态"
        spread = draw_spread(spread=spread_for_question("general"), question=q)
        prompt = build_tarot_prompt(spread, q)
        text = await self._ask(
            prompt, session_id=f"mingling_tarot_{event.get_sender_id()}"
        )
        if text:
            yield event.plain_result(text)
        else:
            yield event.plain_result(
                format_tarot(spread) + "\n\n✨ 默认模型暂时不可用，先看看牌面。"
            )

    async def cmd_my_chart(self, event: AstrMessageEvent):
        if not self._cfg.enabled or not is_private(event):
            return
        birth = await self._get_birth(event.unified_msg_origin)
        if birth:
            yield event.plain_result(
                f"📋 你的命盘档案：\n{format_birth(birth)}\n\n我已经记住了，想排盘时直接跟我说就行。"
            )
        else:
            yield event.plain_result(
                "📋 还没有你的命盘档案。\n能告诉我你的公历出生年月日、具体钟点和性别吗？"
            )

    async def cmd_set_profile(self, event: AstrMessageEvent, text: str = ""):
        if not self._cfg.enabled or not is_private(event):
            return
        raw = self._cmd_arg(event, text, "设置命盘")
        birth = parse_birth(raw)
        if birth:
            await self._save_birth(event.unified_msg_origin, birth)
            yield event.plain_result(
                f"✅ 已记住你的出生信息：{format_birth(birth)}\n"
                "以后想排盘，直接跟我说就行。"
            )
            return
        current = await self._get_birth(event.unified_msg_origin)
        cur = f"\n📌 当前存档：{format_birth(current)}\n" if current else ""
        yield event.plain_result(
            f"📋 请告诉我你的出生信息：{cur}\n"
            "比如：1990年5月20日早上8点，男。\n"
            "公历日期、具体钟点、性别三项即可。"
        )

    async def cmd_method(self, event: AstrMessageEvent, method: str = ""):
        if not self._cfg.enabled or not is_private(event):
            return
        mapping = {
            "紫微": "ziwei", "紫微斗数": "ziwei", "八字": "bazi",
            "六爻": "liuyao", "奇门": "qimen", "梅花": "meihua",
            "自动": "auto", "auto": "auto",
        }
        m = self._cmd_arg(event, method, "命理方法")
        if not m:
            yield event.plain_result(
                "你想用哪种方法？紫微、八字、六爻、奇门、梅花，或者自动。\n"
                "当前默认是自动：有出生信息就走命盘，否则走六爻。"
            )
            return
        key = mapping.get(m)
        if not key:
            key = next((v for k, v in mapping.items() if k in m), None)
        if not key:
            yield event.plain_result("我没认出这个方法。紫微、八字、六爻、奇门、梅花、自动，你想用哪个？")
            return
        umo = event.unified_msg_origin
        birth = await self._get_birth(umo)
        if not birth:
            birth = {}
        birth["method"] = key
        await self._store.save(umo, "birth_profile", birth)
        yield event.plain_result(f"✅ 已设置后台测算方法：{key}。")

    async def cmd_mood(self, event: AstrMessageEvent):
        if not self._cfg.enabled or not is_private(event):
            return
        umo = event.unified_msg_origin
        rel = await self._relationship.get(umo)
        emo = await self._emotion.load(umo)
        topic = await self._topics.current(umo)
        mood = await self._store.load(umo, "last_mood", "")
        yield event.plain_result(
            f"🧠 近期情绪：{mood or '平稳'}\n"
            f"关系阶段：{rel.stage}（亲密度 {rel.intimacy:.2f}）\n"
            f"情绪四维：愉悦 {emo.pleasantness:.2f} / 紧张 {emo.tension:.2f} "
            f"/ 唤醒 {emo.arousal:.2f} / 确定 {emo.certainty:.2f}\n"
            f"当前话题：{topic or '无'}\n\n"
            "想深入聊聊，就把困扰告诉我；想看命盘，也可以直接问我。"
        )

    async def cmd_health(self, event: AstrMessageEvent, note: str = ""):
        if not self._cfg.enabled or not is_private(event):
            return
        if not self._cfg.health_enabled:
            yield event.plain_result("⚠️ 健康建议功能当前未开启。")
            return
        umo = event.unified_msg_origin
        raw = self._cmd_arg(event, note, "健康")
        if raw:
            await self._store.save(umo, "health_note", raw[:500])
        else:
            raw = str(await self._store.load(umo, "health_note", "") or "")
        if not raw:
            yield event.plain_result(
                "💡 想给你定制健康建议，先告诉我一点基本情况吧～\n"
                "例如：我每天12点睡7点起，一周运动2次，想改善睡眠。"
            )
            return
        text = await self._ask(
            build_health_prompt(raw),
            session_id=f"mingling_health_{event.get_sender_id()}",
        )
        yield event.plain_result(
            text or "💡 已记住你的情况，但默认模型暂时不可用，稍后再问我。"
        )

    async def _build_growth_note(self, umo: str) -> str:
        memories = await self._store.load(umo, "relation_memory", [])
        if not isinstance(memories, list):
            memories = []
        timeline = await self._store.read_timeline(umo, limit=30)
        text = await self._ask(
            build_growth_prompt(memories, timeline),
            session_id=f"mingling_growth_{umo}",
        )
        if not text:
            rel = await self._relationship.get(umo)
            topic = await self._topics.current(umo)
            text = (
                f"我慢慢摸清了我们的相处节奏：现在是「{rel.stage}」阶段，"
                f"最近常聊的是「{topic or '日常'}」。"
                "我学到的是：先接住你的感受，再谈事情。"
                "以后我也会继续这样陪着你。"
            )
        await self._store.save(umo, "growth_note", {
            "text": text, "at": time.time(),
        })
        await self._store.append_timeline(umo, "growth_reflect", {"text": text[:300]})
        return text

    async def cmd_growth(self, event: AstrMessageEvent):
        if not self._cfg.enabled or not is_private(event):
            return
        if not self._cfg.growth_engine_enabled:
            yield event.plain_result("⚠️ 成长反思功能当前未开启。")
            return
        text = await self._build_growth_note(event.unified_msg_origin)
        yield event.plain_result(f"🌱 成长反思\n{text}")
