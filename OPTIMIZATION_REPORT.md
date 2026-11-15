# astrbot_plugin_mingbu 架构分析与优化报告

> 分析基线：`6cd9182`（v4.8.0）· 交付版本：v4.8.1
> 业务代码 11,382 行（不含测试）· 测试 209 → 253 项（第六节深化后达 556）· ruff 29 → 0
> 验证环境：Python 3.9.25 与 3.13 双版本实跑，结果一致

---

## 一、架构全貌

### 1.1 技术框架

| 维度 | 选型 |
|---|---|
| 宿主 | AstrBot 4.26+ 插件（`Star` 子类） |
| 扩展点 | `@filter.on_llm_request` 钩子、`@filter.llm_tool` 函数调用、`@filter.command`/`regex` 指令、Plugin Pages Web API、`cron_manager` 定时 |
| 持久化 | AstrBot KV Store（`get_kv_data`/`put_kv_data`，值为 JSON 字符串） |
| 网络 | `aiohttp`（连接池复用 + SSE 流式解读） |
| 质量 | pytest / pytest-asyncio / ruff |

### 1.2 分层结构

```
main.py                插件入口，装配各 Star
  └─ core.py           MingbuCore 单例（共享内核）+ MingbuStar 基类 + HookChain
       ├─ stars/       按职能拆分的 Star：admin(Web API) / tools(LLM 工具) /
       │               collectors(采集) / enhancements(TTS·STT) …
       ├─ 拟人层        memory_core · persona · social · dream · growth ·
       │               proactive · background_watch · conversation_inspector
       ├─ 命理层        client(后端 API) · silent_divination(暗盘) · astrology ·
       │               tarot · disguise_guard(禁用词护栏)
       └─ 支撑层        kv_store · provider_router · formatters · token_monitor
```

**核心设计**：`MingbuCore` 单例持有全部引擎，各 `Star` 通过 `MingbuStar.__getattr__`
动态代理访问，避免了多 Star 之间重复初始化与状态割裂。这是本项目最好的一个决定。

### 1.3 消息主链路

```
用户消息
 → on_llm_request
   → 危机检测（最高优先级闸门）
   → 后台关注·情绪触发 / 休息闸门
   → 状态记录：活跃索引 · 关系 · 记忆写入 · 对话日志
   → HookChain: Crisis → Persona → Character → Counseling → Blessing
 → LLM
 → 回复采集（对话日志 · 人格情绪微调）
```

### 1.4 特色机制

- **暗盘（silent divination）**：命理计算结果不以「算命」面貌出现，经
  `weave_insights_into_prompt` 转译为「直觉」织入提示词，并由 `disguise_guard`
  做禁用词扫描兜底。
- **记忆生命周期**：写入 → 检索（可选 rerank）→ 惰性衰减 → 巩固，情感权重
  影响衰减速率（负面衰减快 = 情感愈合，正面衰减慢 = 留住美好）。
- **对话权重评估**：情感/亲密/自我披露/重要话题四类关键词 + 长度/表达强度/
  私聊加成，超阈值自动入记忆库。

---

## 二、发现并修复的缺陷

### P0 — 记忆写入链路完全断裂

`memory_core.add_episode` / `add_emotion` 的实现：

```python
return await self.add(user_id, EPISODIC, content,
                      importance=kw.get("importance", 0.45), **kw)
```

当调用方**显式传入** `importance` 时，它既在 `kw` 里、又被单独展开，
必然抛 `TypeError: got multiple values for keyword argument 'importance'`。

实际调用方 `core.py:840` 与 `stars/collectors.py:70` 恰恰都显式传了。
后者外层有 `except` 兜底，**异常被静默吞掉**——所以测试全绿、日志干净，
但情景记忆一条都没写进去。这是整个插件最核心的能力。

```python
# 修复
async def add_episode(self, user_id, content, **kw):
    kw.setdefault("importance", 0.45)
    return await self.add(user_id, EPISODIC, content, **kw)
```

> 该 Bug 是在跑热路径基准脚本时暴露的——静态阅读和现有测试都没能发现它。

### P1 — 记忆衰减永不生效

两个问题叠加：

1. `_last_decay_ts` 声明为**类属性**，所有实例共享同一份 dict；
2. 衰减检查只写在 `_load()` 的「缓存未命中」分支，而 `decay_all()` 会先
   `_load()` 把用户塞进缓存 → 之后永远命中缓存 → 衰减分支再也走不到。

结果：记忆强度恒定不衰减，「遗忘」这一拟人化设计事实上不存在。

修复：改为实例属性，抽出 `_apply_pending_decay()`，命中与否都检查。

