# 音色库拉取与试听功能 - 功能说明

## 功能概述

后台管理面板新增「音色库」标签页，支持：
1. **拉取音色列表**：从 DashScope/Qwen TTS 官方获取 24 个预置音色
2. **试听预览**：合成短文本/长文本并播放音频
3. **音色选择**：选择音色用于后续 TTS 合成

## 技术架构

### 后端（Python）

**文件**: `stars/apis/tts_voices.py`

- `TtsVoicesApiMixin` 类：实现 3 个 API 端点
  - `api_tts_voices()`: 拉取音色列表（POST）
  - `api_tts_preview()`: 试听短文本（POST）
  - `api_tts_preview_long()`: 试听长文本≤1600token（POST）

**路由注册**: `stars/admin.py` `_route_specs()` 方法
```python
(f"/{p}/tts/voices", self.api_tts_voices, ["POST"], "拉取 TTS 音色列表"),
(f"/{p}/tts/preview", self.api_tts_preview, ["POST"], "TTS 试听（短文本）"),
(f"/{p}/tts/preview_long", self.api_tts_preview_long, ["POST"], "TTS 试听（长文本）"),
```

**提供商支持**:
- **DashScope Qwen TTS** (主要): 使用 `MultiModalConversation` SDK，音色列表硬编码 24 个
- **CosyVoice** (预留): 接口已预留，后续补充 HTTP API 直连逻辑

### 前端（HTML/JS/CSS）

**文件**:
- `pages/admin/index.html`: 音色库标签页 UI
- `pages/admin/app.js`: 音色库交互逻辑
- `pages/admin/style.css`: 音色库样式

**功能流程**:
1. 用户点击「音色库」标签 → 自动拉取音色列表
2. 选择提供商（DashScope/CosyVoice）→ 刷新音色列表
3. 点击音色卡片「试听」→ 合成并播放音频
4. 点击「选择」→ 选中音色用于预览

## 音色数据

### DashScope Qwen TTS 官方音色 (24 个)

来源: https://help.aliyun.com/zh/model-studio/qwen-tts-voice-list (2026-01-26)

| 音色 | 语言 | 性别 | 描述 |
|------|------|------|------|
| Cherry | 中文 | 女 | 阳光、积极、友好、自然的年轻女性声音 |
| Serena | 中文 | 女 | 温柔、知性、沉稳的女性声音 |
| Ethan | 中文 | 男 | 成熟、稳重、有磁性的男性声音 |
| Liam | 中文 | 男 | 年轻、活力、清晰的男性声音 |
| Olivia | 英文 | 女 | 自然、流畅的英语女性声音 |
| Noah | 英文 | 男 | 清晰、专业的英语男性声音 |
| Aria | 日语 | 女 | 温柔的日语女性声音 |
| Hiro | 日语 | 男 | 沉稳的日语男性声音 |
| Sakura | 韩语 | 女 | 甜美的韩语女性声音 |
| Minjun | 韩语 | 男 | 年轻的韩语男性声音 |
| Yuki | 中文 | 女 | 清新、可爱的女性声音 |
| Jian | 中文 | 男 | 温暖、亲切的男性声音 |
| Luna | 英文 | 女 | 梦幻、空灵的女性声音 |
| Oliver | 英文 | 男 | 优雅、从容的男性声音 |
| Mei | 中文 | 女 | 古典、诗意的声音 |
| Chen | 中文 | 男 | 儒雅、书卷气的声音 |
| Zoe | 法语 | 女 | 浪漫的法语女性声音 |
| Pierre | 法语 | 男 | 优雅的法语男性声音 |
| Sofia | 西班牙语 | 女 | 热情的西班牙语女性声音 |
| Diego | 西班牙语 | 男 | 自信的西班牙语男性声音 |
| Chiara | 意大利语 | 女 | 明快的意大利女性声音 |
| Marco | 意大利语 | 男 | 热情的意大利男性声音 |
| Anna | 德语 | 女 | 清晰的德语女性声音 |
| Felix | 德语 | 男 | 稳重的德语男性声音 |

