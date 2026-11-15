# 命灵·AI伴侣 — 完整配置与后台设置模版

> 适用版本：**v4.6.2** ｜ 宿主：**AstrBot ≥ 4.26**
> 本文件是一套「可直接照抄填入」的模板。读完 0 节弄清配置分几层，再按需取用对应小节。
> 所有字段名均来自插件 `_conf_schema.json`，与 AstrBot 配置表单一一对应。

---

## 0. 配置分层总览（先读这段，避免填错地方）

| 层 | 在哪填 | 管什么 | 模板在 |
|----|--------|--------|--------|
| **L1 插件配置** | AstrBot WebUI → 插件管理 → `命灵·AI伴侣` → 配置（表单/原始 JSON） | 插件全部 70 个开关与参数 | 第 1 节 |
| **L2 平台 Provider** | AstrBot WebUI → 模型配置 / 服务提供商（STT / TTS / Rerank） | 增强功能依赖的外部模型 | 第 2 节 |
| **L3 环境变量 / Docker** | 宿主 `MINGBU_API_URL` 或 docker-compose `environment` | 命理服务地址（L1 也可填，L1 优先） | 第 3 节 |
| **L4 用户级命令** | 私聊里直接发斜杠指令给 Bot | 每位用户的出生信息 / 命理方法 | 第 4 节 |

> 关键约束：**L2 的 Provider 不在插件配置里**。插件里那些 `provider_*` 字段填的是「Provider ID 字符串」，留空表示「用 AstrBot 当前启用的同类型 Provider」。

---

## 1. 插件配置（L1）

### 1.1 直接可用的完整 JSON（粘贴到「原始配置」即可）

把下面整段粘贴进插件配置的原始 JSON 编辑框，再按 `ⓐ` 标注的 3 处改成本机值即可上线。

```json
{
  "mingbu_api_url": "http://localhost:2963",        // ⓐ 改成你的命理服务地址；与服务同机用 localhost，跨机/容器用 IP 或容器名
  "mingbu_interpret_enabled": false,
  "mingbu_interpret_provider": "",
  "mingbu_interpret_api_key": "",
  "mingbu_interpret_base_url": "",
  "mingbu_interpret_model": "",
  "persona_name": "灵犀",
  "counseling_enabled": true,
  "tarot_enabled": true,
  "health_enabled": true,
  "counseling_crisis_enabled": true,
  "growth_engine_enabled": true,
  "dream_engine_enabled": true,
  "token_monitor_enabled": true,
  "social_graph_enabled": true,
  "darkchannel_enabled": true,
  "group_slang_enabled": true,
  "character_collection_enabled": true,
  "conversation_inspection_enabled": true,
  "news_enabled": false,
  "proactive_search_enabled": false,
  "proactive_enabled": true,
  "quiet_hours_start": 23,
  "quiet_hours_end": 8,
  "llm_temperature": 0.75,
  "llm_max_tokens": 0,
  "llm_persona_style": "default",
  "daily_token_limit": 0,
  "provider_main_reply": "",
  "provider_dream": "",
  "provider_growth": "",
  "provider_proactive": "",
  "provider_news": "",
  "provider_search": "",
  "provider_image_vision": "",
  "provider_merged_forward": "",
  "worldview_enabled": true,
  "auto_worldview_match": true,
  "custom_worldview_json": "",
  "tts_enabled": false,
  "tts_auto_for_long_text": false,
  "image_vision_enabled": false,
  "stt_enabled": false,
  "provider_stt": "",
  "rerank_enabled": false,
  "provider_rerank": "",
  "darkchannel_cache_ttl": 3600,
  "proactive_interval_min": 60,
  "proactive_cooldown_hours": 72,
  "group_reply_probability": 0.3,
  "slang_confidence_threshold": 0.5,
  "weight_threshold": 0.45,
  "memory_max_per_user": 400,
  "background_watch_enabled": true,
  "background_watch_cron": "0 7 1 * *",
  "background_watch_emotion_threshold": 0.6,
  "background_watch_emotion_cooldown_min": 360,
  "background_watch_method_default": "auto",     // ⓐ 可改 ziwei/bazi/liuyao/qimen/meihua；auto=有出生→命盘，无→六爻
  "background_watch_bugua_question": "",          // ⓐ 卜卦类兜底问句，留空自动取用户最近一条消息
  "self_evolution_enabled": false,
  "self_evolution_cron": "0 4 * * *",
  "proactive_max_unanswered": 3,
  "proactive_active_grace_sec": 120,
  "tts_emotion_routing_enabled": false,
  "provider_tts": ""
}
```