### P1 — `list_conversations()` 恒返回空

```python
all_logs = []
for target_uid in target_uids:
    logs = await self._load(target_uid)   # 结果直接丢弃

all_logs.sort(...)                        # 对空列表排序
```

循环体没有把 `logs` 收集进 `all_logs`。同时 `search`/`msg_type`/
`learned_only`/`gid` 四个筛选参数**声明了但完全没用**。管理面板的对话列表
页面因此永远是空的。已补齐收集与全部筛选逻辑。

### P1 — `api_user_detail` 必然 500

```python
{"type": m.type, "content": m.content[:120], "ts": m.ts}
```

`Memory` 对象只有 `created_at`，没有 `ts`。任何有记忆的用户，
点开详情就是 `AttributeError`。

### P1 — 「最近活动」永远显示同一批人

```python
for u in uids[:10]:      # 先截断
    ...
items.sort(key=timestamp, reverse=True)   # 再排序
```

先按索引顺序取前 10 个用户、再按时间排序——索引顺序与活跃度无关，
所以这个面板从上线起就一直显示相同的几个人。改为先在受限范围内取全量
`last_interaction`，再排序截断。

### P2 — 对话 id 跨重启失效

`ConversationLog.id` 用内置 `hash(content)` 生成后缀。CPython 对 str 的
hash 受 `PYTHONHASHSEED` 随机化，**进程重启后同一内容算出的 id 不同**，
所有已持久化的 id 引用（学习标注、记忆抽取标记）随之失效。

改用 `zlib.crc32`，并加了一个跨进程子进程测试钉死这个性质。

### P2 — uid 解析截断，操作会作用到别人头上

`log_id.split("_")[0]` 假设 uid 不含下划线。但微信是 `wxid_xxx`、
Telegram 常见 `tg_123`——首段只能取到 `wxid`，于是
detail / learn / unlearn / **delete** 全部定位到错误用户。

改用 `rsplit("_", 2)` 从右侧剥掉固定的时间戳与哈希两段。

### P2 — 危机提示词重复注入

`_on_llm_request_body` 与 `CrisisHook` 各自调了一次 `detect_crisis`、
又各自 `append` 了一次提示词。同一次请求收到两份危机引导，浪费 token，
且两段措辞略有差异的强指令会互相削弱模型的遵循度。

改为：检测只在外层做一次（闸门逻辑需要），注入统一交给 `CrisisHook`。

### P2 — 发布元数据三处不一致

`_version.py` = 4.8.0，`metadata.yaml` = 4.7.4，README 徽章 = 4.7.4，
且 README 没有 v4.8.0 的变更条目，徽章锚点 `#版本迭代` 也对不上实际
标题 `## 版本演进`。AstrBot 插件管理器读 metadata.yaml、Web 面板读
`_version.py`，用户会看到两个不同版本号。

已全部对齐，并新增 `test_metadata_consistency.py` 让漏项在 CI 直接失败。

### P2 — 一个假测试

`test_draw_different_seed_differs` 的断言是 `len(r1["cards"]) == 3`——
即使 seed 被完全忽略也照样通过，测试名与实际断言完全脱节。
改为跨 10 个 seed 统计去重结果数（实测确认 seed 真的生效）。

---

## 三、性能优化

### 3.1 主要瓶颈：KV 全量读写

KV Store 的值是**整个 JSON 字符串**，没有部分更新。原实现每条消息都要
「反序列化整份数据 → 追加一条 → 全量序列化写回」，成本随已存条数线性增长。

对话日志上限 5000 条、一问一答触发两次，是全插件最重的热点。

### 3.2 方案：写回缓存 + 防抖落盘

- 读：命中进程内缓存则零 IO；
- 写：只更新缓存 + 打脏标记；
- 落盘：静默 2~3 秒后合并写一次；
- `terminate` 时 `flush_all()` 强制落盘，防止数据丢失；
- 缓存淘汰跳过脏用户；落盘失败保留脏标记以便重试；
- 缓存条目缺失时**跳过写入**而非写空数组（否则会清空已有数据）。

### 3.3 实测（600 条消息，同一基准脚本）

| 指标 | 优化前 | 优化后 | 变化 |
|---|---|---|---|
| **对话日志** 耗时 | 18.76 s（31.3 ms/条） | 0.35 s（0.58 ms/条） | **54× 更快** |
| KV 读 / 写 | 1801 / 2000 | 3 / 0 | — |
| 序列化写出 | 240.3 MB | ~0 MB | **-240 MB** |
| 反序列化读入 | 229.8 MB | ~0 MB | **-230 MB** |
| **记忆写入** 耗时 | 1.20 s（2.0 ms/条）※ | 0.14 s（0.24 ms/条） | **8.6× 更快** |
| KV 写 | 600 | 0 | — |
| 序列化写出 | 48.7 MB | ~0 MB | **-49 MB** |

