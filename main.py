from __future__ import annotations

import os
import sys
import time

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
from llm_default import ask_default
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
        self._admin = AdminTimelineAPI(self._store, self._cfg)
        self._client = None

        # 同一消息可能触发多次 on_llm_request，用 (umo, text[:50], 分钟) 去重
        self._last_observe_key: tuple | None = None

    # ---------- 生命周期 ----------
    async def initialize(self) -> None:
        if not self._cfg.enabled:
            logger.info("[mingling] enabled=False，插件不注册任何钩子")
            return
        self._register_cron()
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
            manager.add_basic_job(
                name="mingling_growth_weekly",
                cron="0 3 * * 1",
                handler=_growth_all,
                description="命灵每周成长反思",
            )
        except TypeError:
            try:
                manager.add_basic_job("mingling_growth_weekly", "0 3 * * 1", _growth_all)
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

    @filter.command("排盘")
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
                "请发送：/设置命盘 1990年5月20日早上8点 男\n"
                "设置后我就能替你排盘。"
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

    @filter.command("卜卦")
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
                "• /卜卦 我该不该换工作？\n"
                "• /卜卦 这次考试能过吗？\n"
                "• /卜卦 我和 TA 的感情发展如何？"
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

    @filter.command("塔罗")
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

    @filter.command("我的命盘")
    async def cmd_my_chart(self, event: AstrMessageEvent):
        if not self._cfg.enabled or not is_private(event):
            return
        birth = await self._get_birth(event.unified_msg_origin)
        if birth:
            yield event.plain_result(
                f"📋 你的命盘档案：\n{format_birth(birth)}\n\n我已经记住了，随时可以 /排盘。"
            )
        else:
            yield event.plain_result(
                "📋 还没有你的命盘档案。\n请发送：/设置命盘 1990年5月20日早上8点 男"
            )

    @filter.command("设置命盘")
    async def cmd_set_profile(self, event: AstrMessageEvent, text: str = ""):
        if not self._cfg.enabled or not is_private(event):
            return
        raw = self._cmd_arg(event, text, "设置命盘")
        birth = parse_birth(raw)
        if birth:
            await self._save_birth(event.unified_msg_origin, birth)
            yield event.plain_result(
                f"✅ 已记住你的出生信息：{format_birth(birth)}\n"
                "现在可以 /排盘 或 /我的命盘。"
            )
            return
        current = await self._get_birth(event.unified_msg_origin)
        cur = f"\n📌 当前存档：{format_birth(current)}\n" if current else ""
        yield event.plain_result(
            f"📋 请告诉我你的出生信息：{cur}\n"
            "格式示例：/设置命盘 1990年5月20日早上8点 男\n"
            "公历、具体钟点、性别三项即可。"
        )

    @filter.command("命理方法")
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
                "🔮 用法：/命理方法 紫微|八字|六爻|奇门|梅花|自动\n"
                "当前默认：自动（有出生信息走命盘，否则走六爻）。"
            )
            return
        key = mapping.get(m)
        if not key:
            yield event.plain_result("⚠️ 没认出这个方法。可选：紫微 / 八字 / 六爻 / 奇门 / 梅花 / 自动。")
            return
        umo = event.unified_msg_origin
        birth = await self._get_birth(umo)
        if not birth:
            birth = {}
        birth["method"] = key
        await self._store.save(umo, "birth_profile", birth)
        yield event.plain_result(f"✅ 已设置后台测算方法：{key}。")

    @filter.command("心情")
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
            "想深入聊聊，可以 /开解 [话题]；想看命盘，可以 /排盘。"
        )

    @filter.command("开解")
    async def cmd_counsel(self, event: AstrMessageEvent, topic: str = ""):
        if not self._cfg.enabled or not is_private(event):
            return
        if not self._cfg.counseling_enabled:
            yield event.plain_result("⚠️ 咨询功能当前未开启。")
            return
        t = self._cmd_arg(event, topic, "开解")
        if not t:
            yield event.plain_result(
                "🧠 请告诉我你想探讨的话题，例如：\n"
                "• /开解 工作方向\n• /开解 感情困惑\n• /开解 人生意义"
            )
            return
        umo = event.unified_msg_origin
        await self._store.save(umo, "counseling_session", {
            "active": True, "topic": t, "started_at": time.time(),
        })
        await self._store.save(umo, "counseling_active", True)
        yield event.plain_result(
            f"🧠 来，咱聊聊「{t}」。\n\n"
            f"先不急着下结论，你先说说——关于{t}，你心里最真实的那句是啥？"
        )

    @filter.command("结束开解")
    async def cmd_end_counsel(self, event: AstrMessageEvent):
        if not self._cfg.enabled or not is_private(event):
            return
        umo = event.unified_msg_origin
        await self._store.save(umo, "counseling_session", {"active": False})
        await self._store.save(umo, "counseling_active", False)
        yield event.plain_result(
            "🧠 好，这次先到这儿。\n"
            "不管聊出个啥，你愿意跟我说这些，我就挺开心的。啥时候想接着聊，随时找我～"
        )

    @filter.command("健康")
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
                "例如：/健康 我每天12点睡7点起，一周运动2次，想改善睡眠"
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

    @filter.command("成长")
    async def cmd_growth(self, event: AstrMessageEvent):
        if not self._cfg.enabled or not is_private(event):
            return
        if not self._cfg.growth_engine_enabled:
            yield event.plain_result("⚠️ 成长反思功能当前未开启。")
            return
        text = await self._build_growth_note(event.unified_msg_origin)
        yield event.plain_result(f"🌱 成长反思\n{text}")