> `ⓐ` 三处通常要改：`mingbu_api_url`（命理服务地址）、`background_watch_method_default`（默认命理方法）、`background_watch_bugua_question`（卜卦兜底问句，一般留空）。其余保持默认即是一套「均衡生产配置」。

---

### 1.2 分模块填表（逐项说明 + 推荐值）

#### ① 命理服务与 AI 解读

| 字段 | 类型 | 推荐值 | 说明 / 填法 |
|------|------|--------|-------------|
| `mingbu_api_url` | string | `http://<主机>:2963` | 命理计算服务地址。同机 `localhost`；容器间用服务名（如 `http://mingbu:2963`）。也可用环境变量 `MINGBU_API_URL`，但**本项优先**。 |
| `mingbu_interpret_enabled` | bool | `false` | 关闭时只用本地翻译表的轻量洞察；开启会调用 `/api/interpret`（耗平台 LLM 额度）。 |
| `mingbu_interpret_provider` | string | 留空 | 解读用的 LLM provider ID；留空用平台默认。 |
| `mingbu_interpret_api_key` | string | 留空 | 对应 provider 的 key；留空读平台环境变量。 |
| `mingbu_interpret_base_url` | string | 留空 | custom provider 必填。 |
| `mingbu_interpret_model` | string | 留空 | 模型名；留空用平台默认。 |

#### ② 人格与世界观

| 字段 | 类型 | 推荐值 | 说明 |
|------|------|--------|------|
| `persona_name` | string | `灵犀` | Bot 自称，仅首次/人格丢失时生效。 |
| `worldview_enabled` | bool | `true` | 把现代能力转译成角色世界观说法。 |
| `auto_worldview_match` | bool | `true` | 按当前角色自动选世界观风格。 |
| `llm_persona_style` | enum | `default` | `default/warm/mysterious/rational/energetic/quiet/hidden`，自动匹配开启时本项无效。 |
| `custom_worldview_json` | string | 留空 | 高级：自定义世界观映射 JSON，覆盖预设。 |

#### ③ 模型参数与多 Provider 编排

| 字段 | 类型 | 推荐值 | 说明 |
|------|------|--------|------|
| `llm_temperature` | float(0~2) | `0.75` | 主回复温度，越低越稳。 |
| `llm_max_tokens` | int | `0` | 0=不限制。 |
| `provider_main_reply` | string | 留空 | 指定主回复 Provider ID；留空用默认。 |
| `provider_dream` | string | 留空 | 梦境/预感任务 Provider。 |
| `provider_growth` | string | 留空 | 成长反思任务 Provider。 |
| `provider_proactive` | string | 留空 | 主动关怀任务 Provider。 |
| `provider_news` | string | 留空 | 新闻整理 Provider。 |
| `provider_search` | string | 留空 | 主动搜索 Provider。 |
| `provider_image_vision` | string | 留空 | 图片转述用的多模态 Provider（默认 LLM 已是多模态则可不填）。 |
| `provider_merged_forward` | string | 留空 | 合并转发转述 Provider。 |

> 全部留空＝所有任务走 AstrBot 默认 Provider。给某任务单独填 ID 可做到「轻重双核」（见 README 技法 5）。