※ 优化前的 `add_episode` 直接抛 TypeError，此行是绕过损坏包装、
直接调 `add()` 测得的，用于公平比较序列化开销。

**单条消息在 KV 层从 O(已存条数) 降为 O(1) 摊销。**

### 3.4 其他性能修复

- `_remember_user` 在**每条消息**上都读一次 KV 并对整个用户列表做 O(N)
  成员判断。改为内存集合镜像，命中即零 IO，只有新用户才回写。
- 管理面板 `api_stats` / `api_users` / `api_recent_activity` 的 N+1：
  逐个 `await` 拉关系和档案。改为带并发上限（16）的 `asyncio.gather`，
  并统一加 `MAX_LIST_USERS = 200` 上限，响应体附带 `truncated` 标记。
- `extract_high_weight_to_memory` 在循环内反复 `_load` 整份日志，
  改为一次加载 + 建 id 索引。

---

## 四、可维护性与扩展性

### 已改进

- **HookChain 支持短路**：原 `run()` 直接丢弃返回值，各钩子里的
  `return True` 形同虚设，无法表达「危机场景下不要再叠加祝福/玩笑注入」
  这类优先级。现在返回 `False` 即中断后续钩子（当前无钩子返回 False，
  行为不变，但扩展点真正可用了）。
- **消除歧义变量名** `l`（易与 `1`/`I` 混淆）、清理死代码
  （conftest 里一个未使用的 `import _imp` 和三段自相矛盾的「换个思路」注释）、
  修正 `StarAdmin` 「7 个 Web API 路由」的过时 docstring（实为 31 个）。
- **ruff 从 29 项告警清零**。

### 建议改进清单（截至本轮已全部落地）

> 本节原为「仍建议改进（未实施，避免大范围重构风险）」。第六节逐条实施后，
> 5 项建议已全部完成，故在此统一标注去向，保留原文以便追溯判断依据的演变。

1. ~~**`counseling.py`(1108) / `core.py`(1083) 仍偏大**。
   `core.py` 里 `MingbuCore`、`MingbuStar`、5 个 Hook、`HookChain` 混在一起，
   建议把钩子族拆到 `hooks/` 子包，`core.py` 只留内核与基类。~~
   → **已实施，见 6.7 / 6.8 / 6.10**（钩子族拆出后 `core.py` 降至 927 行，
   6.10 再把常量与 8 组横切能力分别下沉到 `constants.py` 与 `star_mixins/`，
   最终 **390 行**，只剩内核与薄基类；`counseling.py` 已拆为 `counseling/` 子包）。

2. ~~**KV 访问缺少统一抽象**。`kv_store.KVStoreBase` 已存在，但
   `memory_core` / `conversation_inspector` / `core` 都在直接调
   `plugin.get_kv_data`，各自实现了一遍缓存与防抖。建议把「写回缓存 +
   防抖落盘 + flush」下沉为 `KVStoreBase` 的通用能力，各模块继承即可，
   避免第三处、第四处再抄一遍。~~
   → **已实施，见 6.2**（新增 `kv_cache.WriteBehindScheduler`，
   `memory_core` / `conversation_inspector` 各自重复的防抖调度已下沉复用）。

3. ~~**异常处理过于宽泛**。大量 `except Exception: pass` 会掩盖真实故障
   （本次 P0 就是这样藏了很久）。建议至少降级为
   `logger.debug(..., exc_info=True)`，并对「静默失败」的关键路径
   （记忆写入、对话记录）加计数器暴露到 `api_stats`，让故障可观测。~~
   → **已实施，见 6.3**（新增 `metrics.py` 故障计数器，静默 `except` 已收敛为
   带 `exc_info` 的日志 + `record_failure`，并经 `api_stats.failures` 对外暴露）。

4. ~~**无 CI**。已有 253 项测试和干净的 ruff 基线，建议加一个
   GitHub Actions 跑 `ruff check` + `pytest`，否则这次清理的成果
   很快会重新劣化。~~
   → **已实施，见 6.1**（`.github/workflows/ci.yml`，push / PR 触发）。

5. ~~**热路径基准应纳入回归**。`tests/bench_hotpath.py` 目前是手动脚本。
   建议加一个宽松阈值的 pytest 用例（如「600 条消息 KV 写 < 50 次」），
   防止有人不小心把防抖策略改回同步全量写。~~
   → **已实施，见 6.1**（`tests/test_hotpath_regression.py`，计数式护栏）。

