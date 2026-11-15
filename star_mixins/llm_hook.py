"""
star_mixins/llm_hook.py -- LLM 请求钩子主体：收集上下文 → 交给共享 HookChain 完成注入
=========================================================================
LLM 请求钩子主体：收集上下文 → 交给共享 HookChain 完成注入。

本模块由 ``MingbuStar`` 混入。方法体内的 ``self.<engine>`` 访问依赖
``MingbuStar.__getattr__`` 代理到 ``MingbuCore``，因此 mixin 本身不 import
``core``，也就不产生循环依赖。
"""
# ``random`` 原先写在方法体内做局部 import，每次 LLM 请求都要
# 走一次 sys.modules 查找。拆分时上提到模块级——语义完全等价，热路径省一次查表。
import asyncio
import random
import re

from astrbot.api import logger

from ..counseling import CounselingPrompts, detect_crisis
from ..emotion_detect import detect_emotion, emotion_to_weight
from ..hooks import get_default_chain
from ..integration.context_budget import DEFAULT_BUDGET, clip_to_budget, estimate_tokens
from ..memory_core import classify_reaction
from ..memory_core import SCOPE_GROUP, SCOPE_PRIVATE
from ..metrics import record_failure
from ..util import (
    inject_text_part,
    msg_type_is,
    spawn,
    strip_audio_parts_from_messages,
)

# 请求钩子同步生成当前图摘要的超时上限（秒）：视觉调用慢于此时长则回退泛化
# 提示，绝不阻塞主回复超过该时间。
IMAGE_HINT_SUMMARY_TIMEOUT = 25


async def _inject_current_image_hint(plugin, event, req) -> bool:
    """当前消息含图片时，同步生成摘要并注入请求上下文（根治图片串扰+读图卡死）。

    历史缺陷：
    1) 串扰：`_on_image_vision` 异步写摘要，主回复等不到 → 模型凭旧图记忆猜；
    2) 卡死：主 agent 自己调 `astrbot_file_read_tool` 读企微压缩图，但文件在
       temp 而工具在 workspace 找 → 路径错误 → 模型空转，回复"发过来吧"
       （生产日志 08:45 实证：模型尝试读 08:04 旧图文件失败）。

    本函数在 LLM 调用前**同步**生成当前图摘要并注入 req（带超时保护），
    模型直接拿到图内容，不再需要自己读文件。摘要失败/超时回退为泛化提示。

    返回是否注入了内容。
    """
    config = getattr(plugin, "_config", None) or {}
    if not config.get("image_vision_enabled", False):
        return False
    try:
        msg_obj = getattr(event, "message_obj", None)
        chain = getattr(msg_obj, "message", None)
        if not chain:
            return False
        image_urls = []
        for comp in chain:
            if getattr(comp, "type", "") == "Image":
                url = getattr(comp, "url", "") or getattr(comp, "file", "")
                if url:
                    image_urls.append(url)
        if not image_urls:
            return False

        uid = event.get_sender_id()
        summary = ""
        summarize = getattr(plugin, "_summarize_image_urls", None)
        if callable(summarize):
            try:
                summary = await asyncio.wait_for(
                    summarize(uid, image_urls), timeout=IMAGE_HINT_SUMMARY_TIMEOUT
                )
            except asyncio.TimeoutError:
                logger.debug("[mingbu] 当前图摘要超时，回退泛化提示")
                summary = ""
            except Exception:  # noqa: BLE001
                summary = ""

        if summary and summary.strip():
            inject_text_part(
                req,
                f"（对方刚刚给你发了一张新图片，就在这条消息里。"
                f"它的内容大致是：{summary.strip()}。"
                f"直接基于这个来回应就好——不用再调用任何文件读取工具，"
                f"也不要凭记忆里以前的图片猜测。）",
            )
        else:
            inject_text_part(
                req,
                "（对方刚刚给你发了一张新图片，就在这条消息里。"
                "请以这张当前图片为准来回应——不要凭记忆里以前的图片猜测。"
                "如果图片内容你看不清楚，就自然地说声抱歉没看清，让对方再说说。）",
            )
        return True
    except Exception:  # noqa: BLE001 - 注入失败绝不能阻断正常对话
        return False