#### ④ 休息 / 免打扰

| 字段 | 类型 | 推荐值 | 说明 |
|------|------|--------|------|
| `quiet_hours_start` | int(0~23) | `23` | 免打扰开始（开始>结束表示跨午夜）。 |
| `quiet_hours_end` | int(0~23) | `8` | 免打扰结束。 |

#### ⑤ 主动关怀

| 字段 | 类型 | 推荐值 | 说明 |
|------|------|--------|------|
| `proactive_enabled` | bool | `true` | 是否允许主动发消息。 |
| `proactive_interval_min` | int(5~1440) | `60` | 同一用户两次主动消息最小间隔（分钟）。 |
| `proactive_cooldown_hours` | int(1~720) | `72` | 用户不活跃后不再主动的冷却（小时）。 |
| `proactive_max_unanswered` | int(1~20) | `3` | 连续主动未回的暂停阈值（防骚扰）。 |
| `proactive_active_grace_sec` | int(0~1800) | `120` | 用户刚发言后的不打断宽限（秒）。 |

#### ⑥ 记忆与对话检视

| 字段 | 类型 | 推荐值 | 说明 |
|------|------|--------|------|
| `memory_max_per_user` | int(50~2000) | `400` | 单用户记忆上限，超出按衰减淘汰。 |
| `weight_threshold` | float(0~1) | `0.45` | 对话权重≥此值标为高权重、可能入记。 |
| `conversation_inspection_enabled` | bool | `true` | 分析对话权重、提取重要记忆。 |
| `darkchannel_cache_ttl` | int | `3600` | 暗盘洞察缓存秒数，0=不缓存。 |
| `slang_confidence_threshold` | float(0~1) | `0.5` | 群黑话被学习前的置信度阈值。 |
| `group_reply_probability` | float(0~1) | `0.3` | 群聊未@时的被动回复概率。 |
| `group_slang_enabled` | bool | `true` | 群黑话学习。 |
| `social_graph_enabled` | bool | `true` | 社交关系图谱。 |

#### ⑦ 增强功能（TTS / STT / 图片 / Rerank）

| 字段 | 类型 | 推荐值 | 依赖 | 说明 |
|------|------|--------|------|------|
| `tts_enabled` | bool | `false` | AstrBot TTS provider | 把 `<tts>` 块转语音。 |
| `tts_auto_for_long_text` | bool | `false` | 同上 | 长文本自动语音。 |
| `tts_emotion_routing_enabled` | bool | `false` | 同上（需支持 voice 参数） | 按情绪选音色/语速。 |
| `provider_tts` | string | 留空 | — | 指定 TTS Provider ID，留空用当前启用。 |
| `tts_voice` | string | 留空 | AstrBot TTS provider | TTS 音色名。SiliconFlow 用 `模型名:音色`(如 `FunAudioLLM/CosyVoice2-0.5B:alex`) 或裸名(`alex`/`ben`)；OpenAI 原生用 `alloy`。留空走接口默认（SiliconFlow 为 alex）。合成完全走 AstrBot Provider，该音色透传给 provider。 |
| `stt_enabled` | bool | `false` | AstrBot STT provider | 听语音消息并转写。 |
| `provider_stt` | string | 留空 | — | 指定 STT Provider ID，留空用当前启用。 |
| `image_vision_enabled` | bool | `false` | 多模态 LLM | 图片/合并转发视觉摘要。 |
| `rerank_enabled` | bool | `false` | AstrBot Rerank provider | 按当前消息重排记忆。 |
| `provider_rerank` | string | 留空 | — | 指定 Rerank Provider ID，留空用首个。 |

> **纯本地、开箱即用**（无需任何外部 Provider）：撤回感知、消息防抖、世界观适配 —— 这些开关不在此表，已随其他 bool 默认开启。
> 所有依赖 Provider 的项都**优雅降级**：对应 provider 没配时自动跳过，不报错。