---

## 五、本次变更清单

**修改**：`memory_core.py` `conversation_inspector.py` `core.py`
`stars/admin.py` `metadata.yaml` `_version.py` `README.md`
`provider_router.py` `self_evolution.py` `stt.py` `token_monitor.py`
`stars/enhancements.py` `tests/conftest.py` `tests/test_tarot.py`

**新增**：
- `tests/test_conversation_inspector.py`（30 项：uid 解析、id 稳定性、
  列表筛选分页、缓存与落盘、按 id 的写操作、权重与抽取、容错）
- `tests/test_hook_chain.py`（9 项：危机注入去重、链路短路与异常隔离）
- `tests/test_metadata_consistency.py`（5 项：版本号三处一致性）
- `tests/bench_hotpath.py`（热路径基准脚本）

**验证**：253 项测试全通过 · ruff `All checks passed`

---

## 六、本轮深化（架构重构 / 可观测性 / UI 验证）

> 在上一轮「253→329 项测试 + 性能修复 + 2 个真实后端 Bug」基础上，
> 依据报告「仍建议改进」逐条落地，并完成本地体验式验证与 UI 打磨。

### 6.1 测试与 CI 基建（报告建议 #4 / #5）

- **Web API 路由测试**：新增 `tests/test_admin_api.py`（63 项），覆盖 31 个
  `stars/admin.py` 路由（此前零覆盖）。顺带修复了 `api_collection_all`
  （漏 `await list_presets()`、取 `p["icon"]` 实为 `avatar`、所有预设写死
  `unlocked=True`）、`api_recent_activity`（名称截断为「wxid」、排序键类型错误）。
- **热路径性能回归**：新增 `tests/test_hotpath_regression.py`（8 项，计数式护栏，
  非计时，CI 稳定），锁死「防抖落盘」不被无意改回同步全量写；并修复其导入
  （`MAX_MEMORIES_PER_USER` 是 `MemoryCore` 类属性，非模块导出）。
- **CI**：新增 `.github/workflows/ci.yml`，push/PR 自动跑 `ruff check .` + `pytest tests/`。

### 6.2 架构重构（报告建议 #2）

- 将「写回缓存 + 防抖落盘」的**机械调度逻辑**抽为 `kv_cache.WriteBehindScheduler`
  （`schedule / _debounced / cancel_all`），`memory_core` 与 `conversation_inspector`
  共用，各自保留缓存/脏标记属性以维持测试契约。防抖时长通过 `debounce_fn()`
  延迟取值，兼容测试里改 `FLUSH_DEBOUNCE_SEC` 的用法。
- 全量测试 329 passed、ruff 0。

### 6.3 异常处理与可观测性（报告建议 #3）

- 新增 `metrics.py`：`record_failure / get_failure_counts / reset_failure_counts`。
- `core.py` 中 4 处「`except Exception: pass`」静默失败（对话记录、情景记忆写入、
  后台关注情绪触发、休息闸门、群 slang 学习）降级为 `logger.debug(..., exc_info=True)`
  + 计数器；其中「对话记录 / 记忆写入」升为 `logger.warning`（核心数据路径）。
- `api_stats` 新增 `failures` 字段，管理面板可直接看到关键路径故障计数。

### 6.4 后端状态数据补齐（前端环形进度条长期不更新之根因）

- `api_stats` 原**从不返回** `bot_status / total_interactions / total_memories`，
  导致前端 4 个环 + 2 张总量卡永远显示占位值。现补齐：
  - `bot_status{mood,energy,social,intimacy,name,traits}`（取自 `PersonaState`，
    `intimacy` 取关系均分）；
  - `total_interactions`（关系互动数求和）、`total_memories`（受并发上限保护的
    `memory.stats` 聚合）。
- 新增 `_bot_status()` 助手。

### 6.5 前端缺陷修复与体验打磨（用户明确诉求）

逐项定位并修复 6 项缺陷（详见 `UI_VERIFICATION_REPORT.md`）：

1. `updateRing` 按 `data-value==value` 查找环 → 改为按元素 id 定位，非默认值也能更新；
2. `botSocial` / `botIntimacy` 环从未被驱动 → 后端补齐 `bot_status` + 前端补调 4 环；
3. `escapeHtml(x).slice(0,100)` 把 HTML 实体从中间切断 → 改为先 `slice` 再 `escapeHtml`；
4. 列表 `data-uid` 未转义 → `escapeHtml(u.uid)`；
5. `init()` 首屏 4 个独立请求串行 → `Promise.all` 并发；
6. 原生 `prompt`/`confirm` → 自定义模态框 `openModal/showConfirm/showPrompt`
   （淡入缩放动效，与面板风格统一）。

