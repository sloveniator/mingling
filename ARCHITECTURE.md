# astrbot_plugin_mingbu — 拟人增强架构设计 v4（多 Star 拆分）

> 基于 12 个成熟 AstrBot 插件（memorix / iris_memory / hindsight_memory / livingmemory / angel_memory / humanlike / private_companion / angel_heart / proactive_chat / self_evolution / stealer / tts_emotion_router）的模式提炼。

## 设计原则（来自参考插件共识）

1. **持久化首选 KV Store**：`self.get_kv / self.set_kv`（AstrBot 托管，重启不丢）。生产级插件用 SQLite+FAISS，但原型阶段 KV+JSON 足够。
2. **记忆生命周期**：写入(strength/importance) → 检索(BM25+向量/近期) → 衰减(decay scheduler) → 巩固(睡眠/梦境 consolidate)。
3. **人格注入方式**：`@filter.on_llm_request()` 内 `req.extra_user_content_parts.append(block)` 或 `req.system_prompt += block`。**绝不改写插件外文件**，每次请求动态拼装。
4. **后台周期任务**：优先 `context.cron_manager.add_basic_job(name, cron, handler, persistent=True)`（框架托管、重载安全），兜底用 `asyncio` 循环。
5. **主动发消息**：`await self.context.send_message(session_id, MessageChain([Plain(text)]))`；群聊可用 `platform.bot.send_group_msg`。
6. **被动监听全部**：`@filter.event_message_type(EventMessageType.ALL)` 捕获所有消息（含未被 @）。
7. **情绪维度模型**：Izard/Plutchik 四维（pleasantness/tension/arousal/certainty）随时间衰减 + 事件 nudge。
8. **拟人技巧**：打字延迟 `asyncio.sleep`、随机沉默 `event.stop_event()`、情绪→语气指令。

## 代码结构（v4 多 Star 拆分）

> 原 `main.py` 为 ~1180 行 god-file，已拆为「共享内核单例 + 1 核心 Star + 4 职责 Star」。
> 所有 Star 共享同一个 `MingbuCore` 单例（避免重复初始化引擎 / HTTP 连接池）。

| 文件 | 角色 | 关键内容 |
|------|------|---------|
| `core.py` | 共享内核 + 基类 | `MingbuCore` 单例（api/tracker/diviner + ensure 惰性建 memory/dream/growth/proactive/social/persona/counsel_sessions + shutdown）；`MingbuStar(Star)` 基类（引擎属性代理 + `_llm_text` + 全部共享助手方法 + `_on_llm_request_body` 钩子主体 + 结构化上下文/话题追踪/出生校验/健康档案存取） |
| `main.py` | 核心 Star `MingbuPlugin` | 极薄：生命周期（initialize→core.ensure / terminate→core.shutdown）+ 唯一 `@filter.on_llm_request` 钩子（转发到 `_on_llm_request_body`）+ `@filter.on_llm_response` 记录本机回复轮次；顶层 `import stars.*` 触发 AstrBot 注册 |
| `counseling.py` | 情绪识别 + 心理疏导 | 关键词情绪识别（含愤怒维度与严重度评分）、心理咨询工具箱（5 技能/5 流派/自适应映射）、**危机识别红线（detect_crisis + 援助热线）**、**多轮疏导会话管理器（CounselingSession / CounselingSessionManager / counseling_turn_prompt）** |
| `tarot.py` | 塔罗牌引擎（纯逻辑） | 78 张牌库（22 大阿卡纳逐张 + 56 小阿卡纳生成）、牌阵（单张/三张/凯尔特十字/感情/抉择/事业）、可复现抽牌（draw_spread）、解读提示构建（build_tarot_prompt）。**无网络依赖** |
| `health.py` | 健康生活指导（纯逻辑） | 健康档案（HealthProfile）、作息/饮食/运动个性化建议生成、LLM 提示构建、非医疗免责声明。无网络依赖 |
| `stars/collectors.py` | `StarCollectors` | 4 个 `@filter.regex`：出生年 / 属相 / 命卜关键词 / 情绪信号 |
| `stars/tools.py` | `StarTools` | 9 个 `@filter.llm_tool`：query_bazi / query_ziwei / divination_liuyao / query_combined / query_situation / guided_reflection / **divination_tarot / health_advice / psychological_counsel** |
| `stars/commands.py` | `StarCommands` | 10 个 `@filter.command`：/排盘 /卜卦 /我的命盘 /设置命盘 /命理方法 /心情 /开解 / **塔罗 /健康 /结束开解** |
| `stars/admin.py` | `StarAdmin` | `initialize` 注册 Plugin Pages Web API（含对话检视/角色收集/世界观等路由） |

