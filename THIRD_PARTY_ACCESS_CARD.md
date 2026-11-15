# 第三方 Provider 接入速查卡（命灵 v4.6.2）

> 命灵用到的 **STT / TTS / Embedding / Rerank / LLM** 都是 AstrBot 的「服务提供商」，在 AstrBot WebUI → 服务提供商 里配，**不在插件配置里**。配好后回插件填 `provider_*` 或留空用默认。
> **前置**：STT/TTS 需要宿主装 **ffmpeg**。

## 一句话结论

**硅基流动（SiliconFlow）一个 Key 同时覆盖五类**（均 OpenAI 兼容，Base `https://api.siliconflow.cn/v1`）。不想折腾就选它；或选 **AIHubMix**（`https://aihubmix.com/v1`）同理。

---

## 硅基流动填表（复制即用）

| 能力 | AstrBot 类型 | API Base | 模型 | 插件开关 |
|------|--------------|----------|------|----------|
| LLM | OpenAI | `https://api.siliconflow.cn/v1` | `deepseek-ai/DeepSeek-V3` | `provider_main_reply` 等 |
| STT | 语音转文字 → Whisper(API) | `https://api.siliconflow.cn/v1` | `FunAudioLLM/SenseVoiceSmall` | `stt_enabled` |
| TTS | 文字转语音 → OpenAI TTS | `https://api.siliconflow.cn/v1` | `FunAudioLLM/CosyVoice2-0.5B` | `tts_enabled` |
| Embedding | 嵌入 → OpenAI 嵌入 | `https://api.siliconflow.cn/v1` | `BAAI/bge-m3` | `embedding_enabled` |
| Rerank | 重排序 → vLLM Rerank | `https://api.siliconflow.cn` | `BAAI/bge-reranker-v2-m3` | `rerank_enabled` |

> 全部共用同一个 Key：`sk-...`（cloud.siliconflow.cn/account/ak）。
> Rerank 的 Base 不写 `/v1`，AstrBot 会自动补 `/v1/rerank`。

---

## 各类型要点 / 兜底

**STT**
- 个别旧版 Whisper(API) 不显示「API Base」字段 → 改用社区插件 **NickCharlie/Astrbot-Voice-To-Text-Plugin** 的 `other`：Base `https://api.siliconflow.cn`，Endpoint `/audio/transcriptions`，Method `POST`，Content-Type `multipart/form-data`，Header `Authorization: Bearer {key}`，Response Path `text`。

**TTS**
- 不想接第三方 → 直接用 **Edge TTS**（AstrBot 内置、免费、无需 Key）。

**Embedding**
- 若 AstrBot 原生嵌入类型不便 → 装社区 **astrbot_plugin_embedding_adapter**（OpenAI/Ollama/Gemini），插件自动识别。

**Rerank**
- 若 vLLM Rerank 与 SiliconFlow 格式偶有不吻合 → 换 **Jina AI**：Base `https://api.jina.ai`，Header `Authorization: Bearer <免费key>`，端点 `/v1/rerank`。

**LLM**
- 即 AstrBot 默认文本生成 Provider，命灵多数任务走它；可用 `provider_*` 为不同任务（主回复/梦境/成长/主动/视觉…）指定不同模型，留空＝默认。

---

## AIHubMix 一行版

服务提供商 → 新增 → **OpenAI**，API Base=`https://aihubmix.com/v1`，Key=你的 AIHubMix Key。保存后，在「文本生成 / 语音转文字 / 文字转语音 / 嵌入 / 重排序」里都选它。

---

## 插件侧回填（开关全开示例）

```json
{
  "stt_enabled": true,
  "provider_stt": "",
  "tts_enabled": true,
  "provider_tts": "",
  "embedding_enabled": true,
  "provider_embedding": "",
  "rerank_enabled": true,
  "provider_rerank": ""
}
```

> `provider_*` 留空＝自动用 AstrBot 当前/首个对应 Provider；指定 ID 则精确绑定。
> 缺某个 Provider 时对应能力**自动跳过**，其余照常。建议顺序实测：`stt` → `tts` → `embedding` → `rerank`。

## 自检

- 重载插件后日志出现 `[mingbu] 内核已就绪：灵犀` 即成功。
- STT 实测前先确认 `ffmpeg -version` 有输出。