视觉增强：`fadeInUp` 列表入场 + 阶梯延迟、卡片悬停抬升柔光、按钮回弹、环形过渡缓动。

### 6.6 验证

- 本地开发服务器（`devtools/devserver.py`）+ 全路由冒烟（`devtools/smoke.py`）：
  **35/35 用例通过**，覆盖全部 31 个 Web API（含正常/异常/筛选/分页/写操作）。
- 静态资源 `index.html / app.js / style.css` 均可达（HTTP 200），`app.js` 经
  `node --check` 语法校验通过。
- 全量 pytest **329 passed**，ruff **All checks passed**。

---

### 6.7 hooks 子包拆分（报告建议 #1）

**动机**：`core.py` 尾部 921–1092 行塞着 `HookProcessor`、5 个具体钩子和
`HookChain`，与生命周期管理、事件分发、人格构建挤在同一文件里。改一个钩子
要在千行文件中定位，新增钩子更没有明确落点。

**可行性前置判断**：拆分前先确认依赖方向——6 个钩子引用的外部符号
（`detect_crisis`、`crisis_support_prompt`、`technical_guidance_prompt`、
`CounselingPrompts`、`weave_insights_into_prompt`、`weave_watch_note`）
**全部来自 counseling / silent_divination / background_watch，无一来自 core.py**；
钩子只通过传入的 `core` 参数以鸭子类型访问运行时依赖。因此
`hooks/ → core.py` 无反向依赖，**不存在循环导入**，这才使拆分风险可控。

**拆分结果**（`core.py` 1093 → **927** 行，-166）：

| 文件 | 行数 | 职责 |
|---|---|---|
| `hooks/__init__.py` | 69 | re-export + `DEFAULT_HOOK_CLASSES` + 链工厂 |
| `hooks/base.py` | 41 | `HookProcessor` 基类、`HookChain` 调度 |
| `hooks/crisis.py` | 33 | 危机支持注入（优先级最高） |
| `hooks/persona.py` | 19 | 人格 / 记忆 / 关系块注入 |
| `hooks/character.py` | 25 | 角色收集：隐藏解锁 + 好感度推荐 |
| `hooks/counseling.py` | 56 | 心理咨询钩子 + 静默洞察 + 关注注记 |
| `hooks/blessing.py` | 39 | 预言式祝福注入 |

**向后兼容**：`core.py` 以 PEP 484 显式 re-export（`X as X`）保留全部旧导入
路径，`from core import CrisisHook, HookChain` 等既有写法（含现有测试）不受影响。
该写法被 ruff 原生识别为「有意 re-export」，无需散落 `# noqa`。

**顺带的两点改进**：
- 热路径省分配：原先每次 LLM 请求都新建 1 条链 + 5 个钩子对象。因内置钩子
  均无实例状态，改为 `get_default_chain()` 返回进程内共享单例；
  `build_default_chain()` 仍可产出独立实例。测试 `test_hooks_are_stateless`
  会在有人给钩子加实例字段时立刻失败，提醒改回按需构造。
- `CounselingHook` 中残留的 `except Exception: pass`（关注注记注入）按 6.3 的
  口径收敛为 `logger.debug(..., exc_info=True)` + 新计数器 `watch_note_inject`。

**开发环境配套修复**：`devtools/astrbot_stub.py` 原先用合成 `ModuleType`
预注册 `hooks` 包，会遮蔽真实的 `hooks/__init__.py`，导致
`from .hooks import CrisisHook` 在空模块上找不到属性而转去找同名子模块并失败。
已改为交给标准导入机制加载，再补注册短名别名。

**验证**：新增 `tests/test_hooks_package.py` **48 项**，锁定三类契约——
① 向后兼容（`core.X is hooks.X`，须是同一类对象而非重复加载的副本）；
② 优先级顺序与单例安全性；③ 链语义不变（返回 `False` 中断、单钩子异常隔离），
外加装配后的真实运行集成测试。
全量 pytest **377 passed**、ruff **All checks passed**、全路由冒烟 **35/35**。

### 6.8 counseling.py 拆分（报告建议 #1 收尾）