## API 接口

### 1. 拉取音色列表

**请求**: `POST /{plugin}/tts/voices`
```json
{
  "provider": "dashscope",  // 可选: "dashscope" | "cosyvoice"
  "api_key": ""  // 可选，留空则使用运行时配置
}
```

**响应**:
```json
{
  "ok": true,
  "voices": [
    {
      "voice": "Cherry",
      "language": "Chinese (Mandarin)",
      "gender": "Female",
      "description": "阳光、积极、友好、自然的年轻女性声音"
    }
  ],
  "provider": "dashscope",
  "count": 24
}
```

### 2. 试听短文本

**请求**: `POST /{plugin}/tts/preview`
```json
{
  "text": "你好，今天天气真好",
  "voice": "Cherry",
  "provider": "dashscope",
  "api_key": ""
}
```

**响应**:
```json
{
  "ok": true,
  "audio_b64": "<base64 encoded WAV data>",
  "duration_ms": 1234,
  "voice": "Cherry",
  "provider": "dashscope",
  "format": "wav"
}
```

### 3. 试听长文本

**请求**: `POST /{plugin}/tts/preview_long`
```json
{
  "text": "较长的测试文本...",
  "voice": "Cherry",
  "provider": "dashscope",
  "api_key": "",
  "instructions": "语速稍快，带点微笑的感觉"  // 可选，≤1600 token
}
```

**响应**: 同试听短文本

## 部署步骤

### 1. 代码部署

```bash
# 在 NAS 容器内执行
cd /volume8/AI/astrbot/astrbot_plugin_mingbu
git pull origin main  # 拉取最新代码（包含音色库功能）
```

### 2. 重载插件

通过 Reload API 重载（无需重启容器）：
```bash
curl -X POST http://127.0.0.1:6185/api/v1/plugins/reload \
  -H "Content-Type: application/json" \
  -d '{"plugin_id": "mingbu"}'
```

### 3. 验证功能

1. 打开管理面板 http://127.0.0.1:6185/admin
2. 点击「音色库」标签
3. 点击「拉取音色列表」按钮
4. 浏览音色卡片，点击「试听」播放音频
5. 输入长文本，点击「试听长文本」验证 1600 token 限制

## 配置要求

### 必需配置

- **DashScope API Key**: 在「核心配置」→「外部服务」→「TTS」中填写
  - 获取地址: https://dashscope.console.aliyun.com/
  - 模型: `qwen3-tts-instruct-flash`

### 可选配置

- **音色选择**: 默认使用 Cherry，可在配置中设置 `tts_voice` 键
- **语气指令**: 长文本试听支持 `instructions` 参数（≤1600 token）

## 限制说明

1. **文本长度**: 长文本试听限制 1600 token（约 1200 字）
2. **音频格式**: 返回 WAV 格式（44100Hz, 16bit, mono）
3. **提供商支持**: 
   - DashScope: 完整支持（24 个音色）
   - CosyVoice: 接口预留，音色列表待补充
4. **API Key 验证**: 拉取音色列表时会轻量验证 Key 有效性（调用 /models 端点）

## 后续优化

- [ ] CosyVoice HTTP API 直连实现
- [ ] 音色克隆功能（上传音频生成自定义音色）
- [ ] 音色评分/推荐系统
- [ ] 批量试听功能
- [ ] 音色对比功能（并排播放两个音色）

## 相关文件

- 后端: `stars/apis/tts_voices.py`
- 路由: `stars/admin.py` (第 87-89 行)
- 前端 UI: `pages/admin/index.html` (第 893-940 行)
- 前端逻辑: `pages/admin/app.js` (第 1977-2041 行)
- 前端样式: `pages/admin/style.css` (末尾)

## 版本信息

- 功能版本: v4.8.130
- 提交时间: 2026-09-09
- 关联 Commit: 待推送
