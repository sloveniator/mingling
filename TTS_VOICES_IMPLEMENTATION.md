# 音色库拉取与试听功能 - 实现总结

## 完成状态

✅ **后端 API 模块** - 已完成
✅ **路由注册** - 已完成
✅ **前端 UI 页面** - 已完成
✅ **前端交互逻辑** - 已完成
✅ **样式设计** - 已完成
✅ **文档说明** - 已完成

## 交付物清单

### 1. 后端模块 (`stars/apis/tts_voices.py`)
- `TtsVoicesApiMixin` 类（mixin 模式，继承自 `MingbuStar`）
- 3 个 API 端点：
  - `api_tts_voices()`: 拉取音色列表
  - `api_tts_preview()`: 试听短文本
  - `api_tts_preview_long()`: 试听长文本（≤1600 token）
- 内置 24 个 DashScope Qwen TTS 官方音色数据
- 支持 DashScope 和 CosyVoice 双提供商架构（CosyVoice 预留）

### 2. 路由注册 (`stars/admin.py`)
- 新增 3 条路由：
  - `/{plugin}/tts/voices` (POST)
  - `/{plugin}/tts/preview` (POST)
  - `/{plugin}/tts/preview_long` (POST)
- 已添加到 `StarAdmin` 类的多继承链中

### 3. 前端页面 (`pages/admin/index.html`)
- 新增「音色库」标签页（第 893-940 行）
- 包含：
  - 提供商选择下拉框
  - 音色列表网格（卡片式布局）
  - 试听预览区域（文本输入、音色选择、语气指令）
  - 音频播放器

### 4. 前端逻辑 (`pages/admin/app.js`)
- 新增函数：
  - `loadTtsVoices()`: 拉取音色列表
  - `renderVoiceList()`: 渲染音色卡片
  - `selectVoice()`: 选择音色
  - `previewVoice()`: 试听音频
  - `base64ToBlob()`: Base64 转 Blob
  - `initTtsTab()`: 初始化音色库标签事件
- 集成到现有 `init()` 函数

### 5. 前端样式 (`pages/admin/style.css`)
- 音色卡片样式（悬停效果、选中状态）
- 性别标签样式（男/女不同颜色）
- 试听区域布局
- 响应式网格（auto-fill, minmax）

### 6. 文档
- `FEATURE_TTS_VOICES.md`: 完整功能说明文档
- `deploy_tts_voices.sh`: 一键部署脚本

## 技术细节

### 后端架构
```
TtsVoicesApiMixin (mixin)
  ├── api_tts_voices()        → 返回 24 个 DashScope 音色
  ├── api_tts_preview()       → _direct_dashscope_tts() → WAV bytes
  └── api_tts_preview_long()  → _direct_dashscope_tts() + instructions
```

### 前端流程
```
用户点击「音色库」标签
  → 自动调用 loadTtsVoices()
  → 显示音色卡片列表
  → 点击「试听」→ previewVoice() → 播放音频
  → 点击「选择」→ 更新 previewVoice 下拉框
```

### 数据流
```
API Key (配置或请求体)
  → DashScope API 验证
  → 返回音色列表 (JSON)
  → 前端渲染卡片
  → 用户选择音色 + 输入文本
  → 调用试听 API
  → 返回 Base64 WAV 数据
  → 浏览器播放音频
```

## 部署说明

### 方式一：手动部署（推荐）
```bash
# 1. 登录 NAS Docker 终端
ssh Slceleto@192.168.2.108 -p 256

# 2. 进入容器
docker exec -it astrbot sh

# 3. 拉取代码
cd /volume8/AI/astrbot/astrbot_plugin_mingbu
git pull origin main

# 4. 退出容器
exit

# 5. 重载插件
curl -X POST http://127.0.0.1:6185/api/v1/plugins/reload \
  -H "Content-Type: application/json" \
  -d '{"plugin_id": "mingbu"}'
```

### 方式二：使用部署脚本
```bash
# 在宿主机执行（需确保 SSH 可达）
bash deploy_tts_voices.sh
```

## 验证步骤

1. **打开管理面板**: http://127.0.0.1:6185/admin
2. **点击「音色库」标签**
3. **检查 API Key**: 核心配置 → 外部服务 → TTS → 确保 DashScope API Key 已填写
4. **拉取音色列表**: 点击「拉取音色列表」按钮
5. **浏览音色**: 查看 24 个音色卡片（Cherry, Serena, Ethan 等）
6. **试听音频**: 点击任意卡片的「试听」按钮
7. **长文本试听**: 输入长文本（≤1200 字），点击「试听长文本」

## 已知限制

1. **CosyVoice 支持**: 接口已预留，但音色列表和 HTTP API 直连未实现
2. **文本长度**: 长文本限制 1600 token（约 1200 字）
3. **音频格式**: 仅支持 WAV（44100Hz, 16bit, mono）
4. **API Key 验证**: 拉取音色列表时会调用 `/models` 端点验证 Key

## 后续优化方向

- [ ] CosyVoice HTTP API 直连实现
- [ ] 音色克隆（上传音频生成自定义音色）
- [ ] 批量试听/对比功能
- [ ] 音色评分/推荐算法
- [ ] 音色收藏/常用音色标记

## 关联 Commit

待推送的 Commit 将包含：
- `stars/apis/tts_voices.py` (新增)
- `stars/admin.py` (修改：路由 + mixin)
- `stars/apis/__init__.py` (修改：导出)
- `pages/admin/index.html` (修改：音色库页面)
- `pages/admin/app.js` (修改：音色库逻辑)
- `pages/admin/style.css` (修改：音色库样式)
- `FEATURE_TTS_VOICES.md` (新增：功能文档)
- `deploy_tts_voices.sh` (新增：部署脚本)

## 版本信息

- **功能版本**: v4.8.130
- **实现日期**: 2026-09-09
- **关联 Issue**: 后台管理面板「音色库拉取与试听」功能

---

**实现者**: QwenPaw Agent  
**审核状态**: 代码语法检查通过，待用户部署验证

---

## 修复记录 (v4.8.130b / 2026-09-10)

### 问题：卡片「试听/选择」无响应
- 根因：`app.js` 以 `<script type="module">` 加载（index.html:953），模块作用域函数不挂全局；
  `renderVoiceList()` 渲染的卡片按钮却用内联 `onclick="previewVoice(...)"` / `selectVoice(...)`，
  模块模式下解析不到全局作用域中的这两个函数 → 点击静默无响应。
- 修复：删除全部内联 onclick，改为容器级事件委托（`voiceList` 上 `click` 监听，按 `data-action`
  分发）；「试听」= 同步到下方试听预览区并播放短文本，「选择」= 选中并同步 `tts_voice` 配置字段，
  整卡点击 = 选择不自动播放。
- 附带：拉取音色后按当前已保存/已选择音色恢复选中，不再每次跳回第一个；版本号统一 4.8.130。