**动机**：`counseling.py` 高达 1108 行，混着关键词库、危机识别、咨询流派工具箱、
情绪追踪状态机、提示词模板、多轮会话六类内容。改任意一处都要在千行文件里翻找，
新增「朋友式关心」相关能力也没有清晰落点。报告曾将其标为「提示词模板与情绪追踪器
强耦合、收益低于风险」，但本轮在确认依赖方向为 DAG（无环）后实际落地：

- `emotion_lexicon` 纯数据（情绪 / 命理意图关键词），无任何依赖；
- `crisis` 危机识别 + 红线提示词，独立；
- `therapy` 流派 / 技能 / 边界守则，纯数据；
- `tracker` 情绪信号与状态机，仅依赖 `emotion_lexicon`；
- `prompts` 提示模板，依赖 `therapy` / `tracker` / `emotion_lexicon`；
- `session` 多轮会话，依赖 `prompts` / `therapy`。

**拆分结果**（`counseling.py` 1108 行 → `counseling/` 子包，7 个文件）：

| 文件 | 行数 | 职责 |
|---|---|---|
| `counseling/__init__.py` | 70 | re-export 全部对外符号 |
| `counseling/emotion_lexicon.py` | 84 | 情绪 / 命理意图关键词库 |
| `counseling/crisis.py` | 138 | 危机识别与红线提示词 |
| `counseling/therapy.py` | 195 | 流派 / 技能 / 边界守则 |
| `counseling/tracker.py` | 258 | 情绪信号记录与状态机 |
| `counseling/prompts.py` | 271 | 心理咨询式提示模板 |
| `counseling/session.py` | 161 | 多轮疏导会话与管理 |

**向后兼容**：`__init__.py` 以 PEP 484 显式 re-export（`X as X`）+ `__all__` 保留
全部旧导入路径，`from counseling import CounselingPrompts, EmotionalTracker,
detect_crisis` 与 `from .counseling import ...` 均保持可用；`core.py` / `hooks/*` /
`stars/commands.py` / `stars/tools.py` 等既有写法无需改动。

**开发环境配套**：`devtools/astrbot_stub.py` 的包预加载逻辑原只为 `hooks` 特判，
本轮泛化为「扫描目录下的子包并预加载 + 注册短名别名」（`_TOP_LEVEL_PACKAGES` +
`_PluginRemapFinder` 支持目录型 spec）。这确保测试里的 `import counseling` 与源码里的
`from .counseling import` 指向**同一个**模块对象，避免「同一个类被加载成两份」的隐患
（hooks 拆分时踩过的坑）。

**验证**：全量 pytest **377 passed**（与拆分前一致）、ruff **All checks passed**。

> 注：`devtools/split_counseling.py` 为本次拆分的一次性脚本（基于 AST 按顶层节点
> 连续切片导出），拆分后已无 `counseling.py` 源文件，故该脚本不可重复运行，仅留作
> 变更追溯。

### 6.9 收尾：兑现 Python 版本承诺 + 入库卫生

**问题**：`pyproject.toml` 声明 `requires-python = ">=3.9"`（AstrBot 实际运行环境），
但 6.1 建的 CI 只在 Python 3.11 上跑。也就是说**下限从未被验证**——任何人引入
`match` 语句、PEP 604 运行时注解或 `datetime.UTC` 之类 3.10+ API，CI 都会放行，
而插件到了真实环境才崩。

**先验证，再承诺**。用 `ast.parse(src, feature_version=(3, 9))` 全量扫描语法兼容性，
并单独检查「未加 `from __future__ import annotations` 却在函数签名里用 `X | Y`」
（这类注解在 3.9 会于**导入时**抛 `TypeError`，静态语法检查抓不到），
再补一轮 3.10+ 运行时 API 的关键字扫描（`pairwise` / `datetime.UTC` /
`asyncio.timeout` / `ExceptionGroup` / `tomllib` / `anext` / `StrEnum` 等）。
三项结果**均为零命中**，确认代码真实兼容 3.9，才把 CI 改为矩阵：

```yaml
strategy:
  fail-fast: false
  matrix:
    python-version: ["3.9", "3.11"]
```

至此 `requires-python` 从一句元数据声明变成了**每次 push 都会被验证的约束**。

**入库卫生**：`devtools/.devdata.json`（33 KB 本地会话状态）与 `devtools/.server.log`
是开发服务器的运行态产物，已加入 `.gitignore`，避免把本机调试数据带进仓库。

### 6.10 core.py 拆分：内核与 Star 基类分家（报告建议 #1 终章）