#### ⑧ 后台关注（每月静默测算）

| 字段 | 类型 | 推荐值 | 说明 |
|------|------|--------|------|
| `background_watch_enabled` | bool | `true` | 每月静默测算 + 高权重情绪实时触发。需已接 Mingbu API。 |
| `background_watch_cron` | string | `0 7 1 * *` | 每月刷新注记的 cron（每月 1 日 07:00）。 |
| `background_watch_emotion_threshold` | float(0~1) | `0.6` | 情绪权重绝对值达此值立即重算；`0`=关闭情绪触发（仅每月定时）。 |
| `background_watch_emotion_cooldown_min` | int(5~10080) | `360` | 同一用户两次情绪触发的冷却（分钟）。 |
| `background_watch_method_default` | string | `auto` | 未用 `/命理方法` 设定时的默认方法（见第 4 节分流逻辑）。 |
| `background_watch_bugua_question` | string | 留空 | 卜卦类兜底问句，留空自动取用户最近消息。 |

**分流逻辑**（由 `background_watch_method_default` 或用户 `/命理方法` 决定）：
- `auto`：**有出生信息 → 命盘类**（`combined`：紫微+八字同算大限/流年/流月）；**无出生信息 → 卜卦类**（默认 `liuyao` 六爻）。
- `ziwei` / `bazi`：命盘类，走 `/api/combined`。
- `liuyao` / `qimen` / `meihua`：卜卦类，走 `/api/liuyao` 或 `/api/qimen`（梅花无服务端端点，桥接六爻）。

#### ⑨ 自我进化（每晚提炼事实）

| 字段 | 类型 | 推荐值 | 说明 |
|------|------|--------|------|
| `self_evolution_enabled` | bool | `false` | 每晚把对话提炼为显性事实回写记忆，**有 LLM 成本**。 |
| `self_evolution_cron` | string | `0 4 * * *` | 每晚 4:00 提炼。 |

#### ⑩ 其余功能开关（按需要开）

| 字段 | 类型 | 默认 | 作用 |
|------|------|------|------|
| `counseling_enabled` | bool | `true` | `/mingbu` 等命理指令响应。 |
| `tarot_enabled` | bool | `true` | 塔罗（纯本地抽牌）。 |
| `health_enabled` | bool | `true` | 健康生活指导。 |
| `counseling_crisis_enabled` | bool | `true` | 危机两线识别红线（**强烈建议保持 true**）。 |
| `growth_engine_enabled` | bool | `true` | 成长反思。 |
| `dream_engine_enabled` | bool | `true` | 梦境/预感。 |
| `token_monitor_enabled` | bool | `true` | Token 用量监控。 |
| `darkchannel_enabled` | bool | `true` | 命理暗盘洞察（不泄露术语）。 |
| `character_collection_enabled` | bool | `true` | 角色收集玩法。 |
| `news_enabled` | bool | `false` | 新闻阅读。 |
| `proactive_search_enabled` | bool | `false` | 主动搜索探索笔记。 |
| `daily_token_limit` | int | `0` | 每日插件 Token 限额，0=不限制。 |

---

## 2. AstrBot 平台 Provider 配置（L2，增强功能依赖）

本插件用到的 **STT / TTS / Embedding / Rerank / LLM** 均为 AstrBot 自己的「服务提供商」，在插件配置里管理开关、在 AstrBot 主设置里管 Key/Base。**STT 也完全走 AstrBot STT provider**：插件不持有任何 STT 凭据、不做直连调用，只需开 `stt_enabled` 并（可选）填 `provider_stt`。AstrBot 原生内置类型有限（STT 仅 Whisper/SenseVoice、Rerank 仅本地 vLLM、TTS 仅 Edge/OpenAI），需要自己接 OpenAI 兼容的第三方 API。先在 AstrBot 主设置配好 Provider，再把 ID 回填到插件 `provider_*` 字段（或留空用默认）。

