# 参考插件分析报告（13 个 AstrBot 插件）

> 任务：分析 13 个参考插件的优缺点，提取可取之处并融入「命灵」(astrbot_plugin_mingbu)。
> 配套代码改动见 `ARCHITECTURE.md` 与本回合新增模块：
> `disguise_guard.py` / `emotion_detect.py` / `background_watch.py` /
> `self_evolution.py` / `client.py:interpret` / `memory_core.py:decay` /
> `proactive.py`(防骚扰) / `stars/enhancements.py`(TTS 情绪路由)。

---

## 一、结论速览（已采纳 / 已拒绝）

| 来源插件 | 采纳点 | 落地模块 | 风险 |
|---|---|---|---|
| iris_memory | 差异情感衰减（负面快忘/正面慢留） | `memory_core.Memory.decay` | 低 |
| livingmemory | 三阶段遗忘 + 去重强化（思路参考） | 规划中（`memory_core` 遗忘清理） | 低 |
| hindsight_memory | 留存门控 + 敏感度分级 + 查询扩展（思路） | 规划中 | 低 |
| private_companion | 休息门禁意图细分 + 回复质量校验（防术语/客服腔泄露） | `disguise_guard.py` | 低 |
| angel_heart | 轻量两级「该不该回」判断 | `proactive.py`(概率门控) | 低 |
| humanlike | 随机延迟 + 删标点（人味滤镜） | **未采用**（随机忽略太危险） | — |
| proactive_chat | 未回复上限 + 竞态丢弃 + quiet 门控 | `proactive.py` | 低 |
| self_evolution | 反思闭环提炼显性事实回写记忆 | `self_evolution.py`(默认关) | 中(有 LLM 成本) |
| virtual_life | ProactivePolicy 预算/冷却/空闲门控 | `proactive.py`(未回复上限) | 低 |
| tts_emotion_router | 情绪→音色路由 + 中文关键词 | `emotion_detect.py` + `enhancements.py` | 低 |
| stealer | **仅**「对 Bot 回复做情绪分析」算法 | `emotion_detect.py`(复用思路) | — |
| angel_memory / memorix | **未整体采纳**（重型 FAISS/Chroma 向量栈、过度抽象） | — | 高(运维重) |

**明确拒绝**：`stealer` 的「被动偷取群聊图片表情包」逻辑——未经同意收集他人图片，
违反平台 ToS 与隐私预期，仅取其「对 Bot 自身回复做情绪分析」的安全算法。

---

## 二、分组详析

### A. 记忆 / 检索组（hindsight_memory / angel_memory / iris_memory / memorix / livingmemory）

**共同可取之处**
- **iris_memory**：`EmotionDecayProfile` 差异情感衰减——负面情绪半衰期短（~7 天，情感愈合），
  正面情绪半衰期长（~60 天，留住美好）。`RIFScorer` 用 Recency/Relevance/Frequency 加权排序。
  ✅ **已采纳**：`memory_core.Memory.decay` 现用 `emotional_weight` 调节衰减速度
  （负值加速、正值减速），与现有 `conversation_inspector` 写入的 `emotional_weight` 直接联动。
- **livingmemory**：`AtomLifecycleManager` 三阶段遗忘（expire→forget→purge）+ Jaccard 去重强化。
  ✅ **规划采纳**：命灵当前 `decay` 仅降分，无真正清理；下一步引入三阶段生命周期。
- **hindsight_memory**：规则+LLM 留存门控、敏感度分级、查询扩展。
  ✅ **规划采纳**：用于 `recall` 前的多语义 query 扩展与隐私闸门。

**共同风险（拒绝整体采纳）**
- angel_memory / memorix 引入 FAISS+SQLite / Chroma+知识图谱，50+ 文件、component_factory、
  migrations，运维极重，每消息 embedding 成本高，且含死抽象。命灵坚持 KV + 纯逻辑可测，
  **不引入重型向量栈**。

### B. 人格 / 陪伴组（private_companion / angel_heart / humanlike）

- **private_companion**：`UserRestGateMixin` 区分「别回这条 / 别主动找我 / 别解读这张图」，
  用户再活动自动解除静默；日记质量校验检测「后台术语泄露 / 客服腔 / 虚构确认」。
  ✅ **已采纳**：`disguise_guard.check_disguise_leak` 检测命理术语与客服腔，作暗盘防御性兜底
  + 回复可观测性检查，守住「不像客服机器人」铁律。