**问题**：`core.py` 927 行里挤着三种东西——共享常量、`MingbuCore`（引擎单例容器）、
`MingbuStar`（供 5 个 Star 继承的基类，含 8 组横切能力）。`MingbuCore` 管的是「谁持有
哪个引擎」，`MingbuStar` 管的是「消息进来怎么处理」，两者生命周期与变更频率都不同，
放在一起的直接后果是：想加一个群聊行为，得在近千行文件里翻到正确的方法群；而
`background_watch.py` 因为不敢从 `core` 导入（怕引入 Star 依赖），干脆**手抄了一份
`MINGPAN_METHODS` / `BUGUA_METHODS`**——常量副本一旦漂移，后台巡检和主链路对「什么算
命盘方法」的判断就会不一致，而且不会报错。

**拆分依据是依赖方向，不是行数**。确认 `constants ← star_mixins ← core ← main/stars/*`
单向无环后落地：

| 产物 | 行数 | 职责 |
|---|---|---|
| `constants.py` | 76 | 全部共享常量（KV 键名、方法枚举、超时、预编译正则） |
| `star_mixins/__init__.py` | 69 | 8 个 mixin 的 re-export + `STAR_MIXINS` 组合顺序 |
| `star_mixins/llm_hook.py` | 130 | LLM 请求钩子链 |
| `star_mixins/persona_ctx.py` | 133 | 人格上下文注入 |
| `star_mixins/profile.py` | 112 | 用户档案读写 |
| `star_mixins/relations.py` | 77 | 关系度与称呼 |
| `star_mixins/conversation.py` | 68 | 对话上下文 |
| `star_mixins/llm.py` | 62 | LLM 文本调用 |
| `star_mixins/group.py` | 54 | 群聊行为 |
| `star_mixins/health.py` | 35 | 健康档案 |
| `core.py` | **390**（原 927） | 仅剩 `MingbuCore` + 薄基类 `MingbuStar` |

`MingbuStar` 现在只有 `__init__` 与 `__getattr__`（代理到 `MingbuCore` 取引擎），
能力全部由 mixin 组合而来。mixin 一律不 import `core`，靠鸭子类型经 `self.<engine>`
取依赖——既断开循环依赖，也让每个 mixin 能被单独导入和测试。

**顺手还掉的债**：`background_watch.py` 那份手抄常量已删除，改为
`from .constants import MINGPAN_METHODS as _MINGPAN_METHODS`（保留下划线别名，
下方 5 处引用零改动）。过程中还发现 `_BUGUA_BRIDGE_TO_LIUYAO` **自定义起从未被引用**
——梅花桥接实际由 `if method == "qimen": ... else: api.liuyao(...)` 的 else 分支兜住，
故未一并导入，已在原处加注说明。

**验证**：新增 `tests/test_star_mixins_package.py` **117 项**，锁死拆分不变量——方法面
不缩水（30 成员快照）、确实搬走（`__module__` 落在 `star_mixins`）、无同名撞车、
MRO 顺序（mixin 必须排在 `Star` 之前）、`core` 基类列表恒等于 `STAR_MIXINS`、
mixin 内不出现 `from ..core import`（无环）、mixin 无 `__init__`（无状态）、
以及常量经两条路径取到的是**同一个对象**。

> 注：`devtools/split_star_mixins.py` 为一次性拆分脚本，按 AST 的
> `lineno` / `end_lineno` 精确切片，保证搬运是字节级等价而非人工重抄。

### 6.11 CI 的 3.9 真跑：从「静态推断兼容」到「实测通过」

6.9 只做了静态扫描就把 3.9 写进 CI 矩阵。本轮拿 Python 3.9.25 真跑了一遍，
抓到一个静态检查**原理上抓不到**的问题：

```python
# 模块级
_lock = asyncio.Lock()      # 3.10+ 无事件循环也能构造；3.9 会去 get_event_loop()
```

`asyncio.Lock/Event/Semaphore` 在 3.9 构造时就绑定当时的事件循环。在模块导入阶段
（还没有 running loop）构造，3.9 会发 `DeprecationWarning` 并绑到一个**将来不会被使用**
的 loop 上；等 `asyncio.run()` 起了新 loop 再 `await`，直接
`RuntimeError: is bound to a different event loop`。3.10 起构造不再绑定，同一份代码
毫无问题——这正是「只在 3.11 跑 CI」漏掉的那类缺陷。

**修法**：改为惰性创建（首次在协程内使用时才构造），涉及 `proactive.py`、
`self_evolution.py`、`tests/test_stress_performance.py`。新增
`tests/test_py39_compat.py` **5 项**把「不得在模块级构造 asyncio 原语」变成断言。

### 6.12 测试桩的两处静默失效（本轮最有价值的发现）