async def _inject_gateway_extras(core, uid, req, message, session_id,
                                 is_group, limit=3) -> int:
    """2.0 ContextGateway 补充注入：把 gateway 额外召回的相关记忆补进 req。

    关系澄清：记忆回注**主链路**是 persona_ctx（经 PersonaHook 接 llm_hook），
    它内部自实现 recall_important/recall_emotional/retrieve_relevant 兜底。
    本函数走的是「已建未接线」的 gateway 补充路径，二者并行。为防同一记忆被
    两套路径重复注入，这里排除 persona_ctx 本轮已登记注入的 id
    （memory.get_last_injected），只把 gateway 额外召回、且未注入过的记忆作为
    轻量补充 hint。

    observe 总闸门：core.flags.observe / 总开关关闭 / gateway 未初始化 → 直接
    返回 0，行为零变化、可随时回滚（移除 llm_hook 中对它的调用即回到接入前）。
    整段 fail-open：gateway 任何异常都只记 debug，绝不阻断 AstrBot 正常对话。

    返回实际补充注入的记忆条数。
    """
    flags = getattr(core, "flags", None)
    gateway = getattr(core, "gateway", None)
    # observe / 总开关关闭 / gateway 未就绪 / 无消息内容 → 零行为变化
    if (flags is None or gateway is None
            or not flags.should_modify_request() or not (message or "").strip()):
        return 0
    try:
        ctx = await gateway.build(
            uid, session_id=session_id, message=message,
            is_group=is_group, limit=limit)
        # 去重：排除 persona_ctx 本轮已注入的记忆 id（同一事实不重复注入）
        already = set()
        try:
            already = {
                str(i) for i in await core.memory.get_last_injected(uid)
            }
        except Exception as e:  # noqa: BLE001 - 去重数据源失败不阻断补充注入
            logger.debug(f"[mingbu.gateway] 读已注入 id 失败(忽略): {e}")
        extra = [
            m for m in ctx.memories
            if getattr(m, "id", None) and str(m.id) not in already
            and (getattr(m, "content", "") or "").strip()
        ]
        if not extra:
            return 0
        # 2.0 预算联动：补充注入计入 context_budget（memory 来源），防多来源
        # hint（persona_ctx 主链路 + 本 gateway 补充）叠加超预算。做法：先为
        # 包络模板预留固定开销，再把候选记忆逐条裁到剩余预算内；超预算时保留
        # 相关度靠前的前缀、整段降级为不注入而非报错（fail-open）。
        envelope = (
            "（你心里还隐约记起一些相关的事：「」；「」。这些只是内心的参考，"
            "不是 TA 刚说的话，在相关时自然流露就好，不要逐条复述。）")
        budget = max(0, DEFAULT_BUDGET.get("memory", 800)
                     - estimate_tokens(envelope))
        contents = [(getattr(m, "content", "") or "").strip() for m in extra]
        kept, _report = clip_to_budget(contents, budget, source="memory")
        if not kept:
            logger.debug("[mingbu.gateway] 补充注入超预算，本轮降级为不注入")
            return 0
        inject_text_part(
            req,
            "（你心里还隐约记起一些相关的事：「"
            + "」；「".join(kept)
            + "」。这些只是内心的参考，不是 TA 刚说的话，"
            "在相关时自然流露就好，不要逐条复述。）",
        )
        logger.debug(
            f"[mingbu.gateway] 补充注入 {len(kept)}/{len(extra)} 条相关记忆"
            f"(预算 {budget} tok) -> {uid}")
        return len(kept)
    except Exception as e:  # noqa: BLE001 - 增强失败绝不能阻断正常对话
        logger.debug(f"[mingbu.gateway] 补充注入跳过(已忽略): {e}")
        return 0