> **前置必装**：语音相关（STT/TTS）处理需要宿主装 **ffmpeg**——Linux `apt install ffmpeg`；Windows 官网下载并加入 PATH；Mac `brew install ffmpeg`。否则语音转写/合成会失败。

### 2.0 五类能力总览

| 能力 | AstrBot 类型 | 推荐第三方 | 插件开关 | 缺 provider 时 |
|------|--------------|-----------|----------|----------------|
| LLM（主回复/解读/视觉） | 文本生成(OpenAI 兼容) | 硅基流动 / AIHubMix | `provider_main_reply` 等 | 用 AstrBot 默认 |
| STT（听语音） | 语音转文字 | 硅基流动 SenseVoice | `stt_enabled` + `provider_stt` | 自动跳过 |
| TTS（说语音） | 文字转语音 | 硅基流动 CosyVoice2 / Edge | `tts_enabled` + `provider_tts` | 自动跳过 |
| Embedding（语义记忆召回） | 嵌入(OpenAI 兼容) | 硅基流动 BAAI/bge-m3 | `embedding_enabled` + `provider_embedding` | 退回原序/Rerank |
| Rerank（记忆重排） | 重排序 | 硅基流动 bge-reranker | `rerank_enabled` + `provider_rerank` | 原序不变 |

> 最省事：**硅基流动（SiliconFlow）一个 Key 同时覆盖 LLM + STT + TTS + Embedding + Rerank 五类**（均 OpenAI 兼容，Base `https://api.siliconflow.cn/v1`）。AIHubMix 同理（`https://aihubmix.com/v1`）。

### 2.1 推荐方案：硅基流动（SiliconFlow）一站配齐五类

Key 在 `cloud.siliconflow.cn/account/ak` 获取。所有类型共用同一个 Key 与 Base `https://api.siliconflow.cn/v1`，国内访问稳、便宜（SenseVoice ≈ $0.006/分钟，bge-m3 / bge-reranker 有免费额度）。

**① LLM（主回复/解读/视觉）**
1. 服务提供商 → 新增 → **OpenAI**，API Base=`https://api.siliconflow.cn/v1`，Key=`sk-...`。
2. 模型：`deepseek-ai/DeepSeek-V3` 或 `Qwen/Qwen3-...`（按你已在用的来）。保存并设为默认 Provider。
3. 插件侧可用 `provider_main_reply` 等指定不同任务模型，留空＝默认。

**② STT（语音转文字）**
1. 服务提供商 → 新增 → 语音转文字 → 选 **Whisper（API）**。
2. API Base=`https://api.siliconflow.cn/v1`，Key=`sk-...`，模型=`FunAudioLLM/SenseVoiceSmall`（中文最佳；备选 `TeleAI/TeleSpeechASR`）。
3. 保存 → 点「服务提供商可用性」测试。
4. ⚠️ 若 Whisper(API) 不显示「API Base」字段（个别旧版写死 OpenAI）：改用社区插件 **NickCharlie/Astrbot-Voice-To-Text-Plugin** 的 `other`：Base `https://api.siliconflow.cn`，Endpoint `/audio/transcriptions`，Method `POST`，Content-Type `multipart/form-data`，Header `Authorization: Bearer {key}`，Response Path `text`。与版本无关，100% 能接。

**③ TTS（文字转语音）**
1. 服务提供商 → 新增 → 文字转语音 → 选 **OpenAI TTS（兼容）**。
2. API Base=`https://api.siliconflow.cn/v1`，Key=`sk-...`，模型=`FunAudioLLM/CosyVoice2-0.5B`（中文/方言/情感好；或 `fishaudio/fish-speech-1.5`）。
3. 保存 → 测试（返回 mp3/wav，AstrBot 的 OpenAI TTS provider 会落地为文件再发送）。
4. 💡 不想接第三方：直接用 **Edge TTS**（AstrBot 内置、免费、无需 Key），类型选 Edge TTS 即可。