**关键设计约束**
- AstrBot 扫描插件目录下所有含 `Star` 子类的模块；`main.py` 必须在顶层 `import` 各子 Star 才能触发注册。
- `MingbuCore.ensure(owner)` 幂等：首个初始化的 Star 完成真实引擎创建，其余 Star 复用同一单例；`get_kv/set_kv` 由各 Star 自己的 `Star` 基类原生提供（共享 context）。
- 引擎模块（`dream/growth/proactive/social/memory_core`）经 `self.plugin.<attr>` 访问插件对象，故 `MingbuStar` 基类必须暴露 `_llm_text / _get_profile / _birth_from / _get_relationship / _active_uids` 等方法与引擎属性代理。
- "暗盘不进梦境"：梦境模块不得 import 或引用暗盘通道（架构守卫 `test_dream_does_not_reference_divination`）。

## 八大系统映射

| 用户需求 | 模块 | 复用自 | 核心机制 |
|---------|------|-------|---------|
| 记忆 | `memory_core.py` | iris/living/angel_memory | 5 类记忆 + 衰减 + 巩固 |
| 性格 | `persona.py` | private_companion/angel_heart | 人格状态 + 情绪四维 + 注入 |
| 习惯 | `persona.py` (habits) | self_evolution persona_sim | 行为模式累积演化 |
| 想法 | `persona.py` (thoughts) | self_evolution 内心独白 | 想法流 + 偶发表达 |
| 成长 | `growth.py` | self_evolution | LLM 反思 → 用户画像/里程碑 |
| 人生 | `growth.py` (life_arc) | self_evolution profile | 人生叙事弧 |
| 情感 | `persona.py` + `counseling.py` | private_companion/tts_emotion | 四维情绪 + 咨询引擎 |
| 梦境 | `dream.py` | angel_memory DeepMindSleep | 夜间巩固 → 梦境叙事 |
| 主动 | `proactive.py` | proactive_chat | cron 检查 + 主动消息 |
| 被动 | `stars/collectors.py` regex / `main.py` on_llm_request | stealer | 监听+捕获+静默排盘 |

## 数据模型

```python
# memory_core.py
Memory {
    id, user_id, type,            # type: EPISODIC/SEMANTIC/EMOTIONAL/DREAM/GROWTH/THOUGHT
    content, source, importance,  # 0-1
    emotional_weight,             # -1..1
    tags, created_at, last_accessed, access_count, decay_score
}

# persona.py
PersonaState {
    name, traits[], habits[], interests[],
    mood, energy, social_need,    # 0-100 自然波动
    emotion { pleasantness, tension, arousal, certainty },
}
Relationship { user_id, mode, score, notes, last_interaction }
```

## 注入顺序（on_llm_request）

```
req.extra_user_content_parts =
  [ 人格状态块, 记忆上下文块, 想法流块, 心理咨询钩子块, 预言祝福块 ]
```

## 后台任务（cron_manager）

| 任务 | cron | 作用 |
|------|------|------|
| mingbu_proactive | `*/20 * * * *` | 检查活跃会话，决定是否主动关怀 |
| mingbu_dream | `0 3 * * *` | 夜间记忆巩固 + 生成梦境 |
| mingbu_growth | `0 2 * * *` | 反思对话 → 用户画像/成长里程碑 |
| mingbu_tick | `*/5 * * * *` | 人格自然波动 + 情绪衰减 |
| mingbu_background_watch | `0 7 1 * *` | 每月后台注记，按命理方法分流：命盘类测算大限/流年/流月，卜卦类卜问情况（术语无关）；另可由高权重情绪话语实时触发 |
| mingbu_self_evolution | `0 4 * * *` | 每晚对话提炼为显性事实（默认关） |

---

## 新增能力（2026-07-17）

