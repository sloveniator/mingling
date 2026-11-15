# 命灵真实环境回归矩阵

版本基线：v4.8.30

> 本矩阵区分“仓库内自动测试”和“真实宿主验收”。前者可由 CI 执行，后者必须在实际 AstrBot、Provider 和消息平台环境执行，不能用 stub 代替。

## A. AstrBot 宿主矩阵

| 场景 | 验收动作 | 预期 |
|---|---|---|
| 最低支持版本 | 安装插件、加载所有 Star | 无导入异常，Core 只初始化一次 |
| 当前支持版本 | 重启 AstrBot、打开管理页 | 页面加载正常，37 个 API 路由注册一次 |
| 重复加载/热重载 | 重载插件两次 | 无重复 Cron、无重复 Web API、无重复回复钩子 |
| 插件卸载 | 卸载插件 | 记忆/对话数据先刷盘，HTTP session 和后台任务释放 |
| 管理页未认证访问 | 直接访问插件 API | 必须由 AstrBot 宿主阻断或要求管理员会话；若宿主不保证，禁止公网暴露 |

## B. Provider 与外部能力矩阵

| 能力 | 拉取模式 | 自定义直连 | 失败场景 | 预期 |
|---|---|---|---|---|
| Provider LLM | 指定 Provider + 默认模型 | 不适用 | Provider 不存在、旧版不支持 model 参数 | 返回结构化失败，不阻断主对话 |
| TTS | AstrBot TTS provider | `/audio/speech` | 401/404/超时/空音频 | 诊断页显示失败原因，日志可定位，主回复不崩 |
| STT | AstrBot STT provider | `/audio/transcriptions` | 非法音频、401、超时 | 返回结构化失败，原消息链路可继续 |
| Embedding | AstrBot Embedding provider | `/embeddings` | 维度不一致、空向量、401 | 记忆召回降级为原序/权重，不崩 |
| Rerank | AstrBot Rerank provider | `/rerank` | 返回格式异常、索引越界 | 保留原记忆顺序，不崩 |
| Vision | 主回复/指定 Provider | `/chat/completions` 多模态 | 图片 URL 失效、模型不支持视觉 | 返回文本降级或明确错误 |
| 合并转发 | 主回复/指定 Provider | `/chat/completions` | 空消息、超长消息 | 返回摘要或原消息降级 |

## C. 能力开关矩阵

逐项关闭后打开管理页并调用对应接口：

- `dream_engine_enabled=false`：`/trigger dream` 返回“梦境能力未启用”；
- `growth_engine_enabled=false`：`/trigger growth` 返回“成长能力未启用”；
- `social_graph_enabled=false`：`/stats`、`/graph`、`/user` 返回明确模块未初始化；
- `conversation_inspection_enabled=false`：对话列表/统计返回明确降级；
- `token_monitor_enabled=false`：Token 统计返回未初始化；
- `proactive_enabled=false`：普通 LLM 请求不因主动引擎缺失而失败；
- `background_watch_enabled=false`：情绪消息不触发后台关注；
- `self_evolution_enabled=false`：不注册自我进化任务；
- `tts_enabled=false`：不发送语音；
- `stt_enabled=false`：不拦截/转写语音消息。

## D. 平台消息矩阵

| 平台消息 | 验收动作 | 预期 |
|---|---|---|
| 私聊文本 | 发送普通文本、危机文本、命理请求 | 主回复、危机提示、命理工具分别生效 |
| 群聊文本 | 被提及/未被提及、概率拦截 | 仅符合策略的消息进入回复链 |
| 图片消息 | 发送单图、多图、无图 URL | Vision 能力可用或明确降级 |
| 语音消息 | 发送可识别语音、空/损坏语音 | STT 成功或结构化失败 |
| TTS 发声 | 触发 `<tts>` 或长文本自动语音 | 平台适配器收到 `Record`，用户实际听到声音 |
| 合并转发 | 发送含文本、图片、空节点的转发 | 正确摘要或可解释降级 |

## E. 网络与数据边界

- HTTP 200、201、202、204：确认客户端是否按协议处理；
- HTTP 400、401、403、404、429、500：诊断页不得泄露 Key；
- 连接拒绝、DNS 失败、超时、非 JSON 响应：统一结构化错误；
- KV JSON 损坏、单用户数据损坏：跳过坏记录，不影响整页；
- 用户超过 200：管理列表必须截断并返回 `truncated`；
- 空模型列表、重复模型、未知模型类别：页面可继续手输模型名；
- API Key 已保存后重新打开页面：接口只返回掩码，不能回填明文。

## 当前自动化覆盖

- 仓库内 pytest：覆盖 Stub AstrBot、管理 API、Provider 解析、TTS/STT/Embedding/Rerank、配置校验和降级逻辑；
- `tests/test_diagnostics_api.py`：覆盖能力状态、配置诊断、Key 脱敏、TTS live-request 结构化结果；
- `tests/test_config_surface.py`：覆盖 schema 与静态前端字段、动态 Provider 字段、休息闸门控件；
- 真实宿主认证、真实外部 Provider、真实平台 `Record` 发声仍需在部署环境按上表执行。

## 发布验收顺序

1. 先执行仓库全量 pytest、ruff、前端语法检查；
2. 在测试 AstrBot 实例安装 v4.8.30；
3. 依次执行管理页诊断、Provider/TTS/STT/Embedding/Rerank 测试；
4. 再执行私聊、群聊、图片、语音、TTS、合并转发矩阵；
5. 最后逐项关闭能力，确认管理 API 降级和主回复不中断；
6. 任何一项失败都记录 AstrBot 版本、Provider ID、HTTP 状态、平台类型和脱敏日志后再发布。