class LLMRequestHookMixin:
    """LLM 请求钩子主体：收集上下文 → 交给共享 HookChain 完成注入。"""

    async def _on_llm_request_body(self, event, req):
        """LLM 调用前统一注入钩子（顶层 fail-open 包装）。

        2.0 fail-open 契约：本方法是全插件最高频路径，任何增强逻辑抛异常
        都**绝不能**阻断 AstrBot 原生对话。实际工作委托给
        ``_on_llm_request_body_impl``，此处只做顶层兜底：
        - 捕获所有异常 → record_failure + exception 日志 → 正常 return，
          放行 AstrBot 原生 LLM 调用（用户仍能收到回复）。
        - 正常控制流不受影响：impl 内部的 ``event.stop_event()``（去重/没听清
          转文字短路）执行后 impl 正常 return，包装层不感知、不拦截。
        - 命灵增强失效的代价是"这一轮没有个性化上下文"，而非"用户收不到回复"。

        impl 体量约 280 行（收集上下文 → 挂载 HookChain），将其完整包进
        try 会因重新缩进产生巨大 diff、难以审查，故采用"包装 + _impl"拆分：
        原方法体一行不改，仅改方法名。
        """
        try:
            await self._on_llm_request_body_impl(event, req)
        except Exception as e:  # noqa: BLE001 - 增强失败绝不能阻断正常对话
            try:
                uid = event.get_sender_id()
            except Exception:
                uid = "?"
            record_failure("request_enhancement")
            logger.error(
                f"[mingbu] LLM 请求增强失败，已降级至 AstrBot 原生对话"
                f"(uid={uid}): {e}",
                exc_info=True,
            )

    async def _on_llm_request_body_impl(self, event, req):
        """LLM 调用前统一注入钩子。收集上下文→挂载钩子链。"""
        uid = event.get_sender_id()
        msg_obj = getattr(event, 'message_obj', None)
        msg_type = getattr(msg_obj, 'type', None)
        is_group = msg_type_is(msg_obj, 'GroupMessage')
        is_private = msg_type_is(msg_obj, 'FriendMessage') or (msg_type is None)
        gid = getattr(msg_obj, 'group_id', '') or ''
        session_id = getattr(event, 'unified_msg_origin', None) or getattr(event, 'session_id', None) or ''
        mentioned = self._bot_mentioned(event) if is_group else True
        message_text = getattr(event, 'message_str', '') or ''
        # 命理咨询关闭时，命理意图判定失效（用户提命理词不再走"已在算卦"分支，
        # 心理咨询钩子等照常注入，v4.8.114 接线 counseling_enabled）
        user_is_divining = (
            self._config.get("counseling_enabled", True)
            and CounselingPrompts.has_divination_intent(message_text)
        )

        # ---- 当前消息是否含图（图片转述上下文串扰根治） ----
        # 历史缺陷：用户发新图时，_on_image_vision 异步写摘要（慢），主 agent 回复
        # 时拿不到新图信息，只能靠历史记忆里"上一张图"的 [看图] 摘要 → 模型把
        # 新图描述成旧图（生产日志 08:30 实证：新图被识别成上一张"奔跑角色"图）；
        # 且主 agent 自己读图路径错误会"卡死"（08:45 实证）。这里同步生成摘要注入。
        await _inject_current_image_hint(self, event, req)

        # ---- 根治：确保本次请求的 chat provider 的 modalities 不含 audio ----
        # AstrBot 4.27 实测：ProviderRequest 没有 messages 属性，且 runner 构建时已把
        # req.contexts 快照进 run_context.messages，钩子里剥 req 不生效；真正对快照生效
        # 的是 _iter_llm_responses 里的 _sanitize_contexts_for_provider，它只在 provider
        # 配置了 modalities 且不含 audio 时才把 audio_url 分片替换成 [Audio] 文本。
        # 这里把 provider 配置补齐，让 AstrBot 每轮自动净化历史语音分片。
        try:
            from ..provider_router import ensure_chat_provider_no_audio
            await ensure_chat_provider_no_audio(
                getattr(self, "context", None),
                umo=getattr(event, "unified_msg_origin", None),
            )
        except Exception as e:  # noqa: BLE001 - 修正失败绝不能阻断正常对话
            logger.debug(f"[mingbu] provider modalities 修正跳过(已忽略): {e}")

        # ---- 危机识别（最高优先级） ----
        # 只在这里检测一次：后续的 background_watch 需要用它做闸门，
        # 而提示词注入统一交给 CrisisHook 完成（此前两处都 append，导致重复注入）。
        crisis = None
        if is_private and self._config.get('counseling_crisis_enabled', True):
            crisis = detect_crisis(message_text)

        # ---- 后台关注·情绪触发 ----
        if self._config.get('background_watch_enabled', True) and self.background_watch                 and not (crisis and crisis.get('level') == 'action'):
            try:
                w = emotion_to_weight(detect_emotion(message_text))
                if abs(w) >= self._config.get('background_watch_emotion_threshold', 0.6):
                    spawn(self.background_watch.maybe_watch_on_emotion(uid, w), name="bg_watch_emotion")
            except Exception as e:
                logger.debug(f"[mingbu] 后台关注情绪触发失败(已忽略): {e}", exc_info=True)
                record_failure("background_watch_emotion")

        # ---- 记忆回写反馈（借鉴 memory_companion 的 mentionability 机制） ----
        # 用户对上一轮注入的记忆表现出否认/纠正/不愿被提及 → 降权并设 avoid，
        # 使 bot 下次不再主动翻旧账。仅在私聊或群里被点名时触发，避免群聊误伤。
        # 误判代价是错误降权某条记忆，故 classify_reaction 宁漏判不误判。
        if message_text and (is_private or mentioned) and not user_is_divining:
            try:
                reaction = classify_reaction(message_text)
                if reaction:
                    # 精确优先：用户消息含隐式短码（〔Mn〕）时，只对该短码对应的
                    # 记忆做单条反馈；无短码时回退全量（对上轮注入的所有记忆反馈）。
                    precise = await self.memory.parse_referenced_ids(uid, message_text)
                    if precise:
                        target_ids = precise
                    else:
                        target_ids = await self.memory.get_last_injected(uid)
                    for mid in target_ids:
                        await self.memory.feedback(uid, mid, reaction)
                    if target_ids:
                        logger.debug(
                            f"[mingbu] 记忆回写反馈 {reaction}: {len(target_ids)} 条"
                            f"({'精确' if precise else '全量'}) -> {uid}")
            except Exception as e:
                logger.debug(f"[mingbu] 记忆回写反馈失败(已忽略): {e}", exc_info=True)
                record_failure("memory_feedback")

        # ---- 状态记录（记忆 / 关系 / 会话追踪） ----
        await self._remember_user(uid)
        rel = await self._get_relationship(uid)

        if is_group and gid:
            await self.social.touch_group(gid, uid, event.get_sender_name() or '')
            if self.slang_manager and message_text:
                try:
                    await self.slang_manager.learn_candidates(gid, message_text)
                except Exception as e:
                    logger.debug(f"[mingbu] 群 slang 候选学习失败(已忽略): {e}", exc_info=True)
                    record_failure("slang_learn")

        if message_text and not user_is_divining:
            try:
                await self.memory.add_episode(uid, message_text, importance=0.45,
                                              scope=SCOPE_GROUP if is_group else SCOPE_PRIVATE)
            except Exception as e:
                logger.warning(f"[mingbu] 情景记忆写入失败(已忽略): {e}", exc_info=True)
                record_failure("memory_write")

        if self.conversation_inspector and message_text:
            try:
                await self.conversation_inspector.log(
                    uid=uid, content=message_text, msg_type='user',
                    gid=gid, session_id=session_id,
                    is_group=is_group, sender_name=event.get_sender_name() or ''
                )
            except Exception as e:
                logger.warning(f"[mingbu] 对话记录失败(已忽略): {e}", exc_info=True)
                record_failure("conversation_log")

        if self.persona:
            self.persona.nudge_from_message(message_text, is_user=True)
            spawn(self._save_persona(), name="save_persona")
        rel.interact(positive=not user_is_divining)
        # 关系分必须回写 KV：此前仅改内存，重启即清零，mingbu.rel:* 永远为空，
        # 预感梦门槛（score>=40）等关系成长特性永不生效（2026-09-05 修复）。
        spawn(self._save_relationship(rel), name="save_rel")

        # 角色羁绊随互动同步涨/跌（每用户 × 每角色，仅私聊）
        if is_private and self.character_collection:
            try:
                coll = await self.character_collection.get_user_collection(uid)
                char_id = coll.get("current")
                if char_id:
                    bond = await self._get_bond(uid, char_id)
                    bond.interact(positive=not user_is_divining)
                    spawn(self._save_bond(bond), name="save_bond")
            except Exception as e:
                logger.debug(f"[mingbu] 角色羁绊互动记录失败(忽略): {e}")

        if session_id:
            # proactive 是可选引擎（配置项"启用主动关怀"可关闭），与本文件其他
            # 引擎一致必须判空。此处原先无条件调用：一旦用户关掉主动关怀，
            # 每次私聊都会 AttributeError 冒泡到 main._on_llm_request（无兜底），
            # 整条注入链路失效。
            if self.proactive:
                from ..proactive import parse_platform
                umo = getattr(event, "unified_msg_origin", None) or ""
                # 企微客服主动推送（2026-09-07）：适配器把每条进入客服消息的
                # self_id 设为 open_kfid（wecom_adapter.convert_wechat_kf_message），
                # proactive 直连 client.kf_message.send_text 需要它。非企微平台
                # 的 self_id 是机器人自身标识，不影响（track_session 仅在企微
                # 发送路径读取该字段）。
                open_kfid = ""
                try:
                    mobj = getattr(event, "message_obj", None)
                    if mobj is not None and getattr(mobj, "self_id", None):
                        open_kfid = str(mobj.self_id)
                except Exception:
                    open_kfid = ""
                await self.proactive.track_session(
                    uid, session_id,
                    kind='group' if is_group else 'private',
                    gid=gid,
                    platform=parse_platform(umo),
                    open_kfid=open_kfid,
                )
            # onboarding 的角色分配不依赖 proactive，缺引擎时仍应执行。
            if is_private:
                await self._maybe_onboard(uid, session_id)

        # ---- 模型参数 ----
        if hasattr(req, 'temperature'):
            req.temperature = self._config.get('llm_temperature', 0.75)
        if hasattr(req, 'max_tokens'):
            mt = self._config.get('llm_max_tokens', 0)
            if mt > 0:
                req.max_tokens = mt

        # ---- 群聊概率拦截 ----
        if is_group and not mentioned:
            if random.random() > self._config.get('group_reply_probability', 0.3):
                return

        # ---- 剥离请求体中的 audio 分片（历史语音残党，防御兜底） ----
        # 主修复在钩子开头 ensure_chat_provider_no_audio：让 AstrBot 的
        # _sanitize_contexts_for_provider 对 runner 已快照的 run_context.messages
        # 每轮自动把 audio_url 分片替换成 [Audio] 文本。此处再对 req.contexts
        # （OpenAI dict 格式历史）直接剥一遍，兜底旧版 AstrBot / 非 runner 链路。
        # STT 转写文本（[语音转写] …）是 text 分片，天然保留；只剥 audio 载体。
        try:
            msgs = getattr(req, "contexts", None)
            if not isinstance(msgs, list):
                msgs = getattr(req, "messages", None)  # 极旧版本兼容
            if isinstance(msgs, list):
                strip_audio_parts_from_messages(msgs)
        except Exception as e:  # noqa: BLE001 - 剥离失败绝不能阻断正常对话
            logger.debug(f"[mingbu] audio 分片剥离跳过(已忽略): {e}")

        # ---- 注入 TTS 能力声明（防模型幻觉「没有语音能力」） ----
        # 日志实证：模型在回复中自称「系统未安装语音合成插件」「无法发送语音」，
        # 甚至编造 /AstrBot/data/temp/lilith_empathy.mp3 去调 send_message_to_user。
        # 插件其实有完整 TTS（直连 /audio/speech 或 AstrBot TTS provider）。
        # 写法注意：不用「[系统能力提示]…不要…不要…」式指令——那会被模型照抄
        # 成回复里的 "(TTS active)"「注：当前仍无法实际发送语音文件」等出戏内容。
        # 改为「自然认知」式描述（角色内在感觉），并顺带声明不出现任何内部标记。
        if self._config.get("tts_enabled", False):
            try:
                inject_text_part(
                    req,
                    "（关于声音，你心里很清楚：你的话有时候会被变成声音送出去，"
                    "那是很自然的事，不用提也不用解释。你从不会在话里出现"
                    "「(TTS active)」「注：当前仍无法发送语音」这类系统说明，"
                    "也不会提起「语音合成」「插件」「系统提示」之类的事。"
                    "你只是自然地说话，声音的事交给背后的人。）",
                )
            except Exception as e:  # noqa: BLE001 - 注入失败绝不能阻断正常对话
                logger.debug(f"[mingbu] TTS 能力声明注入跳过(已忽略): {e}")

            # ---- 注入上一条语音的真实发送结果（防模型幻觉「语音失败」） ----
            # 日志实证：模型回复后看不到 TTS 发送结果，用户问"语音呢/没听到"时
            # 会脑补「语音发送失败了」「系统未安装语音合成插件」。这里把 KV 里
            # 记录的真实结果在用户提及语音时回注，让模型基于事实回答。
            try:
                if re.search(r"语音|声音|听(?:到|不见|不清)|收到", message_text or ""):
                    uid = event.get_sender_id()
                    last = await self.get_kv_data(f"mingbu.last_tts_result:{uid}", None)
                    if isinstance(last, dict) and "ok" in last:
                        ok = bool(last.get("ok"))
                        reason = last.get("reason", "")
                        if ok:
                            inject_text_part(
                                req,
                                f"（你心里清楚，上一条回复已经被变成了声音送到对方那边了"
                                f"（原因：{reason}）。所以对方问你语音的事，你可以放心说"
                                f"「已经发了」，不用怀疑，也不用解释技术细节。）",
                            )
                        else:
                            inject_text_part(
                                req,
                                f"（你心里清楚，上一条回复转语音没有成功（原因：{reason}）。"
                                f"如果对方问起，你就自然地说可能是声音那边没传好，"
                                f"现在用文字补上就好，不用慌、也不用讲技术细节。）",
                            )
            except Exception as e:  # noqa: BLE001 - 注入失败绝不能阻断正常对话
                logger.debug(f"[mingbu] TTS 结果回注跳过(已忽略): {e}")

        # ---- 主动消息上下文回注 ----
        # 命灵 proactive 主动发的消息绕过 AstrBot 回复路径，不在 req.contexts
        # 里；用户回复时模型看不到自己主动说过什么。从 KV 取最近一条主动
        # 消息标记（proactive 发送成功时写入），时间近（<30min）才注入，
        # 让模型有上下文、回复能自然衔接——不生硬解释"我之前发了什么"。
        try:
            import json as _pjson
            from datetime import datetime as _pdt
            _raw = await self.get_kv_data(
                f"mingbu.last_proactive_msg:{uid}", None)
            if _raw:
                _info = _pjson.loads(_raw) if isinstance(_raw, str) else _raw
                _ts = _info.get("ts", "") if isinstance(_info, dict) else ""
                _fresh = True
                if _ts:
                    try:
                        _fresh = (_pdt.now()
                                  - _pdt.fromisoformat(_ts)).total_seconds() < 1800
                    except Exception:
                        _fresh = True
                if _fresh:
                    _snippet = (_info.get("text", "")
                                if isinstance(_info, dict) else "")[:80]
                    if _snippet:
                        inject_text_part(
                            req,
                            f'（你不久前主动给 TA 发过一条消息：「{_snippet}」。'
                            f"TA 现在回复了你，你的回复要和那条消息自然衔接，"
                            f'可以接着聊，但不要生硬地解释「我之前发了什么」。）',
                        )
        except Exception as e:
            logger.debug(f"[mingbu] 主动消息上下文回注跳过(已忽略): {e}")

        # ---- HookChain 执行所有注入逻辑 ----
        # 钩子无状态，复用进程内共享链，省去每次请求 1 链 + 5 对象的分配。
        await get_default_chain().run(self.core, event, req,
            is_private=is_private, is_group=is_group,
            mentioned=mentioned,
            message_text=message_text,
            user_is_divining=user_is_divining,
            crisis=crisis,
            inject_persona=is_private or (is_group and mentioned),
            rel=rel)

        # ---- 2.0 ContextGateway 补充注入（observe 总闸门 + 与 persona_ctx 去重）----
        # 逻辑抽到模块级 _inject_gateway_extras（可独立测试，风格同
        # _inject_current_image_hint）。observe/总开关关闭/未初始化 → 零行为变化，
        # 可回滚（移除本调用即回到接入前）；整段 fail-open 不阻断正常对话。
        await _inject_gateway_extras(
            self.core, uid, req, message_text, session_id, is_group)