### 1. 心理健康模块增强
- 情绪识别新增「愤怒」维度与**严重度评分**（`EMOTION_INTENSITY` + `EMOTION_INTENSIFIERS`，`EmotionalTracker.severity_level`）。
- **危机识别红线**：`detect_crisis` 命中自我伤害/轻生意向后，在 LLM 钩子最优先注入 `crisis_support_prompt`（含心理援助热线），并强制跳过命理预测与休息闸门。配置 `counseling_crisis_enabled`。
- **多轮心理疏导会话**：`CounselingSession` + `CounselingSessionManager`（KV 持久化 `mingbu.counsel:{uid}`）+ `counseling_turn_prompt`，跨多轮保持连贯，按阶段（开场→倾听→探索→重构→收束）推进。

### 2. 命卜模块 — 塔罗（tarot.py）
- 纯本地引擎（无后端依赖）：78 张牌库、6 种牌阵、可复现抽牌（问题+当日派生种子）、`build_tarot_prompt` 交给 LLM 自然解读。
- 工具 `divination_tarot` + 指令 `/塔罗`。配置 `tarot_enabled`。

### 3. 健康生活指导模块（health.py，全新）
- 健康档案（作息/运动/饮食/目标/身体状况）→ 个性化作息/饮食/运动建议，附**非医疗免责声明**。
- 工具 `health_advice` + 指令 `/健康`。配置 `health_enabled`。

### 4. 对话流程与上下文管理
- 上下文由扁平字符串升级为**结构化轮次**（`{role,text,ts}`，`mingbu.context:{uid}`，保留最近 10 轮），本机回复也在 `_on_llm_response` 记录，多轮连贯。
- 新增**话题追踪**（`mingbu.topic:{uid}`），帮助对话保持焦点。
- 出生参数**边界校验** `_validate_birth`，防止 LLM 传入非法年月日/钟点/性别。

### 5. 错误处理与边界情况
- 各新工具/指令均包裹 `try/except` 并返回友好降级文案；出生参数校验；危机用户跳过命理预测；健康模块明确非医疗边界。

---

## 新增能力（2026-07-17 第二回合）：API 接入 + 后台关注 + 参考插件融合

### 1. 命卜 API 接入与「后台关注」
参考 `mingbu-main.zip`（Next.js 命理平台）的 API 契约，插件侧新增：
- `client.py:interpret()` — 调用 `/api/interpret`（SSE 流）累积为完整解读文本；纯函数
  `_accumulate_sse` 可单测。配置 `mingbu_interpret_enabled` / `_provider` / `_api_key` /
  `_base_url` / `_model`（平台侧 LLM，与 AstrBot Provider 独立）。