补 6.11 的护栏时，`test_background_watch_uses_shared_constants` 报了个反直觉的失败：
`background_watch._MINGPAN_METHODS is constants.MINGPAN_METHODS` 为假，但两者**值相等**。
顺藤摸下去挖出测试桩里两个长期存在、且都不会报错的缺陷。

**① 模块被加载成两份。** `devtools/astrbot_stub.py` 的 finder 在「模块已加载」分支里，
把已加载模块的 `SourceFileLoader` 原样交回 import 机制。机制的行为是
`module_from_spec` **新建一个模块对象**再 `exec_module` 跑一遍源码——看着像复用，实则
同一份代码执行两遍。后果是模块级单例整体分裂：`core._CORE` 两个内核、预编译正则两份、
常量集合等值不同一、`isinstance` 对着同名的两个类返回 `False`。测试仍会大面积通过，
只在某个恰好跨越两份副本的断言上莫名其妙地挂掉。

修法是造真别名：`_AliasLoader.create_module` 直接返回既有模块对象，`exec_module` 空实现。
同时 `_TOP_MODULE_ORDER` 是**手工维护的加载顺序表、不是全集**，新增的 `constants.py`
不在表里就没人登记短名别名——已改为按磁盘实际情况兜底回填（只登记已加载的，不主动
加载新模块，避免把 `verify_hooks.py` 这类脚本型模块也执行一遍）。
`tests/conftest.py` 与 `devtools/devserver.py` 的引导也从
`from devtools.astrbot_stub import ...`（扁平名，会先加载一份 `devtools`）改为先登记包、
再以包内全名加载，并在 bootstrap 里补了反向兜底。

**② `main.py` 和 4 个 Star 长期根本没被加载。** 桩的 `_Filter` 只实现了
`on_llm_request` / `on_message_event` 两个装饰器，而源码实际用到 5 种。于是
`stars/collectors.py` 的 `@filter.regex` 在导入期抛 `AttributeError`，异常被
`_load_module` 的 `except` 吞掉，模块从 `sys.modules` 里静静消失——**测试一片绿，
但这几个文件连"能不能 import"都没被验证过**。同类缺口还有三处：`_RemapSpec` 没继承
`has_location`，模块拿不到 `__file__`，`main.py` 里
`os.path.dirname(os.path.abspath(__file__))` 当场 NameError；`MessageEventResult`
（`stars/tools.py` 的返回标注）、`astrbot.api.message_components`（`stt.py` / TTS）
与 `LLMResponse` 三个符号桩里压根没有。

修法：`_Filter` 改用 `__getattr__` 兜住全部装饰器名；补齐
`MessageEventResult` / `message_components`（`Plain`/`Record`/`Image`/`At`）/
`LLMResponse`（`api.provider` 与 `core.provider` 两条路径指向**同一个类**，否则
`stars/enhancements.py` 的 `isinstance(resp, LLMResponse)` 会静默判否、TTS 分支走空）。
`astrbot.api.event` 与 `astrbot.api.message_components` 的组件类也刻意共用同一批对象，
对齐真实 AstrBot 的再导出关系——不然 `stt.py` 的 `isinstance(comp, _Record)` 会对着
同名的两个类返回 `False`，语音识别整条链路安静失效。

**验证**：新增 `tests/test_astrbot_stub_identity.py` **57 项**。它不针对具体模块写死
断言，而是**遍历磁盘上全部顶层模块、子包与 Star** 做全量校验：短名与包名必须解析到
同一对象、`__name__` 不被别名改写、重复导入幂等、桩必须暴露源码真正 import 的符号面、
以及「`main` 能导入且 5 个 Star 都在」。以后再加新模块，漏登记或桩缺符号会立刻被抓住，
而不是变成又一次静默失效。修复后 32 个顶层模块 + 5 个 Star **全部导入成功**（此前
`main` 与 4 个 Star 为零导入级覆盖）。

### 6.13 本轮验证汇总

| 项目 | 结果 |
|---|---|
| pytest（Python 3.13） | **556 passed** |
| pytest（Python 3.9.25，真实解释器） | **556 passed** |
| ruff check | **All checks passed** |
| 新增测试 | 117（star_mixins 契约）+ 57（桩身份契约）+ 5（3.9 兼容）= **179 项** |
| `core.py` | 927 → **390** 行 |
| 模块导入覆盖 | 32 个顶层模块 + 5 个 Star，**全部可导入** |

测试总数由 6.8 时的 377 增至 **556**，增量 179 项全部是本轮新增的护栏
（377 + 5 + 117 + 57 = 556），既有用例数量与结果均未变动——拆分是等价搬运，
不是重写。