- **angel_heart**：轻量 analyzer 两级判断（该不该回），无正当理由不回；状态机 + 超时降级。
  ✅ **部分采纳**：`proactive.py` 的关系分门控 + 免打扰 + 本次新增的「用户刚活跃不打断」已覆盖。
- **humanlike**：`stop_responding`（模型自决退场）、随机延迟、删标点。
  ⚠️ **仅采纳**「可选人味滤镜」思路（默认关、绝不用于咨询）；**拒绝**随机忽略（陪伴型冷落风险高）。

### C. 主动 / 自我进化组（proactive_chat / self_evolution）

- **proactive_chat**：APScheduler 随机间隔、未回复上限、竞态丢弃（生成期间用户已发言则丢弃）、
  quiet 门控、misfire 恢复。
  ✅ **已采纳**：`proactive.py` 新增 `proactive_max_unanswered`（未回复上限）、
  `proactive_active_grace_sec`（用户刚活跃不打断）。
- **self_evolution**：`engagement_planner` 机会评分（relation/persona_drive）、
  `SessionReflection` 反思闭环、`SAN` 精力预算。
  ✅ **部分采纳**：`self_evolution.py` 取其最安全的一块——每晚把近期对话提炼为「显性事实」
  （偏好/计划/关系）回写持久记忆（`importance=0.7`）。**默认关闭**（有 LLM 成本）；
  拒绝 SAN 拒答（易让用户困惑）与 persona 巨模块。

### D. 特殊能力组（tts_emotion_router / virtual_life / stealer）

- **tts_emotion_router**：情绪→音色/语速路由（voice_map + 服务商抽象 + fallback）。
  ✅ **已采纳**：`emotion_detect.detect_emotion`（中文关键词，6 类）+ `enhancements._tts_voice_for`，
  由 `tts_emotion_routing_enabled` 开关控制（需 provider 支持 voice 参数，否则仅记录情绪）。
- **virtual_life**：`ProactivePolicy` 预算/冷却/空闲/白名单门控。
  ✅ **已采纳**：与 proactive_chat 的未回复上限合并，强化主动消息安全阀。
- **stealer**：被动监听全部消息、按意图 nudge；**但**核心是「未经同意偷取群聊图片表情包」——⚠️
  **伦理/隐私风险，明确拒绝整体采纳**。仅取其「对 Bot 自身回复做轻量情绪分析」的安全算法，
  用于驱动 TTS 情绪识别（与 tts_emotion_router 合并）。

---

## 三、命卜「后台关注」与 API 接入（本回合另一主线）

参考 `mingbu-main.zip`（Next.js 命理平台）的 API 契约：
- `POST /api/bazi` `{birthInfo}`→`{chart}`；`POST /api/combined`→`{analysis}`
- `POST /api/interpret`（SSE 流）：`type`(ziwei/bazi/combined/qimen/liuyao/meihua)、
  `birthInfo`/`chart`、`topic`、`provider`、`apiKey`、`baseUrl`、`model`、`question`、`questionType`、
  `targetYear` 等，返回流式解读。

**已落地**（用户要求「接入 API 后，即可正常后台关注」）：
1. `client.py:interpret()` — 调用 `/api/interpret`，累积 SSE 为完整文本（纯函数 `_accumulate_sse` 可测）。
   新增配置 `mingbu_interpret_*`（provider/api_key/base_url/model）。
2. `background_watch.py` — 每日 cron 静默计算各用户当前大运/流年，蒸馏成**术语无关**的
   「后台留意」短句存入 KV；暗盘与主动关怀可随时引用，把「我最近默默留意到你那儿有点在变」
   的轻柔感觉自然织入回复，用户永不知道来自命盘。
3. 配置：`background_watch_enabled`(默认开) / `background_watch_cron`(默认每天 7:00) /
   `self_evolution_enabled`(默认关) / `proactive_*` 防骚扰 / `tts_emotion_routing_enabled`(默认关)。

---

## 四、刻意未采纳（避免过度工程）

- 任何重型向量/图谱检索栈（angel_memory、memorix、livingmemory 的 FAISS/Chroma）。
- stealer 的图片被动采集与表情库逻辑（隐私/ToS 风险）。
- humanlike 的随机忽略用户（陪伴型冷落）。
- self_evolution 的 SAN 拒答、persona_arc 巨模块（成本高、易让用户困惑）。
- virtual_life 的穿搭生成器（偏怪、无必要）。