- `background_watch.py:BackgroundWatcher` — **每月** cron（`background_watch_cron`，默认 `0 7 1 * *`）
  静默计算各用户当前大运/流年/**流月**（流月由流年干支按五虎遁纯 Python 推导，零依赖），
  蒸馏成**术语无关**的「后台留意」短句存入 KV（`mingbu.watch:{uid}`，TTL 7 天）。
  暗盘与主动关怀可引用，把「我最近默默留意到你那儿有点在变」的轻柔感觉自然织入回复
  ——用户永不知道来自命盘。即用户要求的「接入 API 后，即可正常后台关注」。
- **按命理方法分流（2026-07-18）**：`core._method_for(uid, profile)` 依据用户设置
  （`/命理方法` 命令，存 `profile["method"]`）或 `background_watch_method_default`（默认 `auto`）
  推断方法，后台测算据此分流：
  - **命盘类（紫微/八字）** → 调 `/api/combined` 测算 **大限/流年/流月**（大限取自紫微 `daXians`，
    由 `astrology.current_da_xian` 提取），术语无关注记由 `build_watch_phrase` 蒸馏；
  - **卜卦类（六爻/奇门/梅花）** → 调 `/api/liuyao` 或 `/api/qimen` **卜问情况**
    （梅花无服务端端点，桥接六爻），术语无关注记由 `build_divine_watch_phrase` 蒸馏。
  `scan_all` 同时覆盖有出生（命盘类）与无出生（卜卦类）的用户；情绪触发也按方法分流重算。
- **情绪触发重算**：`core.py` 在每次私聊消息钩子里，用 `emotion_to_weight(detect_emotion(text))`
  估算情绪权重；当用户出现**高权重情绪向话语**（绝对值 ≥ `background_watch_emotion_threshold`，
  默认 0.6，即可能出现特殊事件）时，立即异步重算该用户后台留意注记（带
  `background_watch_emotion_cooldown_min` 冷却，默认 360 分钟，避免一波情绪反复打 API）。
  行动红线（实施中自杀）下不触发（纯危机处理，不涉及命理关注）。纯逻辑可单测
  （`tests/test_background_watch.py` 的 `test_maybe_watch_on_emotion_*`）。
- 暗盘注入处（`core.py`）在私聊心理咨询钩子里追加 `weave_watch_note(note)`。

### 2. 记忆情感衰减（采纳 iris_memory）
- `memory_core.Memory.decay` 现用 `emotional_weight`（已由 `conversation_inspector` 写入）
  调节衰减速度：负面情绪衰减更快（情感愈合），正面情绪衰减更慢（留住美好）。
  纯逻辑、可单测（`tests/test_memory_emotion_decay.py`）。

### 3. 伪装护栏（采纳 private_companion 的回复质量校验）
- `disguise_guard.py:check_disguise_leak` 检测命理术语泄露（命盘/八字/紫微…）与客服腔
  （「请问还有什么可以帮您」…），作暗盘防御性兜底 + 回复可观测性检查。命灵铁律：
  暗盘洞察绝不以术语现形，也绝不带客服腔。

### 4. 情绪识别 + TTS 情绪音色路由（采纳 tts_emotion_router / stealer 算法）
- `emotion_detect.py:detect_emotion` 中文关键词 6 类（喜/悲/怒/兴奋/平静/中性），
  零 LLM 成本；`emotion_to_weight` 映射到记忆情感权重。
- `stars/enhancements.py:_send_tts` 在 `tts_emotion_routing_enabled` 时按情绪选音色
  （`tts_voice_map` JSON），provider 不支持则回退默认并仅记录情绪。
- `stars/enhancements.py:_on_voice_message`（新增）：`stt_enabled` 开启后检测语音(Record)
  消息，经 AstrBot STT provider 转写为文本注入对话（让 Bot 能"听"语音）。
- `stt.py`：语音组件检测 + 转写辅助，`transcribe_event_voice` 纯函数可单测。
- `core.py:_rerank_memories`（新增）：`rerank_enabled` 时按当前消息对记忆做相关性重排，
  经 `provider_router.get_rerank_provider` / `rerank_memory_list` 调用 AstrBot Rerank provider；
  无 provider 时原序不变。
- `core.py:_recall_semantic`（v4.6.0 新增）：`embedding_enabled` 时按当前消息对相关记忆做
  余弦相似度语义召回，经 `provider_router.get_embedding_provider` / `embed_texts` /
  `rank_indices_by_vector` 调用 AstrBot 原生或社区 `astrbot_plugin_embedding_adapter` 的
  Embedding provider；无 provider 时退回原序或 Rerank。
- `provider_router.py` 新增跨版本 provider 获取：`get_stt_provider` / `get_tts_provider` /
  `get_rerank_provider` / `get_embedding_provider` / `embed_texts` / `cosine_similarity` /
  `rank_indices_by_vector` / `call_rerank` / `parse_rerank_indices`（兼容 `get_using_*`、
  `get_all_*`、`get_provider_by_id`、manager 实例列表多种形态，取不到返回 None 优雅降级）。

### 5. 主动关怀防骚扰（采纳 proactive_chat / virtual_life）
- `proactive.py` 新增：`proactive_max_unanswered`（连续主动用户不理则暂停）、
  `proactive_active_grace_sec`（用户刚活跃不打断，竞态丢弃）。

### 6. 轻量自我进化（采纳 self_evolution 的反思闭环，默认关闭）
- `self_evolution.py:SelfEvolution` 每晚 cron（`self_evolution_cron`，默认 `0 4 * * *`）
  把近期对话提炼为「显性事实」（偏好/计划/关系）回写持久记忆（`importance=0.7`）。
  `self_evolution_enabled` 默认关（有 LLM 成本）。

> 13 个参考插件的完整优缺点与采纳/拒绝清单见 `REFERENCE_PLUGIN_ANALYSIS.md`。
> 明确拒绝：stealer 的图片被动采集（隐私/ToS 风险）、humanlike 的随机忽略用户、
> 各重型向量栈（FAISS/Chroma）过度工程。