**④ Embedding（语义向量召回）**
1. 服务提供商 → 新增 → 嵌入 → 选 **OpenAI 嵌入（兼容）**。
2. API Base=`https://api.siliconflow.cn/v1`，Key=`sk-...`，模型=`BAAI/bge-m3`（免费；中文多语强）。
3. 保存 → 测试。插件开启 `embedding_enabled` 后会自动探测此 provider；若用社区适配器 `astrbot_plugin_embedding_adapter`，也会自动识别。
4. 🔁 兜底：若 AstrBot 原生嵌入类型不便，装社区 **astrbot_plugin_embedding_adapter**（OpenAI/Ollama/Gemini），插件同样自动可用。

**⑤ Rerank（记忆重排）**
1. 服务提供商 → 新增 → 重排序 → 选 **vLLM Rerank**（类型 `rerank`）。
2. API Base=`https://api.siliconflow.cn`（AstrBot 自动补 `/v1/rerank`），Key=`sk-...`，模型=`BAAI/bge-reranker-v2-m3`（免费/极便宜；或 `Qwen/Qwen3-Reranker-8B`）。
3. 保存 → 测试。
4. 🔁 兜底：换 **Jina AI**（最标准 OpenAI 兼容 rerank）：Base `https://api.jina.ai`，Header `Authorization: Bearer <免费key>`，端点 `/v1/rerank`。

### 2.2 备用方案：AIHubMix 一个 Key 全包

AIHubMix 一个 OpenAI 兼容 endpoint 同时提供 **LLM / STT / TTS / Embedding / Rerank**，全用一个 Key 和 Base，零额外配置：
1. 服务提供商 → 新增 → **OpenAI**，API Base=`https://aihubmix.com/v1`，Key=你的 AIHubMix Key。
2. 保存后，在「文本生成 / 语音转文字 / 文字转语音 / 嵌入 / 重排序」分类里都选这个 provider 即可。

### 2.3 回填插件开关

配好 Provider 后，回到命灵插件配置：
- `stt_enabled: true`，`provider_stt` 填 STT provider ID（留空＝当前启用的 STT）。
- `tts_enabled: true`，`provider_tts` 填 TTS provider ID（留空＝当前启用的 TTS）。
- `embedding_enabled: true`，`provider_embedding` 填 Embedding provider ID（留空＝自动探测）。
- `rerank_enabled: true`，`provider_rerank` 填 Rerank provider ID（留空＝首个 Rerank）。
- 重载插件。

> 没配对应 Provider 也没关系：开关开启但 provider 缺失时**自动跳过该能力**，其余功能照常。建议顺序实测：`stt_enabled` → `tts_enabled` → `embedding_enabled` → `rerank_enabled`。

---

## 3. 环境变量 / Docker（L3，命理服务地址）

命理服务地址有两条设置路径，**L1 的 `mingbu_api_url` 优先级高于环境变量**。

**方式 A：环境变量（适合原生部署）**
```bash
export MINGBU_API_URL=http://192.168.2.108:2963
```

**方式 B：docker-compose（推荐，容器间用服务名）**
```yaml
services:
  astrbot:
    # ... 其余不变
    environment:
      - MINGBU_API_URL=http://mingbu:2963   # 命理服务容器名:端口
  mingbu:
    image: your-mingbu-image:latest
    expose:
      - "2963"
```

> 容器网络下**不要**用 `localhost`（那指向 astrbot 容器自己），用 `http://<服务名>:2963` 或宿主机 `http://host.docker.internal:2963`。

---

## 4. 用户级命令设置模版（L4，私聊发给 Bot）

这些命令决定**每位用户**的出生信息与后台测算方法，是「命理后台关注」生效的前提。

| 命令 | 用法示例 | 作用 |
|------|----------|------|
| `/设置命盘` | 先发 `/设置命盘`，再回 `1990年5月20日早上8点，男` | 登记出生信息（命盘类测算必需）。 |
| `/命理方法` | `/命理方法 紫微` ｜ `/命理方法 八字` ｜ `/命理方法 六爻` ｜ `/命理方法 奇门` ｜ `/命理方法 梅花` ｜ `/命理方法 自动` | 设定该用户后台测算用的命理方法；立即触发一次测算。 |
| `/排盘` | `/排盘` | 查看已存档的出生信息。 |
| `/我的命盘` | `/我的命盘` | 确认命盘档案是否已记住。 |
| `/心情` | `/心情` | 查看 Bot 对其近期情绪的判断。 |
| `/开解 [话题]` | `/开解 工作方向` | 开启多轮心理疏导会话。 |
| `/结束开解` | `/结束开解` | 结束疏导会话。 |
| `/塔罗` | `/塔罗 我近期状态` | 塔罗三牌阵（纯本地）。 |
| `/健康` | 先 `/健康` 再说日常作息 | 生成健康建议。 |
| `/卜卦` | 先 `/卜卦`，再直接说具体问题 | 引导卜卦问句。 |

**建议上线流程（给每位用户）**
1. 私聊 Bot，发 `/设置命盘` 并按提示登记出生信息。
2. 发 `/命理方法 紫微`（有出生）或 `/命理方法 六爻`（无出生/只想卜问）。
3. 之后 Bot 每月 1 日 07:00 会自动静默测算，并在合适的回复里自然透出「直觉」。

---

## 5. 后台定时任务模版

插件内置两个 cron 任务，由配置项控制表达式：

| 任务 | 配置项 | 默认 | 注册名 | 作用 |
|------|--------|------|--------|------|
| 命卜后台关注 | `background_watch_cron` | `0 7 1 * *` | `mingbu_background_watch` | 每月 1 日 07:00 静默刷新各用户注记；高权重情绪实时触发另算。 |
| 自我进化 | `self_evolution_cron` | `0 4 * * *` | （同模块注册） | 每晚 4:00 把对话提炼为事实回写（需 `self_evolution_enabled: true`）。 |

**cron 速查**（分 5 段：`分 时 日 月 周`）
```
0 7 1 * *     每月 1 日 07:00
0 4 * * *     每天 04:00
0 */6 * * *   每 6 小时
30 9 * * 1    每周一 09:30
0 0 1 1 *     每年 1 月 1 日 00:00
```
> 改完 cron 表达式后**重载插件**即可生效，无需重启 AstrBot。

---

## 6. 快速上线检查单

- [ ] **L1**：把第 1.1 节 JSON 粘进插件原始配置，`mingbu_api_url` 改成真实地址。
- [ ] **L3**（可选）：命理服务跨机/容器时，docker-compose 加 `MINGBU_API_URL`。
- [ ] **L2**（按需）：要用 STT/TTS/Embedding/Rerank 就在 AstrBot 配好 Provider（详见第 2 节，推荐硅基流动一站配齐五类），并开 `stt_enabled`/`tts_enabled`/`embedding_enabled`/`rerank_enabled`；**STT/TTS 务必先确认宿主已装 ffmpeg**。
- [ ] **重载插件**，日志出现 `[mingbu] 内核已就绪：灵犀` 即成功。
- [ ] **L4**：私聊 Bot 跑一遍 `/设置命盘` + `/命理方法`，验证后台关注链路。
- [ ] 命理服务可达性自检：`curl http://<mingbu>:2963/api/health`。

---

## 7. 已知小瑕疵（已顺手修正）

`metadata.yaml` 里的 `version` 此前停留在 `4.3.0`，而代码实际已是 `4.6.0`。**不影响加载**，但为版本一致已在本模板生成时同步改为 `4.6.0`。如你自行维护仓库，记得让两者对齐。
