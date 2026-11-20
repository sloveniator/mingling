# 命灵 · AI 伴侣 — AstrBot 1V1 状态层插件（v5）

> **v5.3.0：混合语义路由（正则快速路径 + 默认模型兜底）**
> 明确说法由正则零成本秒判；正则未命中的模糊表达（如“最近提不起劲”
> “心里空落落的”“算了不说了”）再由 AstrBot 默认模型做一次轻量意图分类。
> 分类失败、超时或置信度不足时按普通聊天处理，不阻断回复。

> **v5.2.0：全自然语言交互，不再使用斜杠命令**
> 用户只需正常说话即可触发命盘、卜卦、塔罗、健康、成长、状态查看与开解：
> 例如「帮我看看八字」「抽三张塔罗」「最近睡不好」「我们关系到哪一步了」
> 「想找个人聊聊」。开解由情绪语义自动进入，并在「好多了/不想聊了/换话题」
> 时自然退出。所有命理/咨询/成长相关的 AI 生成统一走 **AstrBot 当前默认对话
> 模型**，不读取插件自有 API Key；关系、情绪、关系记忆在每条私聊消息中自动更新。
> 插件标识与目录已统一为 `mingling`；升级前请先运行 `migrate_v4_to_v5.py`。

> **技法先于角色，工程优于人设**：本插件是**拟人陪伴系统方法论的开源参考实现**——它是聊天机器人有温度、有记忆、会成长的「灵魂」，而不是一个算命工具。插件把记忆生命周期管理、人格连续性建模、关系网络识别、主动行为编排、对话权重评估、世界观转译等 12 项经过验证的工程技法，组合为一套可扩展的有「生命感」的聊天伙伴框架；命理（八字/紫微/六爻等）只作为后台静默洞察通道，把结论伪装成「直觉」织入对话，默认绝不主动以「算命」面貌出场。

[![AstrBot](https://img.shields.io/badge/AstrBot-4.26%2B-purple)](https://github.com/Soulter/AstrBot)
[![version](https://img.shields.io/badge/version-5.4.0-gold)](#版本演进)
[![license](https://img.shields.io/badge/license-MIT-green)](LICENSE)

---

## v5 功能范围与迁移

**保留**：1V1 私聊状态层（关系阶段、情绪轨迹、话题栈、未闭环话题、关系记忆）、
主动消息、管理 API（`/mingling/users`、`/mingling/timeline`）。

**自然语言功能**：状态查看、排盘、卜卦、塔罗、命盘档案、命理方法、心情、
开解、健康、成长。用户无需记任何命令，直接说出需求即可；缺少出生信息等
必要条件时，机器人会用自然语言追问。命盘/卦象由 `mingbu_api_url` 指向的
命理计算服务提供，解读与健康/成长建议由 AstrBot 默认对话模型生成。

**仍未恢复**：角色收集/切换、群聊观察、TTS/STT、暗盘洞察、梦境/预感、
News/搜索、SocialGraph、TTS 音色库与旧管理面板业务。

**配置**：v5 只读取 `CONFIG_V5.md` 列出的 28 个键；`_conf_schema.json` 仍保留旧键
以兼容历史配置，但旧键不再参与运行。`enabled` 默认 `true`。

**迁移步骤**：

```bash
# 1) 备份 AstrBot 数据目录（必须）
# 2) dry-run 查看动作
python migrate_v4_to_v5.py --data-dir ~/.astrbot
# 3) 执行迁移
python migrate_v4_to_v5.py --data-dir ~/.astrbot --apply
# 4) 重启 AstrBot，确认插件目录名为 mingling
```

> 旧 KV 按插件名隔离，脚本不自动改写；需要保留历史关系/情绪/记忆时，见
> `CONFIG_V5.md` 末尾的数据库迁移说明。

---

## 设计哲学

本项目的核心不是"做一个有特定性格的 Bot"，而是**提供一套让 Bot 产生生命感的工程方法论**。所有功能模块均可独立启用、按需组合，你可以用它搭建命理陪伴、虚拟恋人、学习伙伴、群聊管理助手等多种形态。

### 十二项核心技法

| 技法维度 | 设计原理 | 工程实现 |
|---------|---------|---------|
| **记忆工程** | 对话应当留下痕迹，且痕迹会随时间自然褪色 | 6 类记忆分层 + 重要性衰减曲线 + 夜间梦境巩固机制 |
| **人格连续** | 情绪状态有惯性，不是每次独立采样的随机数 | Izard 四维情绪空间 + 事件 nudge 扰动 + 周期性基线重置 |
| **关系识别** | 不同对象对应不同社交距离，不能一碗水端平 | 关系五阶模型 + 亲密度连续打分 + 主动行为边界控制 |
| **主动行为** | 有生命感的实体不会永远等待指令 | cron 触发 + 时机判断漏斗 + 三级强度复核 + 冷却防抖 |
| **对话权重** | 信息密度不均等，重要对话应被优先记忆 | 9 维度实时评分 + 阈值自动入记 + 权重衰减与重算 |
| **世界观转译** | 技术能力需要内化为角色世界观的一部分 | 术语替换表 + 知识库参照注入 + 语境风格改写 |
| **轻重双 AI 核** | 思考用小模型降本，回复用大模型保质 | 多 Provider 编排路由 + 任务分级调度 + Token 用量监控 |
| **休息模拟** | 响应延迟本身就是真实感的一部分 | 睡眠/午休时段闸门 + 多级醒来判断 + 醒后缓冲期 |
| **成长演化** | 长期相处应当产生可感知的变化 | 对话反思日志 + 用户画像迭代 + 里程碑事件记录 |
| **消息增强** | 消息形态与节奏比内容更能传递"人味" | 防抖合并 / 撤回感知 / 图片转述 / TTS 语音 / 打字状态 |
| **群聊观察** | 公共空间需要比私聊更复杂的社交判断 | 气氛感知 / 黑话学习 / 连续对话保持 / 互动关系图 |
| **角色收集** | 多人格切换是验证系统通用性的天然场景 | 6 预设模板 + 1 隐藏彩蛋 + 好感度阶梯解锁 + 台词触发 |

---

## 系统架构

```
                       ┌─────────────────────────────┐
                       │       消息输入（各平台）      │
                       └───────────────┬─────────────┘
                                       │
              ┌────────────────────────┼────────────────────────┐
              ▼                        ▼                        ▼
   ┌──────────────────┐   ┌───────────────────────┐   ┌──────────────────┐
   │  消息增强层       │   │   被动收集层          │   │  关系闸门        │
   │  - 防抖合并      │   │   - 出生信息          │   │  - 亲密度判断    │
   │  - 撤回感知      │   │   - 情绪信号          │   │  - 休息闸门      │
   │  - 图片转述      │   │   - 群黑话学习        │   │  - 免打扰        │
   │  - TTS 语音      │   │   - 对话权重评分      │   │                  │
   └────────┬─────────┘   └───────────┬───────────┘   └────────┬─────────┘
            │                       │                          │
            └───────────────────────┼──────────────────────────┘
                                    │
                       ┌────────────▼────────────┐
                       │     核心内核 (Core)      │
                       │  · 记忆生命周期管理      │
                       │  · 人格状态机            │
                       │  · 关系管理器            │
                       │  · 对话检视与学习        │
                       │  · Provider 编排路由     │
                       │  · Token 用量监控        │
                       └────────────┬────────────┘
                                    │
              ┌─────────────────────┼─────────────────────┐
              ▼                     ▼                     ▼
   ┌──────────────────┐   ┌───────────────────┐   ┌──────────────────┐
   │  记忆系统        │   │  主动行为引擎     │   │  成长 & 梦境     │
   │  EPISODIC        │   │  - 定时检查       │   │  - 夜间巩固      │
   │  SEMANTIC        │   │  - 强度复核       │   │  - 梦境生成      │
   │  EMOTIONAL       │   │  - 分段发送       │   │  - 成长反思      │
   │  DREAM           │   │  - 多形态输出     │   │  - 里程碑记录    │
   │  GROWTH          │   └───────────────────┘   └──────────────────┘
   │  THOUGHT         │
   └──────────────────┘
                                    │
                       ┌────────────▼────────────┐
                       │   命理后台（静默通道）    │
                       │  八字 · 紫微 · 六爻      │
                       │  合参 · 近况分析         │
                       └──────────────────────────┘
```

---

## 技法详解

### 1. 记忆生命周期技法

借鉴 memorix / iris_memory / livingmemory / angel_memory 等成熟插件的共识模式，实现完整记忆生命周期：

```
写入（importance + emotional_weight）
    ↓
检索（近期 + 重要 + 情绪相关 + 语义关联）
    ↓
衰减（decay_score 随时间下降，重要性越高衰减越慢）
    ↓
巩固（夜间梦境中对高权重记忆重放 + 强化）
    ↓
消散（decay_score < 0.02 自动遗忘）
```

**记忆类型**：
- `EPISODIC` 情景记忆 — 对话片段、发生过的事
- `SEMANTIC` 语义记忆 — 关于用户的事实、偏好、约定
- `EMOTIONAL` 情绪记忆 — 情绪快照、感受强度
- `DREAM` 梦境记忆 — 夜间巩固生成的意象叙事
- `GROWTH` 成长记忆 — 里程碑、自我觉察
- `THOUGHT` 想法记忆 — Bot 自己的念头、灵感

### 2. 对话权重评分技法

每条入站消息实时计算权重（9 维度），超过阈值自动入记：

| 维度 | 信号源 | 权重贡献 |
|------|--------|---------|
| 情感密度 | 喜欢/爱/难过/崩溃等 32 个情感关键词 | 0 ~ +0.9 |
| 亲密度 | 宝贝/亲爱的/老公/老婆/家人等 25 个词 | 0 ~ +0.75 |
| 自我披露 | 我觉得/我的秘密/梦想/曾经等 22 个词 | 0 ~ +0.6 |
| 重要话题 | 生日/纪念日/分手/死亡/婚礼等 22 个词 | 0 ~ +0.8 |
| 长度价值 | 15-60 字最优，过短扣分 | -0.3 ~ +0.15 |
| 表达强度 | ? ！ 密度（问号/感叹号数量） | 0 ~ +0.2 |
| 私聊加成 | 私聊比群聊更亲密 | +0.1 |
| Bot 回复 | 有来有回的对话回合 | +0.05 |
| 负面惩罚 | 重复字符/纯英文数字/空白/笑声 | -0.5 ~ -0.2 |

**权重阈值**：≥ 0.45 自动标记为高权重，用户消息自动入记忆库。
**存储上限**：每用户 5000 条原始对话 + 200 条高权重索引。

### 3. 关系识别与边界技法

按五阶关系模型区分对待不同用户：

| 阶段 | 触发条件 | 行为差异 |
|------|---------|---------|
| `normal` 刚认识 | 初始 | 收敛礼貌，不主动追问 |
| `warming` 渐熟 | 好感度 +20 | 主动关心频率提升 |
| `attached` 铁哥们 | 好感度 +50 | 可聊私密话题、主动分享日常 |
| `refusing` 想静静 | 负面信号累积 | 降低主动频率，保持距离 |
| `hurt` 冷淡期 | 严重负面事件 | 被动回复为主，等待修复 |

**主要用户 vs 次要用户**：每个私聊用户可单独标记身份，次要用户自动收敛为普通朋友边界，不注入私密内容、不触发敏感主动能力。

### 4. 主动行为编排技法

不是想到就发，而是经过**四层过滤**：

```
定时触发（cron 周期检查）
    ↓
时机判断（时间/日程/用户活跃度/Bot 精力/关系温度）
    ↓
强度复核（宽松/标准/严格三档，由 LLM 或规则判定）
    ↓
形态选择（文字/图片/语音/戳一戳/窥屏/打字态）
    ↓
发送 + 冷却（避免刷屏）
```

**主动行为类型**：早安晚安、日常分享、心情问候、纪念日提醒、梦境复述、创作分享、新闻读后感、探索笔记……

### 5. 轻重双 AI 核技法

为不同任务分配不同模型，兼顾质量与成本：

| 任务类型 | 推荐模型规格 | 说明 |
|---------|-------------|------|
| 主回复 | 大模型（70B+） | 保证对话质量 |
| 日程/细化 | 小模型（7B-14B） | 结构化生成 |
| 日记/梦境 | 中模型 | 创意文本 |
| 新闻整理 | 小模型 | 摘要提炼 |
| 主动人格判定 | 小模型 | 分类决策 |
| 关系分析 | 中模型 | 理解评估 |
| 记忆整理 | 小模型 | 摘要归纳 |
| 回复复核 | 大模型 | 质量把关 |

**Token 监控**：按任务分类统计调用次数、Token 消耗、失败记录，支持每日限额。

### 6. 休息模拟技法

睡着了就别秒回——让 Bot 的响应时间有"人味"：

```
消息到达
    ↓
是否在休息时段？（夜间/午休/日程休息段）
    ├── 否 → 正常回复
    └── 是 → 闸门判断
              ├── 用户明确叫醒？→ 放行 + 醒后缓冲
              ├── 紧急/安全？→ 放行
              ├── 按概率醒来？→ 概率判定
              └── 模型判断？→ 小模型打分
```

放行后进入**醒后缓冲期**，此段时间内不再重复判醒。

### 7. 角色收集技法

6 个预设角色 + 1 个隐藏角色，通过好感度逐步解锁：

```
初始随机分配 1 个角色
    ↓
好感度 68 → 解锁第 2 个
    ↓
第 2 个 64 → 解锁第 3 个
    ↓
第 3 个及以后 49 → 解锁下一个
    ↓
触发特定台词 → 解锁隐藏角色
```

每个角色有独立的人设、说话风格、能力侧重，可随时切换但需关系重建。

### 8. 世界观转译技法

把现代技术能力翻译成角色能理解的"世界内说法"：

| 现代概念 | 命理世界转译 |
|---------|-------------|
| 搜索 | 卦象推演 / 天机查询 |
| 记忆库 | 前世记忆 / 神识藏书阁 |
| LLM 调用 | 心灵感应 / 神识交流 |
| Token 消耗 | 灵力消耗 / 真元损耗 |
| 插件更新 | 修为提升 / 功法升级 |
| 报错/故障 | 走火入魔 / 灵力紊乱 |

---

## 命理能力（后台静默）

命理作为后台洞察能力，**绝不主动出场**，除非用户先开口：

| 能力 | 说明 |
|------|------|
| 八字排盘 | 四柱、日主、五行、旺衰、格局、用神、大运、流年 |
| 紫微斗数 | 十二宫星曜分布、五行局、命宫/身宫、大限走势 |
| 六爻占卜 | 铜钱起卦，卦名、六爻排列、动爻 |
| 合参分析 | 八字+紫微双体系交叉印证 |
| 近况分析 | 情绪信号 + 命盘 → 近期心理状态推演 |
| 自我探查 | 心理咨询式引导，命理作为镜子 |
| 流月推运 | 命盘类每月推算大限/流年/**流月**（五虎遁纯 Python 推导，零依赖） |
| 后台静默关注 | 每月 cron + 高权重情绪触发，按命理方法分流测算/卜问，蒸馏术语无关注记 |
| 命理方法分流 | 用户可设 紫微/八字/六爻/奇门/梅花，决定后台关注走命盘还是卜卦 |
| 危机干预 | 两线危机识别：自伤意念→技术开导；自杀进行中→热线注入 |
| 暗盘守护 | 命理洞察伪装成"直觉"自然织入，`disguise_guard` 防术语泄露 |

> **静默排盘**：用户首次提供出生信息后，后台自动排盘并存档。后续聊天中，命理洞察会伪装成"直觉"或"感觉"自然织入对话，不出现术语。

---

## 后台静默关注（每月测算）

命灵会在后台按月"默默留意"每位用户，把命理洞察化作一句自然的直觉注入日常对话——**用户永远不知道它来自命盘或卦象**。

- **触发方式**：每月 1 号定时 cron（`background_watch_cron`，默认 `0 7 1 * *`）静默计算；此外，当用户说出**高权重情绪向话语**（如崩溃、强烈期待）时，立即实时重算（带冷却，避免一波情绪反复打 API）。
- **按命理方法分流**（用户直接说“用紫微/用八字/用六爻”等即可设定，或默认自动推断：有出生信息→命盘类，否则→六爻）：
  - **命盘类（紫微 / 八字）** → 调用 `/api/combined` 推算当前 **大限 / 流年 / 流月**。流月由流年干支按五虎遁纯 Python 推导，零依赖。
  - **卜卦类（六爻 / 奇门 / 梅花）** → 调用 `/api/liuyao` 或 `/api/qimen` **卜问当前情况**（梅花无独立端点，桥接六爻）。
- **术语无关注记**：蒸馏出一句绝不出现命理术语的生活化短句（"你最近好像在某件事上悄悄起着变化"），存入 KV，供暗盘与主动关怀自然织入回复。
- **危机红线（两线）**：私聊出现自伤**意念** → 仅做技术开导，不触发命理；出现自杀**进行中**动作 → 注入希望 24 热线 / 北京心理危机热线 / 共青团 12355，并跳过一切命理介入。

> 暗盘守护（`disguise_guard`）在所有命理输出前做术语扫描，一旦泄露命理术语即拦截改写，确保"直觉感"不被戳破。

---

## 安装

### 前置条件

1. **AstrBot** ≥ 4.26（Star 插件框架）
2. **Mingbu 命理服务**（可选，不部署则命理功能不可用，其他拟人功能正常）

### 安装插件

```bash
cd AstrBot/data/plugins
git clone https://github.com/sloveniator/mingling.git
```

在 AstrBot WebUI → 插件管理 → 点击「重载」。

### 配置 Mingbu 服务地址（可选）

| 方式 | 示例 |
|------|------|
| 环境变量 | `MINGBU_API_URL=http://192.168.2.108:2963` |
| 默认值 | `http://localhost:2963` |

Docker Compose 中设置：

```yaml
services:
  astrbot:
    environment:
      - MINGBU_API_URL=http://mingbu:2963
```

---

## 配置项

配置通过 AstrBot 插件管理页面可视化操作，主要分类：

| 分类 | 核心项 |
|------|--------|
| 人格与世界观 | 名字/性别/性格倾向/世界观模式/知识库参照 |
| 模型参数 | temperature / max_tokens / 主 Provider |
| 多 Provider 编排 | 各任务类型对应 Provider |
| 休息与免打扰 | 睡眠时间/午休/闸门模式/醒后缓冲 |
| 主动关怀 | 每日上限/最小间隔/复核强度/边界设置 |
| ↳ 频率自适应 | 主动发送频率随聊天频次动态调整（按近 3 天用户消息数分档）：高频≥6 条→间隔 3h/概率×1.4；常规 2–5 条→6h/×1.0；低频≤1 条→10h/×0.6。开关 `proactive_adaptive_enabled`（默认开），老会话无统计按常规处理；企微仍守官方 48h 窗口 + 每窗口条数上限，自适应不突破硬框 |
| 记忆管理 | 记忆上限/衰减速率/巩固周期 |
| Token 监控 | 每日限额/告警阈值 |
| 增强功能 | TTS / 语音转文字(STT) / 记忆语义向量召回(Embedding) / 记忆重排序(Rerank) / 图片转述 / 撤回感知 / 防抖合并 |
| 后台关注 | 启用开关/cron/命理方法默认/情绪触发阈值与冷却/卜卦默认问句 |
| 角色收集 | 初始角色/解锁阈值/隐藏台词 |
| ↳ 人格注入总开关 | 关闭 `character_collection_enabled` 后，主对话不再注入收集角色或固定「灵犀」人格块（含羁绊/记忆/关系网提示），人格完全采用 AstrBot 面板配置；危机/开解/祝福/图片转述等独立功能不受影响 |

### 环境变量（Docker / 命令行注入）

绝大多数配置都在插件配置页填，只有命理服务地址额外支持环境变量注入，便于 Docker 部署：

| 变量 | 含义 | 默认值 |
|------|------|--------|
| `MINGBU_API_URL` | Mingbu 命理计算服务地址 | `http://localhost:2963` |

**优先级**：插件配置项 `mingbu_api_url` > 环境变量 `MINGBU_API_URL` > 内置默认值。
插件配置页里填了地址，环境变量就会被覆盖。

**生效时机**：该变量在 `constants.py` 于**模块导入时**求值，因此必须在 AstrBot 进程
启动前设置；进程跑起来后再改 shell 变量不会生效，需重启。Docker 场景写进
`environment` 或 `env_file` 即可。完整示例见仓库根目录的 `.env.example`。

> 第三方 Provider（STT / TTS / Embedding / Rerank / LLM）的 API Key **首选在 AstrBot 主设置里配置**，
> 不要写进本插件的环境变量。**STT 也完全走 AstrBot STT provider**：插件不持有任何 STT 凭据、
> 不做直连调用，只需开 `stt_enabled` 并（可选）填 `provider_stt`。

### 增强功能说明（依赖与开关）

这些开关**都不依赖命理 API（`mingbu_api_url`）**，也不依赖彼此。按依赖分为两类：

- **纯本地、开箱即用**：撤回感知、消息防抖、世界观适配——打开即生效，无需任何外部服务。
- **依赖 AstrBot 的对应 Provider**（在 AstrBot 主设置里配置，不是本插件配置）：
  - **TTS 语音**：需 AstrBot 配置了 **TTS provider**（文字→语音）。`provider_tts` 可指定 ID，留空用当前启用的 TTS provider。
  - **图片转述**：走 AstrBot 默认 LLM（自动回退）；若默认 LLM 是多模态模型即可看图，否则在 `provider_image_vision` 指定一个多模态 provider。
  - **合并转发转述**：走 AstrBot 默认 LLM，无需额外配置。
  - **语音转文字（STT）**：把语音消息转成文字再进对话，让 Bot 能"听"语音（也规避主模型不接受 audio 分片的 400）。在 AstrBot 主设置配好 STT provider，开 `stt_enabled`，`provider_stt` 可指定 ID（留空用当前启用的 provider）。插件不再提供 STT 直连（历史版本的 `stt_api_base`/`stt_api_key`/`stt_model` 已移除，填了也不会生效）。
  - **记忆语义向量召回（Embedding）**：需 AstrBot 配置了 **Embedding provider**（如硅基流动 `BAAI/bge-m3`），开启 `embedding_enabled` 后按当前消息对相关记忆做余弦相似度语义召回，比纯时间/权重排序更懂"此刻在聊什么"；无 provider 时自动退回原序或 Rerank。
  - **记忆重排序（Rerank）**：需 AstrBot 配置了 **Rerank provider**，开启 `rerank_enabled` 后按当前消息对记忆做相关性重排，提升注入 LLM 的记忆质量；无 provider 时按时间/权重原序，行为不变。

> 所有依赖 Provider 的功能都做了**优雅降级**：对应 provider 未配置时自动跳过，不会报错、不影响其他功能。
> 注意 STT 与 TTS 方向相反（STT=听，TTS=说），互不依赖；Embedding/Rerank 只作用于记忆检索，与语音无关。第三方接入步骤见 `CONFIG_TEMPLATE.md` 第 2 节或 `THIRD_PARTY_ACCESS_CARD.md`。

---

## 管理面板

插件内置完整的 Web 管理面板（在 AstrBot 插件管理中点击打开）：

| 面板 | 功能 |
|------|------|
| **总览** | Bot 状态（心情/精力/人格）、用户数、互动数、近期活动、群组关系图 |
| **羁绊管理** | 用户列表搜索、关系详情、好感度、记忆时间线、梦境记录、一键触发成长/梦境/反思 |
| **角色收集** | 解锁进度、角色卡片展示、好感度进度条 |
| **对话检视** | 全部对话/高权重对话/已学知识/统计概览，支持筛选、搜索、批量入记 |
| **资源监控** | Token 用量统计、任务分布、每日限额 |
| **配置中心** | 所有配置项可视化操作，带详细说明 |

---

## 版本演进

> 按版本号倒序排列（最新在前），每个版本标注核心技法突破。

**v5.4.0** · **管理面板重做为 v5 专用控制台**——① 后端新增 `/mingling/overview` 与 `/mingling/config`（GET/POST），原有 `/users`、`/timeline` 增强为用户摘要与按类型/条数过滤；② 配置保存支持 AstrBot `save_config()`，缺失时回落到 `data/config/mingling_config.json`，并在内存中原地更新 `CompanionConfig`，状态层/主动消息/语义路由即时生效；③ 前端移除角色、TTS、暗盘、Token、诊断等 v4 遗留页签，改为「总览 / 用户与时间线 / 配置」三页；④ 总览展示版本、用户数、时间线文件、cron、活动配置摘要；用户页支持搜索、关系阶段/情绪/话题摘要与时间线展开；配置页覆盖全部 v5 活动键；⑤ 新增面板 API 与 schema 一致性测试。版本四件套 **5.3.2 → 5.4.0**。

**v5.3.2** · **cron 同名去重**——AstrBot 的非持久化 basic job 在每次重启时会重新入库，但不会自动清理同名旧行，实测形成重复任务。修复 `_add_cron_job`：注册前先列出并删除同名旧任务，再创建新任务；新增同名重复清理测试。版本四件套 **5.3.1 → 5.3.2**。

**v5.3.1** · **cron 注册兼容 AstrBot 4.29**——生产验收发现 `CronJobManager.add_basic_job` 已改为异步且参数名为 `cron_expression`，旧写法 `cron=` 导致主动消息与每周成长任务注册失败。修复为按签名自适应：支持 `cron_expression`/`cron`、同步/异步返回值，并显式绑定 `Asia/Shanghai` 时区；新增同步/异步两种管理器签名的回归测试。版本四件套 **5.3.0 → 5.3.1**。

**v5.3.0** · **混合意图路由：正则快速路径 + 默认模型语义兜底**——① 新增 `semantic_router.py`：正则未命中且消息含功能/情绪线索时，调用 AstrBot 默认模型输出严格 JSON（intent/confidence/counsel_action）；② 正则已命中（排盘/卜卦/塔罗/健康/成长/状态/情绪/退出）时不调用模型，保持零成本零延迟；③ 开解中仅对可能收束/转向的长消息做语义判断，短应声直接延续；④ 低置信度、超时、JSON 非法一律 fail-open，交回普通聊天；⑤ 新增 `semantic_router_enabled/confidence/timeout/min_chars` 四个配置键；⑥ 新增解析、触发条件、语义进入开解、低置信度忽略、正则快路径不调用模型等测试。版本四件套 **5.2.0 → 5.3.0**。

**v5.2.0** · **全自然语言交互：移除斜杠命令，意图路由 + 开解状态机**——① 删除 12 个 `@filter.command` 注册，改为单一私聊消息入口 `auto_route` 做语义意图识别；斜杠文本也只按中文语义处理；② 新增 `intent_router.py`：状态/排盘/卜卦/塔罗/命盘档案/命理方法/心情/健康/成长按自然语言触发，缺少信息时自然追问；③ 开解改为状态机：情绪困扰、想倾诉、需要安慰时自动进入；进入后普通聊天自动带入共情提示；用户说“好多了/没事了/不想聊了/换话题”等语义时自然退出并收尾；④ 开解中转向具体任务时，先简短承接情绪再处理任务；⑤ 所有用户可见回复移除命令式提示，不展示内部意图标签；⑥ 新增意图路由与状态机测试。版本四件套 **5.1.0 → 5.2.0**。

**v5.1.0** · **命理/咨询/成长命令回归 + 状态层自动更新**——① 恢复排盘、卜卦、塔罗、命盘档案、命理方法、心情、开解、健康、成长共 11 项能力（当时以斜杠命令提供，v5.2 起改为自然语言）；② 排盘/卜卦调用命理计算服务，塔罗/健康/成长/解读统一走 `provider_router.default_llm_request`，即 AstrBot 当前默认对话模型，插件不再读取自有 API Key；③ 修复 v5 状态层只注入不更新的问题：每条私聊消息自动执行 `emotion.nudge`、`relationship.nudge`、`relation_memory.remember`，关系与记忆在聊天过程中自然增长；④ 开解会话通过状态层注入陪伴提示；⑤ 新增每周成长反思 cron；⑥ 新增 `command_support.py`、`llm_default.py` 与对应测试。版本四件套 **5.0.0 → 5.1.0**。

**v5.0.0** · **1V1 状态层破坏性重构 + 插件身份统一为 mingling**——按上游 `sloveniator/mingling` HEAD（`a04767b`）落地 v5：① 入口收敛为单一 LLM 钩子 + `/命灵状态` 命令，状态层由关系、情绪、话题栈、未闭环话题、关系记忆五路组成，按 token 预算注入 `system_prompt`；② 新增 `Store`（KV + jsonl 时间线）、`ProactiveEngine`、`AdminTimelineAPI`（`/mingling/users`、`/mingling/timeline`）；③ 插件标识、包名、目录、配置文件名统一为 `mingling`，`plugin_data` 根目录改为 `plugin_data/mingling`；④ 配置 schema 补齐 v5 实际读取的 24 个键，`enabled` 默认 `true`，并把旧版 `quiet_hours_*` 映射到 `proactive_quiet_*`；⑤ 新增 `migrate_v4_to_v5.py`，迁移旧配置与时间线文件；⑥ 删除 v4 的角色/群聊/TTS/STT/命理指令/暗盘/成长/梦境等运行链路。版本四件套 **4.9.9 → 5.0.0**。

**v4.9.9** · **Embedding/Rerank/图片转述 直连键描述纠正（运行时已完全走 Provider）**——接续 v4.9.7/4.9.8 的 Provider 化文档收尾，本次覆盖 Embedding / Rerank / 图片转述（Vision）三类。运行时早已完全交由 AstrBot Provider（`get_embedding_provider` / `get_rerank_provider` 走 AstrBot embedding/rerank provider 实例；图片转述 `_describe_images` 走 `provider_router.llm_request("image_vision")`），插件侧的 `embedding_api_key` / `rerank_api_key` / `image_vision_api_key` 直连键**运行时零读取**，仅剩管理面板「连通性测试」按钮作为自填凭据消费——但 schema 描述仍写「填此项后优先使用插件配置直接调用，不依赖 AstrBot」「优先走插件直连（覆盖 provider_image_vision）」，会让用户误以为填了 Key 就走插件直连、不配 AstrBot Provider 也能用，与 STT 那处遗漏同根。修正范围（用户选定「描述 + README/CONFIG 同步」）：① `_conf_schema.json` 三键描述改为「仅供面板连通性测试按钮使用，运行时走 AstrBot Provider」；② `pages/admin/index.html` 三张服务卡「自定义模式」的 API Key placeholder 与 config-hint 同步纠正（原文案「选中即以该模型独立调用，不依赖默认服务商」）；③ `CONFIG_TEMPLATE.md` 第 ⑦ 节 `tts_voice` 行「依赖」列由「直连 / AstrBot Provider」改为「AstrBot TTS provider」并补「完全走 Provider，音色透传」。README 第 3 节增强功能段（图片转述/Embedding/Rerank）与 `THIRD_PARTY_ACCESS_CARD.md` 经核对已正确指向 Provider，未改动。版本四件套 **4.9.8 → 4.9.9**。

**v4.9.8** · **TTS 测试同步迁移至 Provider 路径 + 清理失效直连/语气指令残留**——接续 v4.9.7「STT 完全交由 Provider + 修文档遗漏」的 TTS 侧收尾：① `tests/test_tts_send_chain.py` 的 fake provider `get_audio` 是同步函数，而 `_send_tts` Provider 分支对返回值 `await` → 合成抛异常被吞 → `ev.sent` 为 None，两个核心回归（MessageChain 包裹、voice 透传）恒失败；改 `async def get_audio` 后 5 用例转绿。② 删除 `tests/test_tts_style_instructions.py`（6 用例）——语气指令链路（`TTS_EMOTION_INSTRUCTIONS`/`_tts_instructions_for`/`instructions` 注入）已在 v4.8.141 随直连整体下线（`main.py` 顶部注释：instructions 是插件直连 TTS 的私有能力，走 Provider 后 provider 不接收语气指令），测的是已不存在的生产符号。③ 改写 `tests/test_tts_long_short.py` 两个 45007 用例——原测已删除的 `_send_voice_chunk_retry_split`（v4.8.106 直连时代的文本二分重切）；Provider 路径 audio 是已合成文件、无法按文本二分，45007 行为改为「逐段记失败、不误计成功」，改写为断言「部分段成功 → 返回 True」「全部段 45007 → 返回 False」。④ schema 清理（与 v4.9.7 STT 同类遗漏）：删除 `tts_style_instructions_enabled`（语气指令 UI 已随 v4.9.3 移除、全库零读取，默认 true 却毫无效果）；`tts_voice` 描述去掉「直连与 AstrBot Provider 两种路径均生效」（直连已删）；`tts_model` 描述去掉「推荐 instruct 模型支持语气指令」（Provider 路径不接收指令）。版本四件套 **4.9.7 → 4.9.8**。

**v4.9.7** · **STT 文档遗漏修正：彻底交由 AstrBot Provider（移除失效直连宣传）**——v4.8.141 已把 STT 运行时全部改走 AstrBot STT provider、删除直连分支与 `stt_api_*` 配置键，但**四处文档仍在宣传这条已失效的直连路径**：`.env.example`（大段「STT 支持插件直连凭据」注释）、`CONFIG_TEMPLATE.md`（第 2 节说明 + 五类能力总览表「或插件直连 `stt_api_*`」）、`README.md`（第三方 Provider 提示 + 「两种接法任选其一」直连小节），用户照文档填 `stt_api_base`/`stt_api_key`/`stt_model` 既不生效也不报错，构成误导。本次统一修正：①四处文档全部改为「STT 完全走 AstrBot STT provider，只需 `stt_enabled` +（可选）`provider_stt`」，并显式标注历史 `stt_api_*` 已移除、填了也不生效；②`stt.py` 清理 `transcribe_audio` / `transcribe_event_voice` 残留的 `plugin_cfg` 形参（v4.8.141 直连移除后已无消费方，调用方 `main.py` 早已不传）与「支持插件直连凭据」的过时 docstring；③新增 3 个回归测试（`tests/test_stt_rerank.py`）守住「STT 只走 Provider、填直连键不生效、无 provider 时不发起直连 HTTP」的不变量。README 中 v4.8.26/93/94 的历史 changelog 记录保留原样（反映当时真实历史）。版本四件套 **4.9.6 → 4.9.7**。

**v4.9.6** · **记忆召回克制化：mention_policy 四档策略 + 反馈回路闭环**——此前被召回的记忆一律平铺进主模型上下文，带来两个问题：一是负向/冲突记忆（伤疤、尴尬、被否认的事）会被「主动戳痛」，二是对注入内容的否认/纠正没有回流通道，错误记忆长期降不下来。本次借鉴 memory_companion 的 mention_policy 给每条记忆标注四档提及策略——`direct`（可进入「我记得」正文自然流露）/ `soft_echo`（正文含蓄带过）/ `tone_only`（只作情绪底色影响语气、不逐条复述）/ `avoid_unless_asked`（默认不注入），按记忆类型 + 情绪极性在 `_default_mention_policy` 推导默认值，写入侧可显式覆盖；`persona_ctx` 注入时按策略分槽，从根上防「主动戳痛」。二是打通**记忆反馈回路**：注入时用 `remember_injected` 登记本轮被注入的记忆 id（id 只写 KV 元数据、不进注入文本，防内部标识泄漏，`remember_injected_refs` 另发短码供精确反馈），下一轮用户消息若命中否认/纠正信号（`classify_reaction`，宁漏判不误判），`llm_hook` 对上轮 id 调 `feedback()` 降权，形成「注入 → 反应 → 降权」跨轮闭环。另修 `self_evolution` 的 `add_memory` → `add()` AttributeError（该功能默认关闭、无测试覆盖，长期未暴露）。新增 mention_policy / feedback_loop / injected_lifecycle / precise_feedback / self_evolution_write_path 五组测试。版本四件套 **4.9.5 → 4.9.6**。

**v4.9.5** · **时区根因修复：命灵作息从 UTC 迁回北京时间**——生产容器原先没有 `TZ`，进程本地时区即 UTC（比北京慢 8h），而插件通篇默认「本地时间 = 北京时间」，于是**整套时间逻辑推迟 8 小时**：显示时间差 8h、睡眠/午休判定错位（2026-09-07 只在 `rest_gate.py` 单点硬编码 `+8` 治标，其余 68 处裸 `datetime.now()` 照旧取 UTC）；更要命的是 10 个 cron job 的 `timezone` 全为 `None`，AstrBot `_schedule_job` 回退「调度器默认 = 进程本地时区」，**梦境巩固 `0 3` 实际跑在北京 11:00、成长反思 `0 2` 跑在 10:00、记忆衰减/自我进化 `0 4` 跑在 12:00、情绪重置 `0 0` 跑在 08:00——「夜间任务」全跑到白天**（该症状在容器级 `TZ=Asia/Shanghai` 落地后消失，但属治标：换个没有 `TZ` 的环境即复发）。修复为**插件层显式时区、对环境零依赖**：①新增 `bj_time.py` 作为北京时间唯一来源（`BJ_TZ` / `now_bj()` / `today_bj()` / `now_bj_aware()` / `CRON_TZ`），`now_bj()` 返回 **naive 墙钟**——与历史 isoformat 存储格式一致，`datetime.now()` 时代的全部差值运算、`_parse_dt` 解析、序列化语义不变（替换是行为等价的）；②68 处裸 `datetime.now()` / `date.today()`（19 个文件）统一替换为 `now_bj()` / `today_bj()`，`rest_gate._now()` 的硬编码 `+8` 收敛为 `now_bj_aware()`（`.timestamp()` 需要 aware）；③4 个注册点全部显式传 `timezone=CRON_TZ`（`main._register_cron` 的 7 个周期任务 + proactive / background_watch / self_evolution），从此不再依赖容器时区；④测试：新增 `tests/test_bj_time.py`（**环境无关性**——把 `TZ` 改成 UTC/纽约/伦敦/空后 `now_bj()` 仍是北京时间、naive 契约、以及「插件源码里不得再出现裸 `datetime.now()`」的静态护栏），`test_cron_registration.py` 补「所有 job 必须绑北京时区」回归与 `_FakeCron.ALLOWED_KWARGS` 签名契约（参数名拼错即失败）。为什么不在容器里设 `TZ` 根治：AstrBot 核心假定「本地时间 == UTC」（`core/db/po.py` 的 aware-UTC 字段、`datetime_utils.normalize_datetime_utc` 把 naive 按 UTC 解读），改容器时区会把核心的会话时间戳一起带偏。全量 1233 用例回归通过。版本四件套 **4.9.4 → 4.9.5**。
**v4.9.4** · **音色触发器与 config-input 控件家族视觉统一**——v4.9.3 的自定义触发器自造样式（`--bg-1` 实底 / 14px / ▾ 文本箭头），与面板控件家族（`.config-input` 经 2318 行后置玻璃覆盖：`rgba(255,255,255,0.05)` + `backdrop-filter: blur(8px)`）不一致，实测背景与高度均偏离。对齐：触发器完全复用控件家族视觉（玻璃底 + blur、13px、同款 SVG chevron、`right 12px center` 定位、10px/12px padding、10px 圆角）；**button → div**（button 内置行高机制使内容行比 input 高 2px，且 div 的 normal 行高含 line-gap 更是高 7px——显式 `line-height: 15px` 压平，实测高度差归零 37.0/37.0）；键盘可达性补齐（`role="button"` + `tabindex` + Enter/Space 展开 + `aria-expanded` 同步 + `:focus-visible` 高亮）。无头浏览器逐属性断言（背景色/字体/圆角/边框色全同、高度 delta 0.0）+ 截图确认。1217 后端用例回归通过。版本四件套 **4.9.3 → 4.9.4**。
**v4.9.3** · **休息闸门午休可配置化（砍掉"静默拦死"死角）**——生产实锤：webchat 凌晨消息全被静默拦截，根因 = 生产配置 `rest_gate_wake_probability=0.0` + 实际时间落入睡眠时段（UTC 日志时间戳曾误导排障方向），且午休 12-14 为硬编码、`sleep_start/end` 配置管不到，用户中午被拦无解。修复：①午休时段改为可配置 `rest_gate_nap_start` / `rest_gate_nap_end`（默认 12/14 向后兼容；`nap_start=-1` 或两端相同即禁用午休），管理面板休息闸门卡片同步新增「午休时段」控件；②`_hour_in_range` 禁用语义显式短路，防止两端相同时走跨午夜分支变成全天拦截。新增 `tests/test_rest_gate.py` 16 用例（默认兼容 / 禁用 / 自定义时段 / 概率 0.0 全拦复现 / 0.7 掷骰 / 叫醒词缓冲 / 总开关）；版本四件套 **4.9.2 → 4.9.3**。

**v4.9.3** · **音色选择器下拉化（试听内嵌 + 音色库区块整体移除）**——v4.9.2 后 TTS 卡片仍存在「音色下拉 + 下方音色库卡片网格 + 试听预览区」三块布局。合并为单一**自定义下拉组件**：①原生 `<option>` 无法内嵌按钮，改用 JS 渲染的下拉面板——每行 = 音色名 + 描述 + **🔊 试听按钮**（点击即真实合成播放，动态 Audio + toast 反馈，不改当前选中）；②数据层保留视觉隐藏的 `data-key="tts_voice"` select，config 读写机制零改动；③点行 = 选用并立即保存（含「留空＝接口默认」清空路径，`saveTtsVoiceValue` 支持空值）；④音色库卡片网格 / ttsProvider 下拉 / 拉取音色按钮 / 试听文本与语气指令输入 / 音频结果区**全部移除**（`svc-voicebank` 区块与 voice-card/voicebank 一族 CSS 清理），试听 provider 固定 dashscope（内置 24 音色均为 DashScope 官方音色）；⑤面板支持点击外部/Esc 收起。后端零改动。前端以 mock bridge harness + 无头浏览器完成端到端验证（初始化恢复选中/展开/试听调用/单键保存/清空/Esc 六链路全绿），后端 1217 用例回归通过。版本四件套 **4.9.2 → 4.9.3**。
**v4.9.2** · **音色库与「音色 (voice)」字段三合一（下拉化 + 试听同源 + 零成本自动填充）**——管理面板 TTS 卡片内原先三处音色入口割裂（音色库卡片 / 试听区独立 previewVoice 下拉 / 「音色」文本输入框）。合并：①「音色 (voice)」由文本输入改为**下拉**（首项「留空＝接口默认（SiliconFlow 为 alex）」+ 24 个官方音色，格式 `Cherry - 芊悦｜阳光积极…`）；②删除独立 previewVoice 下拉，**试听音色 = 音色下拉当前值**；③点击音色卡片「选择」/在音色下拉中直接切换 → 均立即保存 `tts_voice` 单键（config 单键提交，不清其他配置）；④后端 `api_tts_voices` 新增 `skip_verify` 模式——跳过 Key 校验与 DashScope 外部调用，直接返回内置静态音色表（音色表本身为纯静态数据，与 Key 有效性无关），前端页面加载即零成本填充下拉（失败静默不打扰）；「拉取音色」按钮保留 Key 验证语义不变。官方音色单一数据源仍在后端 `tts_config.py`（前端不硬编码）。新增 3 个 skip_verify 接口用例；版本四件套 **4.9.1 → 4.9.2**。
**v4.9.1** · **修复定时任务静默失效（cron 空串配置事故）+ 全模块同名旧 job 积累清理**——生产配置 `self_evolution_cron` / `background_watch_cron` 键存在但为空串，`cfg.get(k, default)` 的默认值对空值无效 → `cron_expression=""` 入库 → AstrBot `_schedule_job` 抛 "recurring job missing cron_expression"，自我进化与每月后台关注两个定时任务静默失效。三处 cron 读取（含 proactive 同源隐患）统一改为 `(cfg.get(k) or "").strip() or DEFAULT`。另发现并修复 **job 无限积累**：basic job 的 handler 不可持久化，每次重启重建，而 db 层纯 INSERT 不按 name 去重——生产库 cron_jobs 已积累 **1563 条**（7 个主模块各 ×223 + 3 个引擎模块各 ×1）；新增 `util.cleanup_stale_cron_jobs()` 并接入全部 10 个注册点，注册成功后自动清理同名旧 job，首次部署即回收全部死记录。顺带修复 main.py 循环注册的 lambda 闭包泄漏（task name 引用循环变量，触发时漂移为最后一个值）。新增 tests/test_cron_registration.py（14 用例）。版本四件套 **4.9.0 → 4.9.1**。（存量问题之二「dashboard dist 缺失」经排查为 AstrBot 自更新过渡态误诊：Core/dist/进程三者对齐 4.28.0 后自愈，随 4.9.0 部署重启解决，无需代码变更。）
**v4.9.0** · **TTS 配置/音色库架构收敛（`tts_config.py` 单一数据源 + 前端音色库并入 TTS 服务卡片）**——① 后端：新增 `tts_config.py` 作为 TTS 唯一配置出口——`TTSSettings` dataclass（enabled/模式/音色/情绪路由/分段字数）+ `load_tts_settings()` 统一读取与类型收敛 + `_DASHSCOPE_VOICE_NAMES` 音色库与 `resolve_dashscope_voice()` 白名单回退；`main.py` / `stars/apis/tts_voices.py` / `diagnostics.py` 全部改走新出口，原 `tts_voices.py` 内联常量收敛为 re-export 层（对外导入路径不变）。② 前端：管理面板「音色库」独立 Tab 删除，整库（服务商切换 / 拉取 / 音色卡片选择 / 试听短长文本 / 语气指令）并入「核心配置 → 语音合成 (TTS)」服务卡片底部（`.svc-voicebank` 区，元素 ID 不变 → app.js 逻辑零迁移）；顺带清掉 `previewVoice` 下拉残留的虚构音色 Liam（v4.8.133 修了数据源、HTML 硬编码漏网），删除死代码 `initTtsTabIfNeeded` 与死 CSS `.tts-preview`。配置页音色库默认不拉取（省 API），点「拉取音色」按需加载。版本四件套同步升 **4.9.0**。
**v4.8.133** · **音色库「多数音色点击不发声」根治（虚构音色列表替换为官方 24 音色 + 无效音色白名单回退）**——① 根因：`stars/apis/tts_voices.py` 硬编码 `_DASHSCOPE_TTS_VOICES` 24 项中 **21 项为虚构音色名**（Liam/Olivia/Noah/Aria/Hiro/Sakura/Minjun/Yuki/Jian/Luna/Oliver/Mei/Chen/Zoe/Pierre/Sofia/Diego/Chiara/Marco/Anna/Felix），DashScope `qwen3-tts-instruct-flash` 根本不存在这些 voice → 试听/合成 400 InvalidParameter 无声。v4.8.132 修复 body 注入后 voice 真正传参，反而暴露了该问题（此前恒 Cherry 掩盖）。② 对照官方音色列表逐项核对并实测（真实 Key 调 DashScope：Liam→400、Chelsie/Moon→合成成功），全量替换为官方 24 音色：Cherry 芊悦 / Serena 苏瑶 / Ethan 晨煦 / Chelsie 千雪 / Momo 茉兔 / Vivian 十三 / Moon 月白 / Maia 四月 / Kai 凯 / Nofish 不吃鱼 / Bella 萌宝 / Eldric Sage 沧明子 / Mia 乖小妹 / Mochi 沙小弥 / Bellona 燕铮莺 / Vincent 田叔 / Bunny 萌小姬 / Neil 阿闻 / Elias 墨讲师 / Arthur 徐大爷 / Nini 邻家妹妹 / Seren 小婉 / Pip 顽屁小孩 / Stella 少女阿月；CosyVoice 列表虚构的 Liam 同步替换为官方 Chelsie。③ 防再发：导出 `_DASHSCOPE_VOICE_NAMES` 白名单，`main.py:_send_voice_chunk_retry_split` 与 `diagnostics.py` 连通性测试对 DashScope 直连路径做音色校验，无效音色 WARN + 回退 Cherry——即使配置残留虚构音色（如本次 NAS 实测发现 `tts_voice="Olivia"` 残留导致企微语音全无声）也不会静默失败。版本四件套同步升 **4.8.133**。
**v4.8.132** · **音色库试听「永远 Cherry」根治（TTS 三接口漏挂 `@with_json`，body 注入缺失）**——① 根因：`stars/apis/tts_voices.py` 三个 handler（`api_tts_voices` / `api_tts_preview` / `api_tts_preview_long`）签名声明 `body: dict = None` 但只挂了 `@api_endpoint` 兜底装饰器，漏挂 `@with_json`；而本插件 Web API 约定「JSON 体只由装饰器读一次并注入 handler 形参」（`_base.py` 模块文档明示，AstrBot 核心不传位置参数、已上线的 `api_config`/`api_kv_set` 等全部自读或挂装饰器）→ `body` 恒为 `None` → `body or {}` 后 text/voice 全走默认值：试听永远是「你好，今天天气真好」+ **Cherry**，与配置里选的音色无关。② 修复：三个 handler 统一补挂 `@with_json`（顺序沿用 `api_kv_set` 范式：`@api_endpoint` 在外兜底、`@with_json` 在内注入）；顺带修复 `api_tts_voices` 同源的 provider/api_key 传参失效（此前靠配置回退自愈，未暴露）。③ 部署注意：admin.py 路由版本守卫键按 metadata 版本命名，同版本 reload 不重挂路由，故版本四件套同步升 **4.8.132**。

**v4.8.131** · **音色库后端校验修复 + 「选择即保存」闭环（根治「选了音色仍合成 Cherry」）**——① 后端根因：`stars/apis/tts_voices.py` 校验 DashScope API Key 时调用 `http_client.request_json` 把 URL 字符串误传给首参 `session` 位置、`url=` 关键字缺失 → 必抛 `request_json() missing 1 required positional argument: 'url'`（全插件仅此一处错位，其余均为正确的 `(session, method, url)` 形式），异常被 except 吞掉后 WARN + 返回硬编码列表，Key 校验形同虚设；修正为 `request_json(None, "GET", url, api_key=..., timeout=...)`，并把响应校验从「`ok` 字段 + `data`」改为「dict + `data`」（DashScope `/models` 返回 `{"object":"list","data":[...]}` 无 `ok` 字段）。② 前端 UX 断层根治：`tts_voice` 配置 input 与「保存配置」按钮在「核心配置」tab，而音色库在独立 tab——用户选完音色看不到保存入口，配置恒为旧值 Cherry。修复：卡片「选择」/点击卡片主体 = 选中并**立即单键保存** `{tts_voice}`（`_save_config` 为合并语义、无 required 全量校验，单键提交安全），toast 提示已生效；「试听」按钮仅试听不落盘。③ 连带：进音色库页若 `CONFIG_DATA` 未加载先拉一次 `/config`，保证「恢复已保存音色选中态」不因 tab 切换时序而丢。④ 测试：TTS 定向 50 passed、配置链路定向 45 passed；全量 `1137 passed, 3 failed`——3 失败经 git stash 验证为基线遗留（路由计数 38→41、README 徽章、changelog），本轮同步修复。版本四件套同步升 4.8.131。

**v4.8.130** · **音色库面板功能上线（试听/选择/事件委托修复）**——① 功能：管理面板新增「音色库」tab——加载 DashScope / CosyVoice 可用音色、卡片化展示（语音/性别/描述）、试听短文本、选择写入 `tts_voice` 配置；后端新增 3 条路由（`tts/voices` 拉取列表 GET+POST、`tts/preview` 短试听、`tts/preview_long` 长试听），DashScope Key 校验、超时熔断复用 `http_client`。② 修复：`<script type="module">` 下模块作用域函数不挂 `window`，内联 `onclick="previewVoice(...)"` 点击必抛 `ReferenceError` → 全部改为 `data-action` + 事件委托；`initTtsTab` 移出 bridge 守卫避免绑定丢失；路由支持 GET+POST。③ 部署：343a327，容器 reload 成功，NAS 内联 onclick=0。版本四件套同步升 4.8.130（README 徽章与版本演进本节为发布时漏项，v4.8.131 一并补记）。

**v4.8.129** · **语音语气指令注入（解决「语音僵硬、缺乏情绪」）**——① 现象：v4.8.128 语音链路打通后用户反馈音色僵硬、朗读腔重。② 根因：DashScope 普通 `qwen3-tts-flash` 不支持任何风格控制（官方 instructions 参数仅 qwen3-tts-instruct-flash 系列支持，≤1600 token 中英文），当前 NAS 配置恰为普通模型且合成时不带任何语气信息。③ 修复：`main.py` 新增 `TTS_EMOTION_INSTRUCTIONS` 六类情绪→语气指令映射（joy=开心雀跃/sad=温柔心疼/angry=撒娇式克制/excited=兴奋饱满/calm=平和陪伴/neutral=自然口语）+ `_tts_instructions_for()`；`_send_tts` 直连分支在模型名含 `instruct` 且 `tts_style_instructions_enabled`（新面板开关，默认开）时按 `detect_emotion` 结果生成指令，经 `_send_voice_chunk_retry_split`（含 45007 二分递归）透传到 `provider_router._direct_dashscope_tts`，SDK 侧 `instructions` + `optimize_instructions=True`（官方语义增强）仅非空时注入，普通模型保持原请求形状。④ 配套：`_conf_schema.json` 新增 `tts_style_instructions_enabled` 并在 `tts_model` 描述中推荐 `qwen3-tts-instruct-flash`。⑤ 测试：`tests/test_tts_style_instructions.py` 5 用例（6 情绪映射完备/未知回退/instruct 传指令/普通模型与开关关闭不传/SDK kwargs 条件注入），全量 1137+ passed。版本四件套同步升 4.8.129。

**v4.8.128** · **用户点名语音强制触发（TTS 触发体验补口）**——① 现象：对 bot 说「发语音」只收到文字回复；NAS 近 3h 日志 `mingbu.tts` 零触发（TTS 从未被尝试，非合成失败）。② 根因：TTS 仅两条自动路径——长文本（≥500 字，被面板 `tts_auto_for_long_text=False` 关闭）与强情绪语义（`TTS_SEMANTIC_EMOTION_WEIGHT>=0.5`），随机路径恒为 0（v4.8.100 设计）；用户点名要语音时模型常回短中性句，三条路径全不满足 → 不触发。③ 修复：`main.py` 新增 `TTS_VOICE_REQUEST_RE`/`TTS_VOICE_REQUEST_NEG_RE` 与 `_user_requests_voice()`，「发语音/来段语音/用语音说/语音呢/说给我听」等点名句式最高优先级强制触发（reason=用户点名），否定语境（「别发语音」「转文字」）排除，不受 `tts_auto_for_long_text`/随机概率/情绪判定约束；结果照常写入 `mingbu.last_tts_result:{uid}` 供 llm_hook 回注防幻觉。④ 测试：`test_tts_trigger.py` 新增 3 用例（点名正例 14 句 / 否定语境 10 句 / 易误伤话术护栏 2 句），定向 16 passed。版本四件套同步升 4.8.128。

**v4.8.127** · **STT 遗留坏模型名自动纠偏（防复发护栏）**——① 现象：面板「语音识别 (STT)」连通性测试再次报 `DashScope STT HTTP 400 UNSUPPORTED_FORMAT: format is empty`。② 根因：浏览器旧缓存面板保存任意配置时，把内存态里的无效遗留名 `qwen-audio-3.0-asr-flash` 回写进 `stt_model`（v4.8.126 曾字节级修正过 NAS 配置，但旧面板会话仍揣着坏值，一保存就复写）。③ 修复：`provider_router.py` 新增 `_DASHSCOPE_ASR_MODEL_ALIASES` 别名表与 `_normalize_dashscope_asr_model()`，`_direct_dashscope_stt` 入口统一归一——无效遗留名自动映射 `qwen3-asr-flash` 并记 warning 日志，配置再被回写也能自愈；NAS 侧配置二次字节级修正并热重载验证通过。版本四件套同步升 4.8.127。

**v4.8.126** · **STT Qwen 系同步载荷改纯音频（NAS 实测闭环）**——① 现象：配置 `stt_model=qwen3-asr-flash` 后点测/直连仍 400 `InternalError.Algo.InvalidParameter: The dedicated task 'asr' corresponding to the current service does not support this input`；同 key 同模型手工探针（content 仅 `{"audio": url}`）稳定 200。② 根因：`_direct_dashscope_stt` 同步分支在 content 里多塞了 `{"text": "转写这段语音"}` 指令项，qwen3-asr 专属任务不支持 audio+text 混合输入。③ 修复：删除该 text 项，content 仅保留 `{"audio": url}`（纯音频即返回转写文本，无需指令）。④ 附带 NAS 侧配置修正：`stt_model` 由无效模型名 `qwen-audio-3.0-asr-flash` 改为 `qwen3-asr-flash`（前者在该端点恒报 `UNSUPPORTED_FORMAT: format is empty`）。⑤ 容器内三服务直连实测全绿：Rerank 0.22s / STT「欢迎使用阿里云。」0.39s / TTS(qwen3-tts-flash+Cherry) 142KB 1.37s。版本四件套同步升 4.8.126。

**v4.8.125** · **STT/相关性重排连通性测试与调用对齐 DashScope 原生路由（同 v4.8.122 TTS 根因）**——① 根因：阿里云百炼（DashScope）原生域不提供 OpenAI 兼容的 `/audio/transcriptions` 与 `/rerank`，也没有 `/models`——STT/Rerank 的连通性点测（`stars/apis/diagnostics.py`）、运行时直连兜底（`stt.py:transcribe_audio`、`provider_router.py:rerank_memory_list`）和「拉取模型目录」（`fetch_model_catalog`）都按 OpenAI 形状拼 URL，测阿里云模型必 404。② `provider_router.py` 新增 `_is_dashscope_target`（以 base 域名为准，不误判本地自建 SenseVoice/FunASR）、`_direct_dashscope_rerank`（原生 `/api/v1/services/rerank/text-rerank/text-rerank`，gte-rerank 同步）、`_direct_dashscope_stt`（按模型分流：qwen3-asr 系走同步多模态 generation；sensevoice/paraformer 走异步录音文件识别 `/api/v1/services/audio/asr/transcription` + 轮询 `/api/v1/tasks/{id}`；原生 ASR 只收公网音频 URL，连通性测试用官方示例音频做真样本）。③ 拉取模型目录：base 指向 dashscope 原生域时自动改走 `compatible-mode/v1/models`。④ 运行时 STT：DashScope 源仅 http(s) 音频 URL 直传原生识别，本地字节明确跳过直连走 provider 兜底（不假装成功）。版本四件套同步升 4.8.125。
**v4.8.124** · **修复面板「加载配置失败：Plugin bridge endpoint is invalid」**——① v4.8.123 把 loadConfig 的缓存穿透写成 `apiGet("config?v=" + Date.now())`，端点字符串里出现 `?` 被 AstrBot 宿主桥接 SDK（PluginPagePage）的端点校验直接拒绝并抛 `Plugin bridge endpoint is invalid`，请求根本不会发出，面板配置页加载必失败；按桥接约定改为 query 走 `apiGet` 第二参数：`apiGet("config", { v: Date.now() })`。② 版本四件套同步升 4.8.124（metadata.yaml / `_version.py` / README 徽章与版本演进 / index.html 静态缓存戳 `?v=` 与页脚占位）。
**v4.8.123** · **TTS 连通性测试修复上线闭环（provider_router 导入路径 + Web API reload 生效 + 面板缓存戳）**——① 修复 v4.8.122（0393d33）引入的 `stars/apis/diagnostics.py:282` 单点相对导入笔误：`from . import provider_router` 会去 `stars/apis/` 找模块，而 `provider_router.py` 在插件根目录，DashScope qwen-tts/qwen3-tts 测试分支一进即抛 `ModuleNotFoundError`；改为与文件头模块级导入一致的三点半 `from ... import provider_router`。② 修复 AstrBot 插件 reload 对插件 Web API 不生效的问题：`stars/admin.py::_register_web_apis` 的幂等守卫原来挂在存活的 star_context 上（`_mingbu_admin_registered`），reload 重新加载插件后被旧标记跳过，38 条路由始终绑着旧进程启动时（拉取前代码）的 handler——升级/修 bug 后管理面板永远跑旧代码。守卫键改为运行时读 `metadata.yaml` 版本号（`_mingbu_admin_registered_v{version}`），升版后首次加载必重新注册，`register_web_api` 按「路由+方法」替换旧 handler。③ 面板静态资源缓存戳 `?v=4.8.116` 自 v4.8.117 起未再更新，浏览器长期命中旧 app.js 缓存（用户改配置不生效、测试按钮跑旧逻辑）；统一升到 `?v=4.8.123`。④ 面板去掉 v4.8.122 临时加的「DashScope TTS 跳过 HTTP 测试」前端拦截：后端 SDK 真实合成测试可用后，测试按钮直接返回真实结果（含合成字节数）。全量 pytest 1131 passed。

### v4.x — 系统成熟期

**v4.8.120** · **角色收集总开关 + 收集图鉴UX双层落地 + 主动发送频率自适应**——① 角色收集总开关（A 案）：`character_collection_enabled=false` 时 `persona_ctx.py::_build_persona_block` 开头直接返回空——不再回退注入「灵犀/神秘朋友」self_prompt，主对话人格完全由 AstrBot 配置（system_prompt/面板默认人格）决定；危机干预/心理开解/祝福/图片/TTS 等功能钩子不经过该注入点、不受影响，默认开启行为与旧版一致。② 收集图鉴 UX 双层：角色收集与解锁（6 可见角色门槛 + 隐藏角色彩蛋线索 KV 一次性标记 + 集齐确定性直发 + 解锁直发角色卡），图鉴/角色命令共享行构建器 `_character_codex_lines`、头部文案「想换谁陪你就直接说名字，或说『切换』」，隐藏角色双通道触发。③ 主动发送频率自适应：`track_session` 记录近 14 天消息（上限 300），`_activity_profile` 按近 3 天分档——高频(≥6条)3h×1.4、常规(2–5条)6h×1.0、低频(≤1条)10h×0.6、老会话走现状；`proactive_adaptive_enabled` 默认开，企微 48h 窗口≤2 条与 quiet hours 硬框不变。附：`rest_gate.py` 固定按北京时间（UTC+8）判定睡眠/午休，修复容器 UTC 时区错位导致的全天无法回复。新增 `tests/test_proactive_adaptive.py`（10 条）；全量 pytest 1131 passed。

**v4.8.119** · **企微主动推送 quiet hours 硬跳过（收紧）+ 主动消息上下文双通道落地**——① `_maybe_message` 企微分支新增静默时段（quiet_hours_start/end，默认 23–8 点）硬跳过：工作渠道夜间不再概率放行（此前 quiet 仅概率折减 ×0.3，回归测试捕获到 quiet=True 仍直连发送的收紧漏洞），其余渠道维持原折减不变；新增回归测试 `test_wecom_quiet_hours_skips`。② 主动消息上下文双通道：proactive 发送成功后三连写 `conversation_inspector.log_bot_reply`（对话历史副本）+ `last_proactive_msg`（llm_hook 回注「你不久前主动给 TA 发过一条消息：…」）+ `memory.add_episode(source="proactive", importance=0.5)`（情景记忆），修复「bot 主动发过消息但回复时自己不知道」；llm_hook.py:353/355 f-string 嵌套双引号 SyntaxError 修复（单引号 + 「」），主机 + 容器 py_compile 通过、pyc 重编译。全量 pytest 1115 passed。

**v4.8.118** · **企微主动推送解锁（v4.8.117 跳过 → 直连）+ 消息类型枚举修复 + 关系分 KV 回写**——① 企微客服主动推送不再整段跳过：官方 kf/send_msg 本身支持主动下发（客户来消息后 48h 内、每轮 ≤5 条），AstrBot `context.send_message` 走 `send_by_session` 才不支持。本版新增 `_send_wecom_kf` 直连 `client.kf_message.send_text`（wechatpy 同步调用经 executor 包装），`_maybe_message` / `send_welcome` 对企微改走直连，并自行守住官方限制（48h 滚动窗口 + 每窗口条数上限，配置 `proactive_kf_window_hours` / `proactive_kf_max_per_window`，默认 48h / 2 条，比官方 5 条更克制）；`open_kfid` 取自进入客服消息的 `message.self_id`（wecom_adapter 转换时写入），由 llm_hook `track_session` 一并落库，无 open_kfid 的会话安全降级跳过。② 消息类型判断统一走新 helper `util.msg_type_is()`：AstrBot `MessageType` 是枚举，历史代码用字符串比较恒为 False，导致群聊/私聊分流失效、onboarding/羁绊/角色收集等私聊能力整段被跳过（main.py ×6 / llm_hook ×2 修复）。③ 关系分回写 KV：此前 `Relationship.interact` 只改内存、重启即清零，`mingbu.rel:*` 永远为空，预感梦门槛（score≥40）等关系成长特性永不生效——新增 `_save_relationship` 落库。新增回归测试，全量 pytest passed、ruff 零告警。

**v4.8.117** · **修复：企微渠道 proactive 主动消息 / 欢迎全数发失败**——日志实证主动关怀每周期向企微会话 `context.send_message(session_id, chain)` 走 `send_by_session`，企微客服模式不支持主动推送，5 次/周期失败告警且不省 LLM。根因：企微客服会话是被动响应模式。修复：`track_session` 新增 `platform` 字段（从 `unified_msg_origin` 首段解析），`_maybe_message` 与 `send_welcome` 在企微渠道（wecom / wechat / weixin）直接跳过，既消除噪音又省无用 LLM 调用；非企微渠道行为不变，旧 KV 无 platform 字段安全降级。llm_hook / group.py 两个调用点同步传入 platform。新增 3 条回归测试（wecom 跳过 / weixin 变体 / 落库字段），全量 pytest 1108 passed、ruff 零告警。

**v4.8.116** · **修复：企微超长文本被微信拒收（字节 vs 字符上限）**——企业微信客服消息 content 上限是 2048 **字节**（UTF-8），而 AstrBot 核心 wecom 适配器按 2048 **字符**切分，中文每字 3 字节，切出的每段 ≈ 6144 字节全部超限被微信拒收（长回复用户收不到）。修复：插件在回复发送前检测，超长文本（>1800 字节）按 UTF-8 字节边界切段自发送（每段 ≤1800 字节 < 2048，且字符数必然 < 2048 不会触发 AstrBot 二次切分），再压空核心回复；仅对企微渠道生效。字节级切分优先在标点/换行断句、绝不切断多字节字符、无损还原。新增 7 个回归测试，全量 pytest 1105 passed。

**v4.8.115** · **渠道与服务配置对齐「外部服务」框架（两渠道互斥）**——能力服务商面板改为与下方「外部服务」一致的<b>双渠道设计</b>：<b>① 拉取模式</b>＝只选 AstrBot 已配置服务商（或手输 Provider ID），<b>模型跟随该服务商默认</b>，不再单独选模型（删除拉取面板内的 `provider_<role>_model` 输入）；<b>② 自定义</b>＝填自有 Key / 接口，模型从顶部「模型统一配置」区拉取的列表中选择，独立调用不依赖 AstrBot 服务商。两种模式互斥（切换即清空另一项），保存时显式清空拉取模式下的旧 `provider_<role>_model` 残留，避免「选了服务商却被旧模型配置劫持」。前端 JS 语法校验通过、全量 pytest 1098 passed。缓存串 `?v=4.8.114`→`?v=4.8.115`。

**v4.8.114** · **紫占工具接入 + 出生校验接线 + 探索完成标记 + 占卜防重（对接 mingbu v0.20.26）**——四项核心修复/增强：
① **紫占（紫微斗数占卜）LLM 工具** `divination_zizhan`：对接后端新增 `/api/zizhan` 端点，以「问事当下」起紫微盘出三层卦断吉凶，无需出生信息；带同用户+同问题 TTL 120s 防重缓存（紫占以当下时空起盘，重复起卦会得到不同卦象），含 `format_zizhan` 三层卦摘要 + 后端占断 prompt grounding；② **出生校验接线**：`query_bazi` / `query_ziwei` / `query_combined` / `query_interpret` 排盘前调用 `_validate_birth`（年份范围/月份/闰年日期/钟点/性别），非法参数直接拦截返回提示，不再空耗后端；③ **探索完成标记修复**：`query_bazi` / `query_ziwei` 排盘成功后补 `tracker.exploration_completed`（与合参/六爻/奇门一致，祝福钩子可拿到命盘数据）；④ **占卜工具防重**：六爻/奇门/塔罗同用户+同问题 TTL 120s 防重缓存，「卦不二起」。全量 pytest 1098 passed、ruff 零告警。缓存串 `?v=4.8.113`→`?v=4.8.114`。

**v4.8.113** · **修复：自定义模式测试按钮"失效"（状态区被隐藏）**——用户反馈 v4.8.112 自定义模式下点测试按钮无反应。根因：测试状态区 `.mc-test-status` 位于**拉取模式面板内部**，自定义模式下该面板 `display:none` → 测试结果写入隐藏元素，看起来像按钮失效（实际请求已发出）。修复：把状态区从拉取面板**移到模式切换与面板之间的公共位置**，两种模式共用、结果始终可见；CSS 基类 `.mc-test-status` + `.ok/.err` 变体与位置无关，无需改样式。app.js 语法校验通过。全量 pytest 1078 passed、ruff 零告警。缓存串 `?v=4.8.112`→`?v=4.8.113`。

**v4.8.112** · **能力服务商双模式补全：自动补全 Key/Base + 测试按钮 + 模式持久化**——承接 v4.8.111 的三项体验补全：① **自动补全**：自定义模式选中模型后，从顶部「模型统一配置」自动补全同卡片的 API Key/接口地址（`mcAutofillCustom`，与外部服务 `svcAutofillCard` 同款，仅空字段补全、不覆盖手填）；② **测试按钮修复**：拉取模式原逻辑依赖已保存配置而失效——改为直接测面板里填写的服务商 ID（空则明确提示），自定义模式则走直连测试（后端 `_test_provider` 新增 api_key+api_base 直连分支，标记 `using_direct`，失败返回结构化错误）；③ **模式持久化**：拉取/自定义切换状态写入 localStorage（`mingbu.mcMode.{role}`），刷新不再丢失——localStorage 记忆优先，首次进入按"已填自定义值"智能推断。含直连测试 ×2（直连连通/直连失败结构化错误）。全量 pytest 1078 passed、ruff 零告警。缓存串 `?v=4.8.111`→`?v=4.8.112`。

**v4.8.111** · **能力服务商双模式：借鉴「外部服务」独立直连方案**——用户问"服务商空+模型填,但模型不属于默认服务商怎么办"(跨服务商用模型必 400)。本版把能力服务商区(主回复/梦境/成长/主动关怀)改造成与「外部服务」一致的双模式卡片：🔗**拉取模式**(现状,AstrBot Provider 下拉+模型)与 ✏️**自定义模式**(新增 `{task}_api_key`/`{task}_api_base`/`{task}_model` 独立直连)——填了 Key+Base 即走 `_direct_openai_chat` 直连,完全绕开 AstrBot Provider 体系,**「服务商」与「模型」彻底解耦**,不再受默认服务商限制;直连失败自动回退 Provider。`llm_request` 新增直连优先分支(返回 SimpleNamespace 兼容 completion_text 契约),失败回退不崩。含直连测试 ×2(直连优先/失败回退)。全量 pytest 1076 passed、ruff 零告警。缓存串 `?v=4.8.110`→`?v=4.8.111`。

**v4.8.110** · **45007 源头预防：单段钳制 150 字（企微吞错误修正盲区）**——用户反馈"最新版仍触发 45007"。日志铁证：`wecom_event:140 微信客服上传语音失败: 45007` 后紧接 `直连共发送 1/1 段语音`（误报成功）且**无二分重试日志**——说明 **45007 在企微适配器内部被吞掉、`event.send()` 不抛异常**，v4.8.106 的二分重切依赖异常检测在此场景不生效（盲区）。既然无法感知失败，只能**源头预防**：`TTS_MAX_CHUNK_CHARS` 默认 200→**150**（按最保守语速 ~2.5 字/秒，150 字≈60s 内，任何音色都不超微信客服 60s 上限）；`_send_tts` 读取 `tts_max_chunk_chars` 时**钳制上限 150**（用户面板配 300 也被钳回，298 字单段实测 45007 且文本被误压）。含钳制测试 ×2（默认值=150/源码含 min 钳制/切段 ≤150）。全量 pytest 1074 passed、ruff 零告警。缓存串 `?v=4.8.109`→`?v=4.8.110`。

**v4.8.109** · **图片链路根治：请求钩子同步注入当前图摘要**——日志实证主 agent 收到图片后自己调 `astrbot_file_read_tool` 读企微压缩图，但文件在 temp 而工具在 workspace 找 → 路径错误 → 模型空转，回复"发过来吧"（08:45 实证：模型尝试读 08:04 的旧图文件失败），用户体感"卡死"需再发消息才激活。本版把 `_describe_images` 的视觉调用提取为 `_summarize_image_urls()`，`_inject_current_image_hint` 升级为**异步同步摘要注入**：LLM 调用前在请求钩子里直接调视觉模型生成当前图摘要（`asyncio.wait_for` 25s 超时保护，绝不过度阻塞），把「内容大致是：…直接基于它回应，不用再调用任何文件读取工具」注入请求上下文——模型直接拿到图内容，**不再自己读文件**（根治路径错/卡死/串扰三合一）；摘要失败/超时回退泛化"以当前图为准"提示。含摘要注入测试 ×3（摘要注入/无图或关闭不注入/摘要失败回退）。全量 pytest 1072 passed、ruff 零告警。缓存串 `?v=4.8.108`→`?v=4.8.109`。

**v4.8.108** · **修复：图片转述上下文串扰（新图被识别成旧图）**——用户发新图后，模型识别完却描述了上一张图（日志 08:30 实证：新图被描述成上一张"奔跑角色"图）。根因：`_on_image_vision` 对图片**异步**写摘要（视觉调用慢），主 agent 回复时拿不到新图信息，只能靠历史记忆里"上一张图"的 `[看图]` 摘要 → 模型凭旧记忆猜。修复：① 请求钩子新增 `_inject_current_image_hint`——当前消息含图时注入「对方刚发了一张新图片，以当前图为准，不要凭记忆猜；看不清就自然道歉」提示（图片转述启用时）；② `_describe_images` 摘要带时间戳标记（`[看图 08-30 08:30]`）区分新旧。含图片提示注入测试 ×2（含图注入/无图或关闭不注入）。全量 pytest 1071 passed、ruff 零告警。缓存串 `?v=4.8.107`→`?v=4.8.108`。

**v4.8.107** · **语音链路输出侧兜底：机器腔过滤 + 发送结果回注**——多轮日志复盘发现两个"提示词管不住"的问题：① 模型仍会把系统说明漏进回复（`(TTS active)`「TTS功能也正常」「注：当前仍无法发送语音」等）并被 TTS 原样朗读；② 模型回复后看不到发送结果，用户问"语音呢/没听到"时脑补「语音发送失败了」（日志实证 07:57:40 模型道歉说语音失败，实际 2/2 段已成功发出）。本版输出侧根治：新增 `_strip_machine_talk()`——TTS 朗读前用正则剥离 `(TTS active)`/`注：…`/`系统…语音…插件…`/`语音通道功能正常` 等机器腔模式（不误伤正常文本）；`_tts_on_response` 把每次发送结果写入 KV `mingbu.last_tts_result:{uid}`（ok/reason/ts），请求钩子仅在用户提及"语音/声音/听/收到"时回注真实结果——成功则模型可安心说"已经发了"，失败则自然地说"声音没传好，用文字补上"，彻底告别幻觉道歉。含机器腔过滤测试 ×2 + 结果记录测试 ×2。全量 pytest 1069 passed、ruff 零告警。缓存串 `?v=4.8.106`→`?v=4.8.107`。

**v4.8.106** · **修复：微信客服 45007（语音超 60s）自动二分重切**——日志实证 `微信客服上传语音失败: Error code: 45007, message: playtime out of limit`（合成成功 979KB 但上传被拒），且「直连共发送 1/1 段语音」仍误报成功。根因：① `_send_voice_with_backoff` 只处理 95001 限流，45007 直接判失败且调用方计数不受影响（合成成功日志误导）；② 长文本段（用户配 `tts_max_chunk_chars=300` 超默认 200）合成后超微信 60s 上限。修复：`_send_voice_with_backoff` 识别 45007 返回 `"too_long"` 信号；新增 `_send_voice_chunk_retry_split`——直连路径遇 45007 自动把该段文本二分再合成发送（min_chars=5 仅防递归死循环，不误杀短语义语音；递归正确传 min_chars）；Provider 回退段 45007 记失败不误计成功（`is True` 才计）。含 45007 二分测试 ×3（触发二分/过短放弃/Provider 不误计）。全量 pytest 1065 passed、ruff 零告警。缓存串 `?v=4.8.105`→`?v=4.8.106`。

**v4.8.105** · **修复：运势时间范围被误采为出生年份**——用户说「分析我在2022-2035年的感情运势」，插件把 `-2035年` 的后段当成了出生年份（日志：`收集到 … 出生年份: 2035`），档案被写坏。根因：① filter 正则 `(?:我是?|我的).*?(\d{4})\s*年` 对"我…2035年"宽泛命中；② `BIRTH_YEAR_RE = (\d{4})\s*年` 全句搜又命中"-2035年"，无范围排除。修复：`BIRTH_YEAR_RE` 收紧——负向后瞻 `(?<!-)` 排除范围后段、负向前瞻 `(?!之|以|前|后)` 排除未来参照（但不排除"年"后跟数字，那是月日如"1990年5月20日"）；`_on_birth_year` 内新增 `_extract_birth_year_from_msg` 第二道语义闸——必须命中出生语义词（出生/生于/生日/生的/年生/属X）+ 年份须 ≤ 当前年（未来年份不可能是出生年）。含出生年采集语义测试 ×7（范围年排除/无语义忽略/正常句式/有出生词仍排除范围/未来年拒绝/超范围拒绝/属相句式）。全量 pytest 1062 passed、ruff 零告警。缓存串 `?v=4.8.104`→`?v=4.8.105`。

**v4.8.104** · **对话拟人化：根治「冒出指令/系统说明」**——用户反馈回复里偶现 `(TTS active)`「注：当前仍无法实际发送语音文件」「【系统提示】」等出戏内容。根因：① 运行时注入块用了「指令式方括号标签 + 祈使句清单」（`[说不清的直觉·来源不可透露]`、`[后台留意·来源不可透露]`、`[隐藏角色解锁]`、`[系统能力提示]…不要…不要…`），LLM 极易把这些「规则」照抄进回复；② 系统提示缺少「不原样复述内部说明」约束。本版双管齐下：所有注入块改为「自然认知式」描述（角色内在感觉，无方括号标签、无规则清单、无命理禁用词——darkchannel 测试守护不泄漏）；`_build_persona_block` 顶层追加「说话的底线」隐形约束——方括号/圆括号说明只是内部参考，绝不原样说出口，不出现 `(TTS active)`/「系统提示」/「TTS/语音合成」等机器腔。含注入文本规约测试 ×3（weave 无标签/weave_watch 无标签/人格块含隐形约束）。全量 pytest 1055 passed、ruff 零告警。缓存串 `?v=4.8.103`→`?v=4.8.104`。

**v4.8.103** · **复合 Provider ID 兼容补全（v4.8.102 只修了主解析路径，本版覆盖其余全部）**——系统排查发现同样的「Dashboard 复合 ID（`服务商/模型`）精确匹配落空」问题还潜伏在 4 条独立 provider 解析路径：`_pick`（STT/TTS 共用）、`get_rerank_provider`、`get_embedding_provider`，以及管理面板「连通性测试」端点的显式 provider_id 分支——用户若在 TTS/STT/Rerank/Embedding 下拉选了复合 ID（如 `openai/models/whisper-1`），同样会解析失败走默认或报「未找到 Provider」。本版把这 4 处全部改为经 `_provider_id_candidates()` 匹配（先精确、再剥离 `/模型` 后缀），与 v4.8.102 的 `get_provider_async`/同步 `get_provider` 对齐，实现复合 ID 兼容全路径覆盖。含 `_pick`/rerank/embedding 复合 ID 解析测试 ×2。全量 pytest 1052 passed、ruff 零告警。缓存串 `?v=4.8.102`→`?v=4.8.103`。

**v4.8.102** · **修复：AstrBot Dashboard 复合 Provider ID（`服务商/模型`）解析失败**——用户在命灵面板「能力服务商」选了 AstrBot 里的 `google_gemini/models/gemini-3.1-flash-lite-preview`（面板下拉直接取 Dashboard API 的复合 id），但 `provider_manager.inst_map` 的键是纯服务商 ID（`google_gemini`），`get_provider_by_id` 精确匹配永远落空 → 图片转述/梦境/成长等任务全部解析不到指定服务商，走默认 provider 或失败（图片转述失效时主 agent 只能自己读图，进而出现模型幻觉工具 `str_opensound`、文件路径错误等连锁问题）。本版新增 `_provider_id_candidates()`：解析 provider 时先精确匹配原串，失败则剥离 `/模型` 后缀再按纯服务商 ID 匹配（`get_provider_async` 与同步 `get_provider` 均生效），与 Dashboard 下拉的复合 id 格式对齐。含复合 ID 解析测试 ×4（候选展开/异步解析/精确优先/同步解析）。全量 pytest 1050 passed、ruff 零告警。缓存串 `?v=4.8.101`→`?v=4.8.102`。

**v4.8.101** · **紧急修复：`extra_user_content_parts` 字符串注入在 AstrBot 4.27.4 下导致 agent run 崩溃**——用户发送消息时日志报 `Error occurred while processing agent: 'str' object has no attribute 'model_dump_for_context'`。根因：AstrBot 4.27.4 的 `ProviderRequest.assemble_context()` 对 `extra_user_content_parts` 逐项调用 `model_dump_for_context()`，**纯字符串必崩**；而命灵 hooks 全部 8 处（persona/character/blessing/counseling/crisis/TTS 能力声明）历史上一直直接 append 字符串，在旧版 AstrBot 可用、4.27.4 收紧后必崩（触发任何 hook 注入即整个 agent run 失败，用户收不到回复）。本版新增 `util.inject_text_part(req, text)`：优先构造官方 `TextPart`（`astrbot.core.agent.message`），测试桩/旧版回退为带 `model_dump_for_context()` 的兜底对象；hooks 全部 8 处改为经它注入，与 AstrBot 官方用法（`astr_main_agent.py` 同样 append TextPart）对齐。含 ContentPart 契约回归测试 ×1（注入元素必须支持 model_dump_for_context）。全量 pytest 1046 passed、ruff 零告警。缓存串 `?v=4.8.100`→`?v=4.8.101`。

**v4.8.100** · **TTS 开关语义修正：`tts_auto_for_long_text` 只控长文本路径，不再当总闸**——用户关掉「常文本自动语音」后，发现情绪音色路由也失效、语音彻底不触发（模型还幻觉「未安装语音合成插件」、编造 `/AstrBot/data/temp/lilith_empathy.mp3` 调 `send_message_to_user` 报 path not exist）。根因：`_tts_on_response` 把 `tts_auto_for_long_text` 当作总闸，False 时直接 return，情绪语义路径被一并短路。本版修正：`tts_enabled` 是唯一总闸；`tts_auto_for_long_text` 只拦截「长文本」触发（reason=="长文本" 时跳过），情绪语义路径（安慰/开心等短回复）不受影响——用户可只关长文本、保留情绪路由。同时新增 TTS 能力声明注入（TTS 启用时）：告知模型语音由插件自动合成发送，禁止它自称「无法发送语音/未安装语音合成插件」、禁止自行构造语音文件路径或调 send_message_to_user 发 record——根治模型幻觉语音文件。含开关语义测试 ×2（长文本被关 + 情绪短回复仍触发）。全量 pytest 1045 passed、ruff 零告警。缓存串 `?v=4.8.99`→`?v=4.8.100`。

**v4.8.99** · **TTS 语义驱动触发：不止长文本，强情绪回复也读出来**——语音发送能力本就存在（直连 OpenAI 兼容 `/audio/speech` 或 AstrBot TTS provider），此前只有「≥500 字长文本」才触发，用户 130 字左右的日常回复永远只发文字，且模型在回复中会幻觉「没有能力发送语音」。本版把 `tts_should_trigger` 升级为语义判定：新增强情绪路径——回复带明显情绪（joy/excited/sad/angry，|情绪权重|≥0.5）且长度 ≥10 字即触发语音（复用 `emotion_detect` 引擎，零新依赖）；长文本(≥阈值)仍必然触发；中性短文本仍只给文字（随机率保持 0，不吵人）。测试用「别难过，我一直都在你身边呀」这类真实安慰短句验证：语义触发后压住核心文本、只发语音。含语义触发测试 ×3（含纯函数路径断言）。全量 pytest 1043 passed、ruff 零告警。缓存串 `?v=4.8.98`→`?v=4.8.99`。

**v4.8.98** · **语音 400 真正根治（v4.8.97 剥离目标挂空，本版源码级修正）**：核查 AstrBot 4.27 源码后确认三重根因——① `ProviderRequest` 根本没有 `messages` 属性（只有 `contexts`/`prompt`/`audio_urls` 等），v4.8.97 在 `on_llm_request` 钩子里剥 `req.messages` 永远拿到 `None`，剥离从未执行；② 即便剥 `req.contexts` 也无效——runner 构造时已把 `req.contexts` **快照**进 `run_context.messages`（`bind_checkpoint_messages`），钩子在 runner 构建后才触发，最终 payload 用的是快照；③ AstrBot 唯一对快照生效的净化点是 `_iter_llm_responses` 里的 `_sanitize_contexts_for_provider`，它只在 provider 配置了 `modalities` 且不含 `audio` 时才把 `audio_url`/`input_audio` 分片替换成 `[Audio]` 文本，未配置则原样透传 → Qwen3-VL 等非全模态主模型每轮 400 落 fallback。本版根治：`on_llm_request` 钩子开头调用新增的 `ensure_chat_provider_no_audio(ctx)`，把当前会话 chat provider 的 `modalities` 幂等修正为不含 audio 的 `["text","image","tool_use"]`，AstrBot 每轮自动净化历史语音分片（含已快照上下文）；同时把剥离目标修正为 `req.contexts`（OpenAI dict 格式）作防御兜底。STT 转写文本 `[语音转写] …` 是 text 分片天然保留，不丢语义。含 provider modalities 修正 + contexts 剥离测试 ×20。全量 pytest 1040 passed、ruff 零告警。缓存串 `?v=4.8.96`→`?v=4.8.98`。

**v4.8.96** · **修复 v4.8.95 配置回写失效（self.config 缺失）**：`MingbuStar.__init__` 未显式 `self.config = config`，而 AstrBot 官方插件模板要求插件自己持有 config（Star 基类不自动存）——导致 `_sync_astrbot_static_config` 拿不到 AstrBotConfig、`save_config()` 回写静默跳过，AstrBot 原生「插件配置」页永远不更新、与后台页不一致。本版按官方模板补齐 `self.config = config`，后台保存后真正落盘到 `data/config/<plugin>_config.json`，两页彻底同步。全量 pytest 1013 passed、ruff 零告警。

**v4.8.95** · **插件配置与后台核心配置统一（以 KV 后台页为最终真源 + 回写 AstrBot 静态 config）**：新增 `_sync_astrbot_static_config`——命灵后台配置页 `/config` 保存后，除写 KV 运行时覆盖层（最终真源）外，再调用官方 `self.config.save_config()` 把本次键回写进 AstrBot 静态 config 并持久化到 `data/config/<plugin>_config.json`，使 AstrBot 原生「插件配置」页与命灵后台页显示同一份值——根治「后台改了原生页却显示旧值」的双入口不同步。防御式：无 `self.config` 或 `save_config` 时静默跳过，不影响后台保存（KV 仍为真源）。全量 pytest 1012 passed、ruff 零告警。

**v4.8.94** · **STT「填了即用」：不再需要手动开总开关**——`stt.py` 新增纯函数 `is_stt_enabled`：仅当 `stt_enabled` 显式为 false 才强制关闭；未显式设置且已填 `stt_api_base` 直连凭据时自动启用并转写语音（面板填三项即可用，无需再找「启用语音转文字」开关）。同步把 `_conf_schema.json` 中 `stt_enabled` 误导性的 `default` 由 true 改 false（与代码 `get(...,False)` 真实默认一致），并修正「需 AstrBot 配置 STT provider」的过时描述。全量 pytest 1010 passed、ruff 零告警。

**v4.8.93** · **语音转文字（STT）直连增强**：`stt.py` 直连分支门槛由「`stt_api_base` 与 `stt_api_key` 同时非空」放宽为「`stt_api_base` 非空即可」——云端（SiliconFlow `https://api.siliconflow.cn/v1` + `FunAudioLLM/SenseVoiceSmall`，复用主模型 key）或本地私有化（OpenAI 兼容 SenseVoice，`stt_api_key` 留空、无需鉴权）两种接法都覆盖，且不要求安装 AstrBot STT provider。语音消息转写后以 `[语音转写] …` 文本注入对话，主模型不再收到 `audio` 分片，规避 `400 · Only text and image_url are supported` 与 fallback 投毒。同步修正 README / CONFIG_TEMPLATE / .env.example 中「STT 不由插件读取」的滞后描述并补充直连用法。全量 pytest 1006 passed、ruff 零告警。

**v4.8.92** · **命理深度解读双通道（mingbu 侧 AI 分析 + AstrBot 角色润色）**：新增 LLM 工具 `query_interpret`——调用 mingbu `/api/interpret` 在后端完成「命盘 + 知识库 grounding」的 AI 解读并流式回传最终文本，AstrBot 侧仅做「角色口吻重写」（选项 B），命盘 grounding 不再进入 AstrBot 上下文历史，结构性根治「上下文雪崩 / 回复被吞」，且长对话下贵模型上下文约减半。provider 解析优先级：① 插件配置 `mingbu_interpret_*`（可指定专属服务商）；② 留空则透传 AstrBot 当前对话服务商的 key/base_url/model（与 AstrBot 同一个 provider）；③ 都取不到则由 mingbu 自身环境变量兜底。`MingbuClient.interpret` 新增 `messages` 参数以传递对话历史做连贯多轮解读；调用失败自动回退本地合参（`query_combined`）。全量 pytest 1003 passed、ruff 零告警，缓存串 `?v=4.8.91`→`?v=4.8.92`。

**v4.8.91** · **排盘工具「晚子时契约」+ 紫微知识库上下文预算上限（根治回复被吞）**：① 三个出生类 LLM 工具（query_bazi/query_ziwei/query_combined）docstring 增补「晚子时契约」——`birth_hour=23` 直接传原始年月日+23，插件内部 `_apply_late_zi_swap` 自动按次日排盘，明确禁止模型因「晚子时」自行 +1 日期或反复重排（此前生产日志实证模型反复调用 query_ziwei 重排、上下文被撑爆后人设被截断、进而幻觉出不存在的工具/发图，造成「怎么不回我」）。② `build_ziwei_knowledge_grounding` 增加 `max_chars=6000` 优先级预算上限——各小节（命宫主星/宫位/四化/入宫/组合/辅星/六吉六煞/杂曜/运限/古籍应证）按核心→次要排序拼接，超预算时从低优先级段裁剪，病理级全星曜命盘从 ~10.8k 字符收敛到 ≤6k，根除单/多次调用对上下文的雪崩式膨胀；典型命盘完整保留。同步 bump 静态资源缓存串 `?v=4.8.90`→`?v=4.8.91`。全量 pytest 1001 passed、ruff 零告警。

**v4.8.90** · **管理面板「对话检视」下拉框配色修复**：`对话检视` 页 3 个下拉框（用户筛选 ×2、类型筛选 ×1）此前是原生 `<select>`——未做 `appearance:none`、未强制 `color-scheme:dark`、未给 `option` 设暗底，导致在浅色系统下弹层渲染成「白底 + 亮字(#e8eaf0)」低对比度、文字难以辨认。修复：与核心配置页 `.config-input` 下拉对齐——① `appearance:none` + 自绘 SVG 箭头；② 收起态实底 `var(--bg-2)`(#0f121f) + 亮字，对比度约 14:1；③ `color-scheme:dark` + `option` 显式暗底(#151826)，弹层独立于操作系统主题（浅色系统下同样清晰）；④ 补全悬停/聚焦/禁用/选中五态。同步 bump 静态资源缓存串 `?v=4.8.41`→`?v=4.8.90`（此前缓存串滞后，CSS 改动可能被浏览器缓存遮蔽）。全量 pytest 通过、ruff 零告警。

**v4.8.89** · **去重逻辑重构简化 + 日志降噪**：应「重新设计、保证完整且简化不繁琐」要求重构微信重投去重——① 收拢为单一入口 `_is_replay(event)`（请求钩子只一行调用），内部拆 `_is_seen`/`_mark_seen` 两个短方法；② 字段 `_replied_msgids` 更名为 `_seen_msgids`（现在是请求阶段标记「已见过」而非「已回复」，语义更准）；③ 重投拦截日志从 WARN 降为 DEBUG（这是预期内的正常保护动作，不该刷告警、也避免日志被 LLM 或排障者误读成「插件作妖/流量防抖」，见生产日志 08:02:26 爱丽丝误判）。功能完整性不变：同 msgid 在 TTL 内只放行一次、其余 LLM 调用前短路。全量 pytest 通过、ruff 零告警。

**v4.8.88** · **微信重投去重端到端回归加固**：在 v4.8.87 之上补充端到端闭环回归测试 `test_dedup_full_flow_three_deliveries_one_reply`——模拟生产日志场景（同一条语音被微信重投 3 次），断言「request 钩子对第 2/3 次投递短路（3 次投递只调 1 次 LLM）且第 1 次投递启动的 run 的唯一响应在 response 钩子中完整放行（不压空、不丢弃、不额外发送）」。经全量代码审查确认：无 follow-up 时重投短路、有 follow-up 时合并单响应、run 完成间隙重投短路——三种时序均只产生 1 条回复。全量 pytest 通过、ruff 零告警。

**v4.8.87** · **移除响应钩子去重兜底（修复 follow-up 合并场景误杀正常回复）**：v4.8.86 在响应钩子保留的「重复 msgid 压空回复」兜底被证伪——微信语音重投被 AstrBot follow-up 机制合并进同一 run 时，唯一且正常的响应挂在原始消息上，其 msgid 已被 request 阶段标记，兜底将其误判为重复而压空（生产日志 07:34:38 实证「兜底压空回复 + skipping respond stage」，用户收不到任何回复）。本版彻底移除响应钩子的 msgid 检查与压空逻辑，去重只保留在 `_on_llm_request` 请求钩子（LLM 调用前短路）——无 follow-up 时重投仍只调 1 次 LLM，有 follow-up 时正常回复照发。更新去重回归测试：同 msgid 的正常响应不再被压空。全量 pytest 通过、ruff 零告警。

**v4.8.86** · **微信重投去重前置到 LLM 调用前（真拦截）**：v4.8.84 的去重放在 LLM 响应钩子，只能「丢弃命灵自己的后续处理」——LLM 调用照跑、AstrBot respond 照发，用户仍收到重复回复（生产日志实证「丢弃本次回复」与 `Prepare to send` 同一毫秒出现）。本版把去重前置到 `_on_llm_request` 请求钩子：首次进入即标记 `msgid`，重投在 LLM 调用前 `event.stop_event()` 短路——既省 LLM 配额（同一消息 3 次投递只调 1 次 LLM），又真正不重复回复。更新去重回归测试覆盖「首次放行 + 重投短路」行为。全量 pytest 通过、ruff 零告警。

**v4.8.85** · **主动消息崩溃修复 + 夜任务错峰**：① 修复 AstrBot v4.27.3 导入变更——`Plain` 从 `astrbot.api.event` 挪到 `astrbot.api.message_components`，proactive.py 原整体导入整条 `ImportError` 致主动消息全程崩溃（`'str' object has no attribute 'chain'`）；拆成两条独立 `try/except` 导入，互不拖累。补 `test_proactive_chain_import`。② growth/dream 夜任务批量遍历活跃用户时，每用户之间插入 4–5s 随机串行延迟，错峰避免 LLM 配额（Gemini 429）被瞬时打满。补 `test_nightly_stagger`。全量 pytest 通过、ruff 零告警。

**v4.8.84** · **TTS 模式改造（短文本文字 / 长文本语音）+ 微信重投去重**：① TTS 策略改为「短文本（<500 字）只给文字、长文本（≥500 字）转语音」，关掉原先短文本随机朗读（默认随机触发概率置 0），避免「既文本又语音」；长文本语音成功发出后压住核心文本回复，语音整段失败时回退文字不丢失。阈值 500 字做成面板可配项「长文本转语音阈值(字)」。② 新增「没听清 / 听不清 / 转文字」等表述的拦截：直接从历史调出上一条回复以文字重发，并经 `event.stop_event()` 短路、不调 LLM（省 token、秒回）。③ 修复微信客服 webhook 在回调未及时 ack 时重投同一条消息、被当成新消息多次处理的问题——回复钩子入口按 `message_id` 做 TTL=120s 去重，重投直接丢弃。④ 修复 Genie 回退路径 `get_audio()` 不接受 `voice` 关键字（inspect 探测签名，不接受则不传）。补 9 条回归测试（短→文字不压、长→压文本、没听清短路、msgid 去重、Genie 无 voice 不报错等）。全量 pytest 通过、ruff 零告警。

**v4.8.83** · **修复长文本 TTS 超微信 60 秒限制（45007）**：命灵此前把整段长回复一次性合成，单条语音超过微信客服 60 秒上限，上传报 `45007 playtime out of limit` 导致语音发不出。新增按句切分逻辑 `_split_tts_chunks`（优先在断句符。！？处切分，无断句符则硬切），`_send_tts` 改为逐段合成、逐条发送 `Record` 语音（直连 + AstrBot Provider 两路径均支持）；新增可配置项 `tts_max_chunk_chars`（默认 200，按 ~4.5 字/秒留安全余量，语速偏慢音色可调小）。管理面板 TTS 卡片新增「单段最大字数」输入框。补 6 条 `_split_tts_chunks` 单测。全量 pytest 通过、ruff 零告警。

**v4.8.82** · **新增 TTS 音色 (tts_voice) 配置项**：用户可在插件配置（管理面板 TTS 卡片新增「音色」输入框）直接指定语音合成音色，直连与 AstrBot Provider 两种路径均生效。直连路径把 `tts_voice` 透传给 `_direct_openai_speech` 的 `voice` 参数（经 `_resolve_tts_voice` 适配：SiliconFlow 裸名自动补成「模型:音色」，缺省 alex；OpenAI 原生用 alloy）；AstrBot Provider 路径在 `tts_voice` 非空时以 `get_audio(text, voice=...)` 指定音色、失败回退默认。连通性测试的 TTS 用例同步透传 `tts_voice`。补 5 条回归测试（`_resolve_tts_voice` 四态解析 + `_send_tts` voice 透传）。全量 pytest 通过、ruff 零告警。

**v4.8.81** · **修复子任务 LLM 全线失效（根因级）**：真实 AstrBot 的 `Context` **没有** `llm_request()`（只有 `llm_generate(chat_provider_id=...)` 与 `get_using_provider_async()`），而 `ProviderRouter.llm_request` 的「回退默认服务商」分支判据是 `hasattr(ctx, "llm_request")`，真机上恒为 False——凡未单独配置专属服务商的子任务（图片转述 / 梦境 / 成长反思 / 主动关怀 / 新闻整理 / 主动搜索 / 合并转发转述）一律返回 `None` 并打 `没有可用 Provider，任务 X 失败`。同时修复第二处：`provider_manager.get_provider_by_id` 在真实 AstrBot 中是 `async def`，被同步调用后返回的 coroutine 被当成 Provider 缓存，导致**即使填了 `provider_xxx` 也解析不到**，并刷 never-awaited 警告。改造：新增 `get_provider_async()`（正确 await）+ `get_default_chat_provider()`（`get_using_provider_async` → `get_using_provider` → `get_all_providers` → `provider_insts` 四级兜底）+ `default_llm_request()`；同步 `get_provider()` 加 coroutine 防护；面板「服务商连通性测试」不再误报「Provider 不支持 text_chat」。补 13 条回归测试（用贴近真机结构的假 Context：无 `llm_request`、异步 `get_provider_by_id`）。全量 pytest 通过、ruff 零告警。

**v4.8.80** · **修复 TTS 语音发送崩溃**：`_send_tts` 直连硅基流动 CosyVoice2 合成语音成功，但发送时对企业微信等平台 `event.send` 裸传 `Record(...)`，缺少 `.chain` 属性而抛 `AttributeError`，语音发不出去。改为用 `MessageChain([Record(...)])` 包裹后再 `event.send`；两处 TTS 发送（直连 + AstrBot Provider 回退）统一修复。补 1 条回归测试。全量 pytest 通过、ruff 零告警。

**v4.8.79** · **修复管理面板下拉箭头重叠**：`<input list>` 类控件（主回复服务商、能力服务商等）在 Chromium 系浏览器中原生下拉箭头未被 `appearance: none` 完全隐藏，与自定义 `.combo-arrow` 箭头重叠；新增 `::-webkit-calendar-picker-indicator` 隐藏规则，仅保留一套可点击箭头，UI 恢复清爽。全量 pytest 通过、ruff 零告警。

**v4.8.78** · **角色图鉴展示解锁进度**：`/角色图鉴` 对尚未集齐可见角色的用户，如实给出「再和当前角色多培养羁绊（好感度达到 X）就能解锁下一个角色」的进度提示，X 由 `CharacterCollection.next_unlock_threshold` 按已解锁可见角色数推算（对应驱动解锁的当前角色羁绊值），让「发现 → 解锁 → 切换」闭环真正可操作。新增 `next_unlock_threshold` 静态方法，补 3 条回归测试。全量 pytest 通过、ruff 零告警。

**v4.8.77** · **新增 `/角色图鉴` 指令（角色发现）**：私聊专属，列出全部 7 个角色（6 可见 + 隐藏款），标注「已解锁 / 未解锁 / 当前角色」与解锁提示，让用户先知道有哪些角色、如何解锁，再去 `/切换角色`，使 v4.8.76 的切换方案真正闭环可用；群聊优雅拒绝。补 3 条回归测试。全量 pytest 通过、ruff 零告警。

**v4.8.76** · **丰富角色人设 + 终端用户角色切换通道**：① 新增角色人设富字段（background 角色底色 / catchphrases 口头禅 / taboos 底线分寸 / pet_names 对用户的称呼 / sample_dialogue 风格示例 / emoji_usage 表情倾向 / verbosity 话痨度），以独立的 `PRESET_PERSONA_RICH` 映射承载、不改动原有大模板，并通过 `_enrich_preset_data` 为旧 KV 中已存储的预设卡片自动补回（被用户编辑过的非预设角色不补）；`CharacterCard` 读写与 `update_character` 同步支持。② 重写 `to_persona_prompt` 为更有指令性的「角色代入」提示（角色身份不可破功、底色、说话方式、口头禅、称呼、风格示例、底线分寸、终极约束），让 LLM 真正以角色口吻说话。③ 新增终端用户切换通道：私聊 `/切换角色`（无参数列出已解锁角色、带编号/名字/特征切换、支持「下一个」循环）与 `switch_character` LLM 工具（自然语言切换），均经每用户 `collection.switch_to_character` 切换，群聊优雅拒绝。④ 修复隐藏角色解锁后无法切换的缺陷（`switch_to_character` 现会把已解锁的隐藏角色纳入 unlocked 列表）。⑤ 管理面板角色卡片新增底色/口头禅/话痨度/表情倾向展示。补 16 条回归测试。全量 pytest 通过、ruff 零告警。

**v4.8.75** · **修复角色切换下拉卡「加载中」**：`loadCharacters()` 在请求失败时只刷新角色卡片、没有重置下拉框，导致下拉永远卡在「加载中…」。本版在请求前先把下拉框重置为「选择角色...」占位，失败时改为「加载失败」并 toast 提示；同时修正 `apiGet` 调用方式，让 `uid` 通过 params 对象传递而非手工拼 URL。另外，未选择用户时直接提示「请先选择用户」，避免无 uid 时展示全局解锁状态造成切换再次失败。

**v4.8.74** · **修复角色切换「未解锁或不存在」**：`/collection/all` 曾用全局角色池判定解锁状态，而 `/collection/switch` 按每用户 `collection.unlocked` 校验，导致 UI 显示角色已解锁、切换时却报「角色未解锁或不存在」。本版让 `/collection/all` 支持 `uid` 参数并按每用户 collection 返回解锁状态，前端 `loadCharacters()` 同步带上当前用户 uid；无 uid 时回退到全局角色池保持兼容。补 1 条回归测试。全量 pytest 通过、ruff 零告警。

**v4.8.73** · **修复 Provider 拉取空 + Dashboard 熔断误伤**：① 根因：AstrBot 真实 Provider 对象不直接暴露 `id`/`provider_id`，旧代码只读这两个属性导致「有实例但缺 id」、面板服务商下拉为空；新增 `_extract_provider_id/_extract_provider_name` 统一兼容 dict / 旧模拟对象 / 真实 AstrBot Provider 结构（从 `provider_config["id"]`、`meta().id`、`model_name` 提取）。② Dashboard `/api/v1/providers` 拉取失败会污染全局熔断器，并把「熔断中」错误拼到实例枚举成功的消息里；现为 Dashboard 使用独立熔断器，且仅在实例枚举也失败时才把 HTTP 错误拼入主 message。补 4 条回归测试。全量 pytest 通过、ruff 零告警。

**v4.8.72** · **成长反思「服务商」连通性测试修正**：`diagnostics._test_provider` 对「按任务类型解析不到专属服务商」的两种情形区分处理——① 同时未填模型：本就是「留空则沿用默认服务商」的合法态，改为提示性「通过（沿用默认）」，消除原「未找到任务类型 X 对应的 Provider」的误导红错；② 只填了模型却没填服务商：模型无承载方，报精确错误指引用户去「服务商」框填对应的 Provider ID（即承载该模型的 AstrBot 服务商条目 ID）。补 2 条端到端回归测试。全量 pytest 通过、ruff 零告警。

**v4.8.71** · **紫微四化 grounding 回归正确 + 四化契约测试纠正**：v4.8.68 对「紫微四化检索失效」的修复方向错误——误将「自化」(selfHua/selfHuaList) 当作「生年四化」读取，污染了正确的 siHua 四化（如把武曲化权误判为自化忌）。本版回退 `ziwei_kb.py` 的 `build_ziwei_knowledge_grounding` 仅读取生年四化 `star.siHua`（iztro 的 mutagen 单字 禄/权/科/忌），与后端 `/api/combined` 字段契约一致；自化语义独立、不再混入四化 grounding。同步纠正 `test_ziwei_sihua_selfhua_contract`→`test_ziwei_sihua_contract` 的错误断言（原按 selfHua 构造、与正确代码矛盾），新增「仅含自化时无四化段落」反例。另：经核查，该 bug 的真正根因在后端 mingbu 的 `SIHUA['禄']` 键字面不匹配（SIHUA 键带「化」前缀、extractKnowledge 却传入 iztro 单字 mutagen），已在本机 mingbu 仓库修复 `getSihuaInfo` + 取值处归一化。全量 pytest 通过、ruff 零告警。

**v4.8.70** · **角色收集/羁绊管理真正跑通**：修复三处「数据层已建、接线断裂」——① **角色人设注入**：`_build_persona_block` 私聊时从每用户 `collection.current` 取当前角色，注入 `CharacterCard.to_persona_prompt()`（角色取代固定「灵犀」人格），并修复「当前角色」全局单例被多用户互相覆盖的问题；② **羁绊按角色分开**：新增 `bond.py` 的 `Bond`（每用户 × 每角色，存储 `mingbu.bond:{uid}:{char_id}`），含亲密度/关系模式/羁绊称号（初识→熟悉→挚友→羁绊）/里程碑；③ **羁绊事件接线**：新增 `detect_bond_event` 从消息识别亲密/想静静/受伤/安慰/道歉五类事件，`CharacterHook` 触发当前角色羁绊 `nudge`（此前 `Relationship.nudge` 在非测试代码零调用、refusing/hurt 模式永不可达），称号升级时注入「羁绊升级」提示。角色推荐解锁阈值改用当前角色羁绊亲密度。全量 933 passed、ruff 零告警。

**v4.8.69** · **晚子时换日处理**：23 点（23:00–23:59，晚子时/夜子时）出生，按传统命理主流流派（徐乐吾《子平真诠评注》等）以**次日**推日柱、时柱。新增 `_apply_late_zi_swap`（`date+timedelta` 换日，自动处理跨月/跨年/闰年），`_birth_params` 在 `birth_hour==23` 时日期 +1、时辰归子时；八字/紫微/合参三个排盘工具输出时附加「🌙 晚子时（23 点后出生，按次日排盘）」提示。全量 920 passed、ruff 零告警。

**v4.8.68** · **复盘审查：修正排盘格式化契约 + 补齐命宫/身宫/胎元展示**。经万年历独立验证，八字四柱/大运、紫微命宫/身宫/五行局/主星安星均准确；技法库内容（十神/格局/纳音 30/刑冲合害/六十四卦/奇门九星八门八神/紫微 105 条）逐条核对无误、无数据丢失。本轮修复 4 个真实存在的展示/契约 bug：① 八字用神/喜神/忌神此前因字段名不匹配（后端 `yong/xi/ji` 数组 vs 命灵读 `yongShen/xiShen/jiShen`）**从不显示**，现改为读 `yong/xi/ji` 并 join 展示（兼容旧契约）；② 八字命宫/身宫/胎元此前未展示，现补上；③ 紫微命宫/身宫在「子」（branch=0）时此前被 `if mg` 误判跳过，现改用 `is not None` 判断；④ 紫微四化检索此前读 `siHua`、后端实际字段为 `selfHua`/`selfHuaList`，导致四化/四化入宫/组合格局四化判断失效，现兼容三字段。全量 913 passed、ruff 零告警。

**v4.8.67** · **继续扩充八字/紫微/奇门技法库**：① 八字新增神煞 10（天乙/文昌/羊刃/桃花/驿马/华盖/将星/劫煞/天医/禄神）、六十甲子纳音 30、地支刑冲合害 7 类（六合/六冲/三合/三会/三刑/六害/六破），`build_bazi_knowledge_grounding` 注入神煞/刑冲合害/命局四柱纳音；② 奇门新增九星 9/八门 8/八神 8 详解，`build_qimen_knowledge_grounding` 注入；③ 紫微新增杂曜 11（红鸾/天喜/天姚/天刑/天哭/天虚/咸池/龙池/凤阁/天马/天巫）+ 六吉六煞个体详解（六吉含禄存 7、六煞 6），`build_ziwei_knowledge_grounding` 按命盘出现注入。全量 908 passed、ruff 零告警。

**v4.8.66** · **新写六十四卦卦象知识库**（`technique_kb.py` 的 `HEXAGRAM_KB`）：六十四卦每卦含五行（京房八宫）、《周易》本经卦辞、`《象传》`象辞、判曰（卦意与占断要点）；`_hexagram`/`_format_hexagram` 支持全称/简称查询。`build_liuyao_knowledge_grounding`/`build_meihua_knowledge_grounding` 现按本卦/变卦/互卦名注入「六十四卦卦象」段（卦辞+判曰），六爻/梅花断卦可精确锚定到具体卦象。全量 904 passed、ruff 零告警。

**v4.8.65** · **紫微知识库全部移植补全**（不遗漏）：把 v4.8.64 未移植的剩余维度补全——全局原则 3→7 条（补四化动态引动/本命大限流年分层/健康财务关系安全/分享金句具体性）；新增四化入宫导向 17 条、组合格局导向 29 条、辅星导向 6 组、流年运限导向 5 条、K 线评分导向 5 条、合盘导向 6 条；`ziwei_kb.py` 结构化条目共 **105 条**。新增 learned **古籍应证库**（`ziwei_concepts.json` 124 概念 / `ziwei_geju.json` 9 格局 / `ziwei_qa.json` 309 问答，自 mingbu 复制），`lookup_concept`/`lookup_geju`/`lookup_qa` 按命盘星曜/格局查古籍原文片段注入。`build_ziwei_knowledge_grounding` 现注入：原则 + 主星 + 宫位 + 四化 + 四化入宫 + 辅星 + 组合格局 + 运限 + 古籍应证。全量 902 passed、ruff 零告警。

**v4.8.64** · 移植**紫微知识库** + 编写**六爻/梅花/奇门三术技法**：① 新建 `ziwei_kb.py`，从后端 `lib/ziwei/knowledge-db/entries/` 移植 14 主星导向、十二宫导向、四化导向 + 3 条全局原则，`build_ziwei_knowledge_grounding(chart)` 按命宫/身宫主星、高价值宫位、四化分布精准注入，接线 `query_ziwei`/`query_combined`；② `technique_kb.py` 新增八卦类象（8 卦完整类象）与六爻/梅花/奇门三套结构化技法（六爻：世应六亲用神六神动变旺衰空破应期；梅花：体用生克主互变卦气外应应期；奇门：盘式用神生克三奇吉凶格伏反吟应期），`build_liuyao_knowledge_grounding`/`build_meihua_knowledge_grounding`/`build_qimen_knowledge_grounding` 按卦象/局象注入；③ 六爻/奇门工具改用本地技法库（替代空转的 `/api/techniques`），删除 `_fetch_technique_grounding`。全量 900 passed、ruff 零告警。

**v4.8.63** · 从 mingbu 后端移植**八字技法知识库**（`technique_kb.py`）：把后端 `lib/bazi/knowledge/index.ts` 的技法数据（十神详解 10、格局详解 32、日主本性十干、五行生克口诀、衰旺派方法论、盲派宾主体用做功、古籍原文印证、方位脏腑）抽成 Python 模块，`build_bazi_knowledge_grounding(chart)` 按命盘字段精准注入相关条目，作为 LLM 专业解读的 grounding。接线到 `query_bazi`/`query_combined`（不依赖后端 /api/techniques，纯本地数据）。全量 893 passed、ruff 零告警。

**v4.8.62** · 六爻/奇门占卜工具接线 mingbu 技法知识库（`/api/techniques`）：排盘后按「卦名 + 六亲 + 六神」（六爻）/「值符值使 + 九星八门 + 用神 + 通用易理词」（奇门）提取检索词，调用 `/api/techniques` 检索蒸馏技法条目，作为「命理技法依据」附给 LLM 做专业解读（grounding）。全程容错：后端未就绪/无命中时静默跳过、不破坏主链路。新增 `formatters.extract_liuyao_terms/extract_qimen_terms/format_technique_grounding` 三个纯函数。全量 888 passed、ruff 零告警。

**v4.8.61** · 把排盘/占卜工具从「直接发卦象」改为「返回结果给 LLM 整合」：① 根因——工具用 `yield event.plain_result()` 直接发卦象时，AstrBot 流式输出在 LLM 决定调用工具时会先发「空第一帧」，且工具「无 return 值」导致 LLM 拿不到卦象、无法接着做朋友式解读，用户体验是「空消息 + 迟到的卦象、戛然而止」；② 修复——8 个工具（八字/紫微/六爻/奇门/合参/近况/塔罗/健康）成功路径改为 `return summary`、失败路径 `return 错误字符串`，返回类型 `-> str`；去掉拼进结果的「用户近期在聊」摘要（`extra`）；这样 LLM 拿到卦象后生成「卦象 + 解读」的完整一段；③ 心理疏导/自我探查工具已于 v4.8.60 改 return。全量 882 passed、ruff 零告警。

**v4.8.60** · 修复排盘/占卜工具把内部「朋友式解读」prompt 泄露给用户：`CounselingPrompts.counseling_frame`（八字/紫微/六爻/奇门/合参/近况）、`build_tarot_prompt`（塔罗）、`health_plan_prompt`（健康）、`counseling_turn_prompt`（心理疏导）、`guided_reflection`（自我探查/`/开解`）这些「给 LLM 的引导指令」此前被直接拼进 `yield event.plain_result()` 发给用户，导致回复里出现「请以朋友的方式… / 【疗法指引】 / 【朋友边界】」等内部提示词。修复——命盘/占卜/健康/塔罗工具去掉 prompt 拼接、只输出结果本身；心理疏导/自我探查工具改为 `return prompt` 交给 LLM 整合（不再直接发用户）；`/开解` 指令去掉 prompt 拼接。新增回归测试锁定不泄露。全量 882 passed、ruff 零告警。

**v4.8.59** · 修复八字命盘「五行空」「大运 ??」两个显示 bug：`format_bazi` 两处字段契约未对齐后端真实结构——① 五行：后端 `wuXing` 是 `{count:{金:n,木:n,...}, dominant, weak}`，旧代码直接 `wx.get(e)` 按平铺结构读导致全空；改为读 `count`（兼容旧平铺契约）；② 大运：后端 `daYun` 元素用单个 `ganZhi` 字段（如「壬午」），旧代码读 `gan`/`zhi` 两字段读空显示「??」；改为优先 `ganZhi`（兼容旧 gan/zhi），起运前童限阶段 `ganZhi` 为空则显示「童限」。经真实后端 `/api/bazi` 端到端实测输出正确。全量 881 passed、ruff 零告警。

**v4.8.58** · 修复「能力服务商（拉取模式）」拉取为空：① 根因——`_resolve_astrbot_dashboard` 的默认回退端口写成了 `61805`（多了个 0），而 AstrBot Dashboard 实际默认端口是 `6185`，故用户留空时会连错端口、拉取为空；同时用户填「IP:端口」裸地址时缺 `http://` 前缀，会让 aiohttp 抛 `Invalid URL (No scheme supplied)`、静默失败；② 修复——默认端口 `61805`→`6185`，裸地址自动补 `http://`（https 需显式填写）；③ HTTP API 拉取失败时把具体错误（无 scheme/连接拒绝/401 未授权等）附到诊断 message 末尾，让「拉取为空」时能看到真实原因而非笼统的「未配置服务商」；④ 同步 schema description 与面板 placeholder。全量 880 passed、ruff 零告警。

**v4.8.57** · 把剩余的 LLM 工具与体验增强 handler 也内联迁回 `main.py`，彻底消除「AstrBot 只识别入口模块 handler」的多 Star 架构缺陷：① 10 个 `@filter.llm_tool` 工具（排盘/紫微/六爻/奇门/合参/近况分析/自我探查/塔罗/健康指导/心理疏导）从 `stars/tools.py` 迁回 `MingbuPlugin`，去掉 `_with_self`/`_STAR_INSTANCE` 适配器——此前适配器只绕过了「子 Star 未实例化」，但工具仍未在 AstrBot 里正确登记（v4.8.56 只迁回了指令与被动收集）；② 体验增强（TTS 语音、图片转述、语音转写、合并转发、撤回、消息防抖）从 `stars/enhancements.py` 迁回，TTS 的 `on_llm_response` 钩子并入入口 Star 的同一钩子（记录对话 + TTS 二合一）；③ 删除 `stars/tools.py`、`stars/enhancements.py`，`stars/` 仅剩 `admin.py`（后台 Web API，经继承链接入，initialize 由入口 Star 手动触发）。全量 876 passed、ruff 零告警。

**v4.8.56** · 修复聊天功能全部不生效（指令/被动收集）：① 根因——AstrBot 的 handler 绑定按**入口模块路径** `data.plugins.astrbot_plugin_mingbu.main` 精确/前缀匹配，而命灵把 `@filter.command`/`@filter.regex` 处理器定义在兄弟子模块 `stars/commands.py`、`stars/collectors.py`（路径 `…stars.commands` 不以 `…main.` 为前缀），导致这些 handler 从未被 `functools.partial` 绑定到插件实例、调用时 self 缺失，聊天里 `/排盘 /卜卦` 指令和出生信息被动收集全部失效；此前所有测试都在 stub 环境（stub 的 filter 不校验模块路径）掩盖了该问题；② 修复——把 10 个指令 + 4 个被动收集 handler 内联迁回 `main.py` 的 `MingbuPlugin`，删除 `stars/commands.py`、`stars/collectors.py`；③ LLM 工具（排盘/占卜）经 `_with_self` 适配器本就可调用（v4.8.53），增强类（TTS/STT/视觉）待后续迁回。全量 877 passed、ruff 零告警。

**v4.8.55** · 把「命理服务地址」输入框移到「命理后台」折叠区、紧挨「测试命理后端」按钮：原字段藏在默认折叠的「高级参数」里，用户找不到；现与测试按钮配套，并补充 Docker 容器网络的地址填写提示（宿主机 IP 或同网络容器名）。全量 879 passed、ruff 零告警。

**v4.8.54** · 「测试命理后端」失败时补显尝试连接的地址：连接失败（如「无法连接到 Mingbu 命理服务」）时，状态区额外显示 `· 地址 http://…`，便于区分是「地址配置错误」还是「后端未部署/未启动」。全量 879 passed、ruff 零告警。

**v4.8.53** · 修正 LLM 工具单例登记方式（避免真实环境插件加载失败）：v4.8.52 在 `MingbuPlugin.__init__` 里显式 `StarTools(context, config)` 实例化一个 StarTools，会触发真实 AstrBot `Star.__init__` 的 logger 注册等副作用，有导致插件加载失败、功能「找不到」的风险；改为**直接把入口 Star 实例登记为 `_STAR_INSTANCE`**（`from .stars import tools as _tools; _tools._STAR_INSTANCE = self`）——它同为 MingbuStar 子类、经 `__getattr__` 代理到同一 core 单例，工具方法绑定到它即可正常取回 self，无需额外实例化。全量 879 passed、ruff 零告警。

**v4.8.52** · 修复 LLM 工具「StarTools 尚未初始化」：① 根因——AstrBot 的 `PluginManager.load()` 只实例化入口模块 `main.py` 注册的 Star（MingbuPlugin），**不会**遍历 `star_registry` 实例化子模块 `stars/tools.py` 里的 StarTools；而 LLM 工具的 `_with_self` 适配器依赖模块级单例 `_STAR_INSTANCE`，故它恒为 None、聊天里调用排盘/占卜等工具即报「StarTools 尚未初始化，无法调用 LLM 工具」；② 修复——`MingbuPlugin.__init__` 里显式实例化一个 StarTools（复用同一 core 单例，重复 configure 幂等），使 `_star()` 可取回 self；③ 新增 2 条回归测试。全量 879 passed、ruff 零告警。

**v4.8.51** · 重新设计 API Key 与地址的保存/展示方式为「明文展示、所见即所得」：① 后端移除密钥掩码（`_mask_config_secrets`/`_is_secret_key`/`SECRET_MASK`/`VISIBLE_SECRET_KEYS`）与「留空保留原值」过滤（`_is_credential_field`），`/config` GET 明文返回真实 Key；② 前端密钥字段 `type="password"`→`type="text"`，移除眼睛切换按钮与掩码/placeholder 逻辑；③ 留空=清空该字段（所见即所得），地址/密钥/模型统一直接提交；④ 更新测试——删除「留空保留原值」8 条，新增「明文返回 / 明文保存+留空清空」2 条。全量 877 passed、ruff 零告警。

**v4.8.50** · 修复配置表单 API 设置「留空保留原值」失效：① 根因——前端保存时对 text 类型地址字段（`*_api_base`/`*_base_url`）留空会提交空字符串，后端 `_save_config` 只过滤密钥掩码、不过滤空字符串，导致 `existing.update(data)` 用空值覆盖 KV 里已配置的地址，刷新后再次保存即丢失；② 修复——新增 `_is_credential_field` 判定「密钥+地址」凭据字段（`_api_key`/`_api_token`/`_api_base`/`_base_url`），`_save_config` 与 `_merge_runtime_config` 对这类字段的空字符串与掩码统一做「留空保留原值」处理；③ `*_model` 字段刻意不纳入（「留空=用默认模型」是明确语义，清空模型是合法操作）；④ 首次配置（无原值）留空与后续编辑（有原值）留空行为一致；⑤ 新增 8 条回归测试（地址/密钥/interpret base_url/mc_base_url 留空保留原值、模型留空清空、首次留空、真实值覆盖、模型目录保存空 base_url 保留）。全量 885 passed、ruff 零告警。

**v4.8.49** · 修复后台刷新（重启/热重载）后外部服务 API 地址与请求地址失效：① 根因——AstrBot 热重载/重实例化 Star 时会再次调用 `core.configure()`，传入的静态 config 不含 KV 运行时覆盖层（`mingbu.runtime_config` 里的外部服务地址/密钥/模型），旧实现用 `self._config = dict(config)` 整体替换内存态，且 `ensure()` 的 `_ensured` 守卫让 `_load_runtime_config` 不再执行，导致 KV 里的外部服务地址（tts/stt/embedding/rerank/vision/merged/interpret 的 `_api_base` 等）在刷新后从内存态消失、运行时读到空值；② 修复——`configure()` 改为原地更新（clear+update，避免替换 dict 引用导致多 Star 实例断裂）并重新合并已缓存的 KV 覆盖层镜像 `_runtime_overlay`；`ensure()` 把 `_load_runtime_config` 提到 `_ensured` 守卫之前，任何一次 `initialize()`（含热重载）都重新从 KV 捞回覆盖层；`_save_config`/`_merge_runtime_config` 写 KV 后同步 `_runtime_overlay` 镜像（避免下次 configure 用旧镜像覆盖新提交）；地址变化时 `_sync_api_client` 重建命理后端客户端；③ 新增 4 条回归测试（热重载保留外部服务地址 / 仅 configure 不丢覆盖层 / 部分保存同步镜像 / KV 命理地址重建客户端）。全量 877 passed、ruff 零告警。

**v4.8.48** · 管理面板「命理后台」新增「测试命理后端」按钮：① 后端新增 `/mingbu_test` 端点（`DiagnosticsApiMixin.api_mingbu_test`），向命理服务地址发 `GET /api/health` 探测，返回连通状态、延迟、以及后端透传的 `providerConfigured`（AI Provider 是否就绪）；② 地址解析优先级——面板已保存的 `mingbu_api_url` > 运行中客户端 `base_url` > 环境变量 `MINGBU_API_URL` 默认；③ 前端「命理后台」折叠区加按钮 + 状态区，展示「连通 · XXms · 地址 · AI Provider 就绪/未配置（纯排盘仍可用）」或失败原因；④ 新增 3 条回归测试（健康探测 / 配置地址覆盖 / 连接失败），并同步路由计数断言。全量 873 passed、ruff 零告警。

**v4.8.47** · 修复 STT 上传音频 Content-Type 错误（20015 更深层根因）：① 根因——HTTP 客户端 `_headers()` 无条件写 `Content-Type: application/json`，会把 multipart 表单的 boundary 覆盖掉，导致 STT 上传音频时后端按 JSON 解析 multipart 字节、报 HTTP 400 / code 20015（即使换合法 WAV 仍会被头错误拒收）；② `_headers()` 改为只保留鉴权头，`Content-Type` 交由 aiohttp 按 `json=`/`form=` 自动设置（JSON 请求仍自动 application/json，表单请求自动 multipart boundary）；③ 新增 2 条回归测试（`_headers` 不强制 Content-Type、form 请求不带 application/json）。全量 870 passed、ruff 零告警。

**v4.8.46** · 修复语音识别（STT）连通性测试 `HTTP 400 / code 20015`：① 根因——面板 STT「测试」在未配 TTS 模型时，旧逻辑回退 `b"diagnostic-audio"` 假字节喂给识别服务，SenseVoiceSmall 校验音频格式失败报 20015；② 新增 `_synthetic_wav()` 生成合法 16k 单声道 PCM WAV 样本作为兜底（不依赖 TTS 合成、不依赖 ffmpeg）；③ `_direct_openai_transcribe` 新增 `allow_empty` 参数——连通性测试空转写结果判成功（测试目的是验证端点/鉴权/音频格式，而非识别内容）；④ 新增 4 条回归测试（合成 WAV 合法性 / allow_empty / 未配 TTS 走合成样本）。全量 868 passed、ruff 零告警。

**v4.8.45** · 命理模块全链路修复与后端对接打通：① 修复 LLM 工具在 AstrBot 4.27.x 下不执行（`@filter.llm_tool` 派发不注入 `self`，改以 `_with_self` 适配器取回单例转发，消除 `missing 'event'` 与工具调用 JSON 泄漏）；② 被动收集器加 try/except 防御，杜绝异常上冒吞消息；③ 格式化函数与后端契约对齐——`format_ziwei`/`format_liuyao`/`format_signals` 按真实 `ZiweiChart`/`LiuYaoChart`/`CombinedSignal` 字段修正（命宫/身宫、十二宫地支、卦名/动爻、交叉印证不再错乱），并新增 `format_qimen`；④ 补齐奇门 `query_qimen` 工具，修复奇门起局 `hour` 应传 0–11 地支而非 0–23 钟点；⑤ `ensure_client` 接线到排盘建档（`x-ziwei-user-id` + 匿名 UUID 映射，非阻塞后台归档）；⑥ 模型统一配置凭据持久化修复（`mc_base_url`/`mc_api_key`/`mc_extra_headers` 升为一等配置项，「拉取保存后刷新不再消失」），`mc_api_key` 撤销明文常显、恢复掩码；⑦ HTTP 熔断仅对可重试错误计数（4xx 不再误触发熔断）。全量 864 passed、ruff 零告警；并经私有后端 `mingbu` 端到端实测（八字/紫微/六爻/奇门/合参 + DeepSeek AI 解读 10 客户模拟全部通过）。

**v4.8.44** · 外部服务自定义模式连通性测试补齐（图片视觉/Vision、合并转发、命理 AI 解读）：① 根因——面板「测试」对这三类服务的自定义模式硬写了允许列表（`["tts","stt","embedding","rerank"]`），且后端 `api_connectivity` 未实现 `vision/merged/interpret` 分支，故前端统一报「该服务暂不支持自定义模式连通性测试」；② 后端放开允许集合并加 `KEY_PREFIX`/`BASE_FIELD` 映射（`vision→image_vision_*`、`merged→merged_forward_*`、`interpret→mingbu_interpret_*`，且 interpret 接口地址字段为 `base_url` 而非 `api_base`），三类统一走 `_direct_openai_chat` 做 OpenAI 兼容 `/chat/completions` 探测（vision 走多模态带图路径，与运行时一致）；③ `_direct_openai_chat` 新增 `raise_error` 开关（与 tts/stt/embed/rerank 一致，修复静默空结果问题）；④ vision 软成功——OCR 类模型（如 PaddleOCR-VL）拒收占位/过小测试图返回 `HTTP 400 code 20015` 时，判定为「端点已连通、密钥有效、仅测试图不合适」记成功并提示，避免误判失败；⑤ 前端 `app.js` 允许列表同步放开；⑥ 新增 4 条回归测试（含 vision 软成功）。全量 850 passed、ruff 零告警。

**v4.8.43** · 修复 STT 连通性测试 `HTTP 400 / code 20015` 并加固真实语音转写：① 根因——面板 STT「测试」未带音频，后端 `diagnostics` 回退发送假字节 `b"diagnostic-audio"`，`SenseVoiceSmall` 校验音频格式失败报 20015；改为先用 TTS 合成一段真实 **wav** 样本再喂 STT；② `provider_router._direct_openai_speech` 新增 `response_format` 参数（默认 mp3，测试用 wav）；③ `provider_router._direct_openai_transcribe` 上传 `audio/wav`，新增 `_maybe_to_wav` 在宿主有 ffmpeg 时把平台下发的 mp3/silk 转码为 wav，使真实用户语音消息也不再被 20015 拒；④ 新增 `_maybe_to_wav` 行为与 STT 合成 wav 分支回归测试（全量 846 passed、ruff 零告警）。

**v4.8.42** · 外部调用健壮性与配置默认优化：① 统一 HTTP 客户端 `http_client` 新增按 host 极简熔断（连续失败冷却、半开探活）+ 有限指数退避重试（仅对连接/超时/5xx/429 重试，4xx 不重试），`ServiceHttpError` 增加 `status`/`retryable` 字段；② `background_watch` 情绪冷却状态落 KV（`mingbu.watch_emotion_last`），重启后仍生效；③ `conversation_inspector` 已学知识由全局上限 500 跨用户改为每用户分桶（`mingbu.learned.<uid>`），抽取索引落 KV 按需重建，`unlearn`/`reweight_all` 同步；④ `provider_router` 修复 TTS `voice` 硬编码为 `alloy` 的缺陷，改为按服务自适应 `模型:音色`；⑤ `_conf_schema.json` 7 个增强开关（tts / stt / 长文本语音 / 图片转述 / rerank / 情绪音色路由 / embedding）默认打开；⑥ `star_mixins/profile` 出生 `hour` 归一化为地支索引 0–11（真机复测命理服务接受 0–11）。版本一致性 9/9、全量 pytest 842 passed、ruff 零告警

**v4.8.41** · 模型统一配置的 API Key 改为明文常显（不再掩盖/隐藏）：`index.html` 的 `mcApiKey` 由 `type="password"` 改为 `type="text"`，`system.py` 的 `_is_secret_key` 新增 `VISIBLE_SECRET_KEYS = {"mc_api_key"}` 排除集，使 `GET /config` 返回真实 Key 而非 `********`；前端 `loadConfig`/`saveConfig` 走明文分支，刷新页面后 Key 依然可见。提示文案同步改为「此 Key 以明文保存并显示，刷新页面后依然可见；重新拉取模型时直接使用本框内容」。

**v4.8.40** · 把「模型统一配置」的 API Key 升级为真正的配置项 `mc_api_key`：`_conf_schema.json` 新增字段、`index.html` 的 `mcApiKey` 加 `data-key` 纳入正常保存/加载、后端 `/model_catalog` 在前端留空时自动回退到已保存的 `mc_api_key`。保存后 Key 会被掩盖显示为 `********`（留空即保留原值），重新拉取模型时无需再次输入；新增 2 项回归测试覆盖「留空回退」与「显式填写优先」逻辑（全量 825 项测试通过）。

**v4.8.39** · 修复「模型统一配置 API Key 自动变成 `********`」：根因是前端 `mcPrefillEndpoint()` 在打开面板时把后端已掩盖的 `*_api_key`（字面量 `********`）直接写进 `mcApiKey` 密码框，既渲染成圆点、又会在拉取模型时把假 Key 发往接口导致 401；移除该掩盖值回填，仅保留非敏感的 `mcBaseUrl` 预填（node --check 通过）。

**v4.8.38** · 点击/聚焦 API Key 即显示明文、失焦恢复密文：所有 API Key 密码框（模型统一配置 + 外部服务 tts/stt/embedding/rerank/vision/合并转发/命理解读 + AstrBot dashboard）统一由前端 `initPasswordToggles` 注入眼睛切换按钮，点击输入框或眼睛图标即由 `*` 明文；修复眼睛按钮因输入框 blur 被强制恢复密文导致「只能显示不能隐藏」的问题（mousedown 拦截阻止焦点转移）；移除 mcApiKey 手写重复按钮，改为通用注入（node --check 通过）。

**v4.8.37** · 修复「API Key 保存后变成不知道的密码」：根因是浏览器密码管理器把 `type="password"` 的 API Key 输入框当成登录密码自动填充，点击保存后把错误的填充值写回配置导致模型失效。为所有 API Key 输入框加 `autocomplete="new-password"` 等防填充属性，并对「模型统一配置」的 API Key 加 readonly-onfocus 与显示/隐藏切换；`loadConfig` 读到后端掩码 `********` 时不再回填到密码框，`saveConfig` 遇到掩码或留空的已设置字段时跳过，避免覆盖真实 Key。新增后端回归测试确保 POST 回传掩码不会覆盖真实 api_key（全量 823 项测试通过）。

**v4.8.36** · 根治能力服务商下拉为空（「已拿到 Provider 实例，但实例缺少 id/provider_id 属性」）：命灵改为**优先通过 AstrBot 有 OpenAPI 文档、结构稳定的 HTTP 接口 `/api/v1/providers`** 拉取服务商列表——该接口响应对象明确带 `id` 字段（如 `Sense/deepseek-v4-flash`），不再依赖 `provider_manager` 进程内实例的属性猜测；新增 `astrbot_dashboard_url`/`astrbot_api_key` 配置项（留空回退本机 `127.0.0.1:61805`），公网/自签证书地址可填并自动跳过证书验证；`http_client` 的 `request_json`/`request_bytes` 新增 `ssl` 参数；新增 E2E 测试断言 HTTP API 路径正确过滤出文本生成类服务商并成功枚举（全量 822 项测试通过）。

**v4.8.35** · 能力服务商与外部服务卡片端到端落地：① 加固 Provider 解析以兼容真实 AstrBot 接口——`get_provider` 除按 id 匹配外新增按实例 name 匹配兜底，`list_llm_providers_with_diagnostics` 新增 `llm_providers` 候选属性并支持 dict 形态实例；② 能力服务商区新增「重新拉取服务商」按钮，解决页面初次打开时 AstrBot 未就绪导致下拉为空、且无可重试入口的问题；③ 新增 `tests/test_e2e_provider_services.py` 端到端验证，覆盖 `/list_providers` 拉取、`provider_dream` 配置被消费并透传模型、`/connectivity service=provider` 真实发起 `text_chat`、外部服务「自定义模式」TTS/Embedding 真发 HTTP 到正确端点（全量 821 项测试通过）。

**v4.8.34** · 修复外部服务「自定义模式」点击测试后全部显示「失败：服务返回空结果」的模糊问题：根因是 TTS/STT/Embedding/Rerank 四个直连函数在请求失败时静默返回空值（`b""`/`""`/`[]`），诊断接口只看到空值就统一报“空结果”，把 401/404/模型不存在等真实错误吞掉。为四个函数增加 `raise_error=False` 开关，诊断测试时传 `True` 暴露真实异常；前端直接展示后端返回的具体错误。新增 13 项直连服务诊断回归测试（覆盖默认静默、raise_error 暴露 HTTP 错误、空结果判定）。

**v4.8.33** · 修复「能力服务商（拉取模式）」下拉为空时只提示「未配置任何服务商」的模糊问题：后端 `list_llm_providers_with_diagnostics` 返回 context/provider_manager 就绪状态、枚举策略与错误、Provider 实例属性采样、根因分类等结构化诊断；前端据此显示具体原因（未初始化 / 接口异常 / 实例结构变更 / 确实未配置）。同时在「渠道与服务配置」每个能力服务商与外部服务卡片新增「测试」按钮，支持 AstrBot Provider（按 task_type 或 provider_id）与直连凭据（TTS/STT/Embedding/Rerank）的连通性验证，结果 inline 展示成功/失败与耗时。

**v4.8.32** · TTS 触发条件改造：由「回复必须含 `<tts>` 标签」改为「长文本(默认 >=200 字)必触发，其余回复按 8.6% 固定概率随机触发」，不再依赖 LLM 主动吐标签；抽出纯函数 `tts_should_trigger`（长文本/随机判定，注入 rng 可测）并新增 7 项回归测试；长文本阈值可由 `tts_long_text_threshold` 配置覆盖。

**v4.8.31** · 修复统一 HTTP 客户端只接受状态码 200 的边界缺陷：JSON、SSE、二进制请求现在接受全部 2xx 成功响应；JSON 接口遇到成功但非 JSON 响应时统一转为结构化 `ServiceHttpError`；新增 201/202/非 JSON 回归测试。

**v4.8.30** · 开始系统治理：新增依赖诊断与 Provider/外部服务配置检查 API 及管理面板入口；记录能力初始化状态；管理 API 对配置和模型目录中的 API Key 脱敏；补齐可选能力关闭后的概览/用户详情/关系网/梦境/成长接口降级；补充休息闸门模式和醒来概率配置控件；新增配置/能力诊断回归测试。

**v4.8.29** · 修复外部服务自定义模式未同步 `mode-custom` 状态类导致自动补全始终提前返回的问题：模式切换现在同时更新卡片 class 与 data-mode，保存兜底和选模型即时补全均可正确识别 TTS/STT 等自定义卡片。

**v4.8.28** · 修复外部服务自动补全「只在选模型那一刻触发、遗留空状态无法自愈」的缺陷：① 问题——`svcAutofillOnModelSelect` 仅在 `_selectCombo`（mousedown 选模型）与模型输入框 `change` 事件触发；若用户早在自动补全功能上线前就选好了模型、或选模型时顶部「模型统一配置」底座尚未填写，卡片的 `api_key`/`api_base` 会永久留空且不再被补全，导致 TTS/STT 等自定义模式能力静默失效（命灵因 `tts_api_key and tts_api_base` 不满足而跳过直连、又无 AstrBot Provider 回退）。② 修复——将补全逻辑抽出为通用 `svcAutofillCard(card, {silent})`，新增 `svcAutofillAll()` 遍历所有自定义模式卡片，在 `saveConfig()` 收集配置**之前**调用，对「已选模型但 key/base 仍空」的卡片用顶部底座自动补齐（`silent` 模式不弹提示），确保点「保存全部」即可自愈，无需重选模型。③ 保留原有即时补全与「底座未填时 toast 提示」行为不变。node --check 通过、Python 语法通过、版本一致性 9/9、全量 pytest 769 passed

**v4.8.27** · 修复 TTS 失败时真实异常被静默吞掉的排查黑洞，并增强外部服务自动补全体验：① TTS 日志透明化——`stars/enhancements.py` 中 `_on_llm_response` 与 `_send_tts` 的异常捕获从 `logger.debug` 升级到 `logger.error` 并打印完整 traceback；`_send_tts` 增加关键节点 `info`/`warning` 日志，清晰记录当前配置（key/base/model/provider_tts）、走的是直连还是 AstrBot Provider、失败原因。② 自动补全更健壮——`svcAutofillOnModelSelect` 在顶部「模型统一配置」底座（`mcBaseUrl`/`mcApiKey`）未填时，通过 `toast` 明确提示用户先填底座；为所有 svc 模型输入框绑定 `change` 事件，确保鼠标/键盘/datalist 选择模型后都能触发补全。③ 前端缓存破坏——`index.html` 中 `app.js`/`style.css` 引用追加 `?v=4.8.27`，避免插件更新后浏览器仍用旧缓存导致新功能不生效。node --check 通过、Python 语法通过、版本一致性 9/9、全量 pytest 769 passed

**v4.8.26** · 全面排查外部服务与能力服务商的一致性缺陷（审计修复）：① 自动补全失效——`svcAutofillOnModelSelect` 原按 `card.dataset.svc` 拼 `${svc}_api_key`/`${svc}_api_base`，但 `vision`/`merged`/`interpret` 三卡真实字段前缀为 `image_vision_`/`merged_forward_`/`mingbu_interpret_`，查询落空导致自动补全静默不工作（v4.8.25 功能仅对 4/7 卡生效）；改为从 `data-custom-keys` 解析真实字段名，7 卡全部可用。② schema 误导描述——`_conf_schema.json` 中 `tts_api_base`/`stt_api_base`/`rerank_api_base` 的 description 仍写完整 endpoint（`/v1/audio/speech` 等），与后端「base+自行拼子路径」矛盾（同 v4.8.25 placeholder 那一类），统一改为 base 到 `/v1`。③ 失效槽位——面板「能力服务商」中的「新闻整理/主动搜索」全仓无 `task_type=news/search` 的 LLM 调用（仅网页抓取+KV 存储），配置永不消费，已从面板移除（schema 键保留兼容存量）。④ 更新 `provider_router.py` 过时「`image_vision_*/merged_forward_*` 是死配置」注释（实际已被 `enhancements.py` 接上使用）。node --check 通过、版本一致性 9/9、全量 pytest 769 passed

**v4.8.25** · 外部服务「自定义模式」选中模型后自动补全 API Key 与接口地址：① 场景——用户在核心配置台「模型统一配置」拉取模型（底座 `mcBaseUrl`/`mcApiKey`）后切到外部服务卡片的「自定义」并选模型时，`svcSetMode` 会清空 `api_key`/`api_base`，需手填，体验差。② 修复——`_selectCombo` 在自定义模式下选中 svc 卡片模型后调用 `svcAutofillOnModelSelect`：仅当 `api_key`/`api_base` 为空时，自动填入 `mcApiKey` 与 `normalizeBaseToV1(mcBaseUrl)`（base 规范到 `/v1`）。③ 关键正确性——后端 `provider_router.py` 把 `api_base` 当作 base、自行拼接 `/audio/speech`·`/audio/transcriptions`·`/embeddings`·`/rerank`·`/chat/completions` 等子路径，故自动补全只填 base 不拼子路径，避免双重拼接 404；同时修正 TTS/STT/rerank 卡片误导性的 placeholder（原写成完整 endpoint）。④ 仅空则补、不覆盖手填；兼容 Chrome/Edge/Firefox/Safari；node --check 通过、版本一致性 9/9

**v4.8.24** · 修复下拉列表「滚轮完全无法滚动」的回归缺陷：① 根因——v4.8.23 的 `_onComboWheel` 在**未到达列表边界**时调用了 `e.preventDefault()`，而 wheel 事件上的 `preventDefault()` 会一并阻止浏览器默认的面板滚动（包括 `_comboPop` 自身），导致滚轮时列表既不收起也不滚动，表现为「滚轮失效」。② 修正边界处理：未到达列表边界时**不再** `preventDefault`，放行浏览器默认行为（面板自身内部滚动），保证滚轮可用；仅在**到达列表边界**时才 `preventDefault` 阻止滚动链继续联动到页面；面板不可滚动时仍彻底屏蔽页面滚动联动。③ 保留 v4.8.23 的全部正常交互（点击选项/外部点击/箭头/Escape 关闭、不误收起）。兼容 Chrome/Edge/Firefox/Safari；node --check 通过，版本一致性 9/9

**v4.8.23** · 修复模型选择下拉列表中「滚轮意外收起列表」的交互缺陷：① 根因——`_ensureComboPop` 在 window 上以捕获阶段监听 `scroll` 并直接 `_closeCombo`，导致用户在展开的下拉面板内滚动鼠标滚轮时，面板自身的 `scroll`（或页面 `scroll`）冒泡至 window 捕获监听即将列表收起；且 window 用 `capture:true`，面板内 `stopPropagation` 也无法拦截。② 改为滚动时只重定位面板而不关闭：`window` 的 `scroll` 监听改为 `_onComboScroll`，仅在「页面滚动」时调用 `_positionComboPop(input, false)` 贴合输入框，面板自身滚动则跳过；`_positionComboPop` 新增 `resetScroll` 参数（重定位不重置列表滚动位置）。③ 下拉面板新增 `wheel` 监听 `_onComboWheel`（`passive:false`）：`stopPropagation` 阻止冒泡；面板可滚动时在内部滚动并 `preventDefault` 禁用页面滚动联动，仅在到达列表边界后才放行页面滚动；面板不可滚动时彻底屏蔽页面滚动。④ 保留正常交互：点击选项（`mousedown`→`_selectCombo`）、点击外部（`document` click 关闭）、点击箭头切换、Escape 关闭均不受影响。兼容 Chrome/Edge/Firefox/Safari 等主流浏览器；node --check 通过，版本一致性 9/9

**v4.8.22** · 修复外部服务「自定义模式」模型框出现两个下拉箭头的问题：① 根因——模型类下拉（`data-cats` 驱动，选项取自已拉取 `MC_MODELS`）仍残留 `list="mcModelList"` 属性；该属性对模型类完全冗余（`_comboOptions` 走 `data-cats` 分支、从不读 datalist），却会触发浏览器原生 datalist 箭头，与 `enhanceCombos` 注入的自定义 `.combo-arrow` 叠加成「双箭头」（嵌入式 webview 中 `appearance:none` 无法稳定压制原生箭头）。② 移除 7 个 svc 自定义模型输入框与 `renderMcRoles` 生成的 `mc-provider-model-input` 上的冗余 `list="mcModelList"`；同步将 `enhanceCombos` 选择器与 `.config-input[list]` 样式扩展为 `input.config-input[data-cats]`（保留 `appearance:none` + 右侧内边距 + 指针），确保模型框仍由自定义箭头驱动、文本不压箭头。纯前端改动，全量测试 769 通过、ruff 零告警、node --check 通过

**v4.8.21** · 修复「模型拉取保存后再次进入全部消失」：① 根因——`fetchModels` 把拉取的模型只存进前端内存变量 `MC_MODELS`，从不写后端；`loadModelsTab` 重新进入时仅从 `/config` 加载各角色选中的模型名，`MC_MODELS` 为空 → 目录与下拉全部消失。② 后端新增模型目录持久化：KV 键 `mingbu.model_catalog`，`/model_catalog_save`（POST，落盘 base_url/api_key/extra_headers/models）与 `/model_catalog_get`（GET，读回）；与运行时配置分开存，不经 `coerce_by_schema`、不被配置保存冲刷。③ 前端 `fetchModels` 拉取成功后立即 `apiPost("model_catalog_save", …)` 持久化；`loadModelsTab` 进入时 `apiGet("model_catalog_get")` 恢复 `MC_MODELS` 并回填端点字段，状态栏提示「已恢复上次拉取的 N 个模型」。④ 新增 `tests/test_model_catalog_persist.py`（5 例：保存/读取往返、空态返回 null、覆盖写、`put_kv_data` 异常返回 error 而非 500、两路由注册）；路由数 33→35。全量测试 769 通过，ruff 零告警，零回归

**v4.8.20** · 修复「模型统一配置」中拉取的模型无法在「能力服务商」下拉选择的问题：① 根因——`MC_PROVIDER_ROLES`（主回复/梦境/成长/主动关怀/新闻/主动搜索）只渲染了服务商输入框，没有对应的模型选择框，用户即便在上方「拉取模型列表」拉到了模型，也无法为各能力指定模型。② `_conf_schema.json` 为每个能力服务商新增 `provider_{role}_model` 字段（共 6 个，title 为「{角色}模型」，说明「从已拉取模型中选择，留空则使用该服务商默认模型；未指定则沿用主回复模型」）。③ 前端 `renderMcRoles` 在每个服务商输入框下新增「{角色} 模型」输入框（`list="mcModelList"`，从已拉取模型下拉选择；留空即该服务商默认模型），并同步 `MC_PROVIDER_MODELS` 状态。④ 后端 `star_mixins/llm._llm_text` 从 `_config` 读取 `provider_{task_type}_model` 并透传至 `provider_router.llm_request`（子任务未指定则沿用 `provider_main_reply_model`），实现「选模型即以该模型独立调用」。新增 `test_provider_model_passthrough.py`（4 例）覆盖 model 透传与回退；全量测试 764 通过，ruff 零告警，零回归

**v4.8.19** · 明确「服务商 (AstrBot Provider)」的两种调用模式：① 服务商留空 → 自动依赖 AstrBot 已配置的主服务商（`ctx.llm_request` 透传选中 model）；② 通过下拉框选中「已拉取的模型」→ 以独立模型进行调用，不再简单回落默认服务商。具体改动：`ProviderRouter.llm_request` 增加 `model` 参数并透传至 `provider.text_chat`（旧签名 TypeError 兜底回退）；`stars/enhancements.py` 的 `image_vision` 与 `merged_forward` 调用点传入各自 `{task}_model`；前端 svc-intro 文案强化两种模式说明，并为 7 个模型字段（tts/stt/embedding/rerank/image_vision/merged_forward/mingbu_interpret）补「选中即独立调用，不依赖默认服务商」提示。全量测试 760 通过，ruff 零告警，零回归

**v4.8.18** · 修复「渠道与服务配置」下拉仍显示「无」的问题：① 根因——`_renderComboList` 统一使用模型类空态提示「模型需先「拉取模型列表」」，导致「服务商 (AstrBot Provider)」下拉在 AstrBot 未配置服务商时同样显示该提示，造成用户误解；且 provider 数据在 init 中异步加载，若用户在加载完成前打开下拉会看到空列表。② 新增 `_isModelCombo` / `_comboEmptyText`：按输入框类型区分空态文案——模型类提示「模型列表为空/暂无匹配模型」；provider 类提示「正在加载.../当前 AstrBot 未配置任何服务商.../无匹配的服务商」。③ `_openCombo` 改为 async：对 provider 类下拉若数据未加载，先显示「正在加载...」、异步拉取 AstrBot 服务商列表并刷新 `svcProviderList` / `mcProviderList` 两个 datalist，加载完成后再渲染真实选项。④ 同步更新所有键盘与鼠标事件监听器以支持 async 打开。纯前端改动，无后端逻辑变更

**v4.8.17** · 修复「渠道与服务配置」下拉箭头失效：① 根因——`style.css` 把箭头画成 `input[list]` 的 `background-image` 且 `appearance:none` 隐藏原生箭头，箭头只是背景图不可点击；外部服务卡片的「模型名」输入框（tts/stt/embedding/rerank/vision/merged/interpret 共 7 个）原本无 `list` 属性，已拉取模型无处可下拉选入。② 新增可点击自定义下拉组件 `enhanceCombos`：真实 `<button class="combo-arrow">` + `position:fixed` 弹层（避免被容器裁剪），支持键盘 ↑/↓、Enter、Esc、实时模糊过滤、点击外部/滚动/失焦关闭。③ 下拉触发与数据绑定：服务商字段（无 `data-cats`）从关联 `<datalist>` 读 AstrBot 已配置服务商；模型字段（带 `data-cats`）从已拉取的 `MC_MODELS` 按能力过滤读取并附价格速查。④ 导入与状态更新：选中写入输入框并派发 `input` 事件经 `data-key` 落配置、带 `data-role`/`data-cats` 的同步 `MC_ASSIGN` 刷新目录徽标；`init` 预填 `svcProviderList` 使未开「模型统一配置」标签时服务商下拉也可用。纯前端三件套改动，无后端逻辑变更

**v4.8.16** · 核心配置模块审查修复（3 项）：① 让 `mingbu_interpret_*` 真正生效——`SilentDiviner._compute` 在本地翻译表洞察的一级兜底之上，开启 `mingbu_interpret_enabled` 时调用 `/api/interpret` 生成更丰富洞察，输出经 `disguise_guard.strip_leak_terms` 硬屏蔽命理术语（异常/空结果安全回退本地），`core.py` 在构造 / 配置热更新 / ensure 三处注入 interpret 配置，面板开关即时生效（消除审查发现的「假功能」死配置）。② 修复 `system._save_config` 落盘前未合并既有运行时覆盖层、会冲掉其它已存键的缺陷：改为先 `get_kv_data` 读取既有覆盖层再 `update`，部分保存不再丢键（重写原假阳性测试为「保存→重建→KV 重载」集成测试）。③ 前端清理：删除 `mcModelsForCats` / `.mc-model-input` 死代码、修正残留「应用到配置」文案、修温度滑块（0–2）误显为百分比的 bug。新增 `test_silent_divination_interpret.py`（5 例）+ 重写配置合并集成测试；全量测试 760 通过，ruff 零告警，零回归

**v4.8.15** · 代码审查 P2 修复（两处并发/缓存健壮性）：① `core.ensure` 加 `asyncio.Lock()`（惰性创建，避免 Python 3.9 在 `__init__` 同步上下文构造原语抛 RuntimeError）包裹构建段，采用双检锁——多 Star 并行初始化时首个协程构建引擎、其余快速返回，杜绝引擎双建与 KV/HTTP 连接池泄漏（审查报告 P2 并发安全）。② 新增 `ProviderRouter.invalidate_cache()`，并在 `system._save_config` 保存成功后调用，使面板修改外部服务配置（provider_*）立即失效 Provider 解析缓存、免重启生效（审查报告 P2 缓存不失效）。③ 新增回归测试锁定两处修复（`test_provider_cache` / `test_ensure_concurrent`）。全量测试 754 通过，ruff 零告警，零回归。④ 管理面板整体视觉升级为毛玻璃（Glassmorphism）风格——新增统一玻璃令牌（`--glass-blur`/`--glass-edge`/`--glass-highlight`/`--glass-shadow*`/`--shadow-sm`/`--border-soft`），为所有面板/卡片/区块套用半透明背景 + `backdrop-filter` 模糊 + 圆角 + 柔和投影 + 顶部高光描边，并增强背景柔光斑使模糊可见；顺手修复 `--shadow-sm`/`--border-soft` 未定义导致的悬停/边框失效。纯 CSS 改动，所有 `data-key`、DOM 结构与 JS 逻辑零影响

**v4.8.14** · 核心配置页「外部服务」视觉与交互重构：① 把分散的外部服务配置（语音合成/语音识别/语义召回/重排/视觉/合并转发/命理解读）整合为 7 张统一服务卡片，每张含启用开关，并提供「拉取模式（复用 AstrBot 已配置服务商）」与「自定义模式（自有 Key/接口/模型）」两种互斥方案，通过分段按钮一键切换、切换即清空对侧输入。② 删除核心配置页遗留的重复「命理解读」区块，消除二选一改一处漏一处的隐患。③ 精简 `applyModels` 为仅回写 `provider_*`，移除与卡片自定义模式冲突的顶部直连分配区，新增 `svcSetMode`/`initExternalServiceToggles` 模式切换逻辑。④ 新增服务卡片与分段按钮样式，复用主题变量。纯前端改动，无后端逻辑变更，未引入新环境变量

**v4.8.13** · 修复 v4.8.12 在 AstrBot 4.27.2 上导入即崩（插件「不存在」）：① 根因——v4.8.12 给 `stars/enhancements.py` 的 5 个处理器新加的 `@filter.on_message()` 在 4.27.2 已被移除（改用 `event_message_type(EventMessageType.ALL)`），导入阶段抛 `AttributeError` 致整个插件加载失败、被判定「不存在」。② 改用**自适应入口装饰器** `_message_handler`：旧版走 `on_message()` 入口，4.27+ 走 `event_message_type(ALL/PRIVATE)` 入口（`EventMessageType` 为 Flag，`ALL` 捕获全部、`PRIVATE` 仅私聊防抖），在真实 4.27.2 环境下验证 5 个处理器正确注册为消息事件处理器。③ 把测试桩 `devtools/astrbot_stub.py` 的恒等装饰器改为**忠实模拟 4.27.2 缺失 `on_message`** 的语义，回归测试因此真正覆盖部署环境路径（修复前必失败）。全量测试 751 通过，ruff 零告警，零回归

**v4.8.12** · P0 修复·增强链路处理器挂载：① 修复 `stars/enhancements.py` 的 5 个被动消息处理器（图片转述/语音转写/合并转发/撤回感知/私聊防抖）**长期未被 AstrBot 事件分发挂载**的 P0——它们只挂了 `@filter.event_message_type` 条件装饰器、缺 `@filter.on_message()` 入口装饰器，按 AstrBot 契约（`event_message_type` 仅追加过滤条件、无法单独创建处理器）生产环境 0 触发，导致整组增强功能失效却零报错。已补 `@filter.on_message()` 使其真正挂载。② 防抖合并逻辑从「仅打日志空实现」改为合并后写入记忆 OBSERVATION（不再静默无用）；并修正误导性注释（说明不调用 `stop_event` 以保留正常单条回复）。③ 升级测试桩忠实模拟 AstrBot 装饰器注册语义（`on_message` 打 `__astrbot_event_type__` 标记、`event_message_type` 仅追加 `__astrbot_event_filters__`），新增注册回归测试锁定该修复（修复前必失败）。全量测试 751 通过，ruff 零告警，零回归

**v4.8.11** · 模型统一配置重构·三种方式合并：① 把「为各能力指定模型」与外部服务「服务商编排」**合并进同一「模型统一配置」框架并始终可见**（不再等拉取完才显示）。② 直连能力（图片转述/语义召回/重排/语音合成/语音识别/合并转发）由 `<select>` 改为**可手输的 combobox**：拉取后下拉建议、也可直接手输模型名、留空＝用 AstrBot 默认；外部服务 6 个 Provider 框同样下拉选/手输/留空三态。③ 删除核心配置页里重复的静态「服务商编排」区块（与助手内生成的 Provider 框二选一，避免改一处漏一处）。④ 新增 `ProviderRouter.get_provider` 路由断言锁定「`provider_<task_type>` 确实能代替 AstrBot 默认服务商」（dream/growth/proactive 等任务实际走 `provider_router.llm_request`）。保留全部既有功能：拉取 / 手填 / 默认 三种方式互不冲突。全量测试 748 通过，ruff 零告警，零回归

**v4.8.10** · 模型统一配置·Provider ID 下拉化：① 后端新增 `list_providers` 接口（`/{p}/list_providers`，GET），跨版本兼容枚举 AstrBot 已配置的 LLM 服务商（优先 `provider_manager.llm_provider_insts`，回退 `provider_insts`/`get_all_llm_providers`/`context.get_providers`，取不到降级为空）。② 前端把「模型统一配置」里 6 个 Provider ID 文本框改为 `<input list="mcProviderList">` + 共享 `<datalist>`：既能从下拉选已配置服务商、也能手动输入 ID，彻底免去手抄 ID 易错的问题；取不到列表时自动回退为纯文本框并保留原填写指引。新增 `list_llm_providers` 枚举测试 + `api_list_providers` 路由测试；路由总数 32→33。全量测试 744 通过，ruff 零告警，零回归

**v4.8.9** · 模型选项映射纠错 + 选取页配色 + Provider ID 指引 + 拉取内置化：① 修复「为各能力指定模型」下拉的映射错乱——`_MODEL_CAT_RULES` 的 vision 规则含过宽的 `"gpt-4"`，把 `gpt-4`/`gpt-4-32k` 等非视觉模型误判为视觉；且 `image_vision` 角色 `cats` 含 `"chat"`，导致 `mcModelsForCats` 把**全部 chat 模型**塞进「图片转述(视觉)」下拉。修正为移除 `"gpt-4"`、视觉规则改 `gpt-4o/gpt-4-vision/gpt-4-turbo`，`image_vision` 的 `cats` 改为仅 `["vision"]`，确保各模型正确入座。② 选取页配色：`:root` 加 `color-scheme: dark` 让原生 `select`/`option` 弹层走暗色高对比；`select` 显式暗底亮字 + 自定义箭头；`.mc-model-price` 由 `--text-faint` 提亮到 `--text-dim` 并加底纹。③ `Provider ID` 输入框新增明确填写指引（填 AstrBot「设置→模型服务」里服务商名称，非 URL/模型名，附示例）。④ 「模型拉取助手」从独立标签页移入**核心配置页内联**，去掉多余窗口；「应用到配置」后回填核心配置页对应输入框，同页可见所选模型。新增分类断言 6 项；全量测试 739 通过，ruff 零告警，零回归

**v4.8.8** · 修复拉取模型目录的导入路径错误：v4.8.7 修复路由注册后，服务器点「🔄 拉取模型列表」报 `No module named 'data.plugins.astrbot_plugin_mingbu.stars.provider_router'`。根因为 `stars/apis/system.py` 中 `from ..provider_router import fetch_model_catalog` 相对导入层级写错——`system.py` 位于 `stars/apis/`，`..` 只到 `stars/` 目录，而 `provider_router.py` 在插件根目录；应使用 `from ...provider_router import fetch_model_catalog`。修正后本地开发与 AstrBot 运行时（模块名为 `data.plugins.astrbot_plugin_mingbu.*`）均能找到正确模块。全量测试 733 通过，ruff 零告警，零回归

**v4.8.7** · 修复管理面板全部 Web API 404（含「拉取模型列表」）：根因为 AstrBot 仅自动初始化插件主模块 `main.py` 内的主 Star（`MingbuPlugin`），而管理面板 32 个接口全部依赖 `stars/admin.py` 的 `StarAdmin.initialize() → _register_web_apis()` 注册——`StarAdmin` 作为子目录 Star 从未被初始化，于是自 v4.8.4 拆分以来页面可见、接口全 404（`config` 保存/读取/`stats`/`model_catalog` 均受影响）。修复：`MingbuPlugin` 改为继承 `StarAdmin` 并在其一定会被调用的 `initialize()` 末尾显式注册路由；`_register_web_apis` 加 context 级幂等守卫，避免 AstrBot 同时初始化多 Star 时重复注册。验证：桩环境复刻主 Star 初始化 → 注册 32 路由（含 `model_catalog`/`config`），共享 context 的第二个 Star 被守卫跳过；全量测试 733 通过，ruff 零告警，零回归

**v4.8.6** · 模型拉取展示价格：管理面板「🧠 模型配置」一键拉取模型列表时，每个模型卡片新增价格行（输入/输出 每百万 tokens）。价格两层兜底——优先采用端点 `/models` 自带价格（OpenRouter 等 `pricing.prompt/completion`，自动折算每百万 tokens，标记「端点精确价」）；OpenAI 兼容端点通常不返回价格，故内置约 55 个常见模型关键字速查表（OpenAI / Claude / Gemini / DeepSeek / Qwen / GLM / Kimi / Mistral / Llama / MiniMax / 百川 / 零一 / 阶跃），按 id 命中回退并标记「参考价」，更具体型号写在泛化型号之前避免误抢；本地/开源模型无匹配则显示「价格未知」。列表顶部新增图例说明来源与「参考价随厂商调价变动」的局限。`provider_router.py` 新增 `_BUILTIN_MODEL_PRICES` / `_extract_provider_price` / `_resolve_model_price`，`fetch_model_catalog` 每个模型附 `price` 字段；`pages/admin` 前端渲染 `mcPriceText` + 图例样式。新增 `tests/test_model_catalog.py` 价格用例（9 项），全量测试 724 → 733，ruff 零告警，零回归

**v4.8.5** · 统一模型配置页 + 死配置生效：新增管理面板「🧠 模型配置」标签页，填一个 Base URL + API Key 即可一键拉取该端点全部模型；模型按能力（对话/视觉/嵌入/重排/语音合成/语音识别）自动分类并标记已分配角色，各能力下拉选模型 + 填 AstrBot Provider ID，「应用到配置」自动写入直连凭据并开启对应开关；后端 `provider_router.py` 新增 `_direct_openai_chat` 直连多模态对话与 `fetch_model_catalog` 模型拉取，`stars/apis/system.py` 新增 `/model_catalog` 接口；接通此前未生效的 `image_vision_*` / `merged_forward_*` 直连凭据（`_describe_images` 优先走视觉直连、`_on_merged_forward` 改为异步生成自然摘要），消除配置空转。新增 `tests/test_model_catalog.py`（16 项），路由数 31→32，全量测试 708 → 724，ruff 零告警，零回归

**v4.8.4** · 十项优化清单全落地 + 三处真实缺陷修复 + 测试基础设施硬化：将 792 行上帝类 `stars/admin.py` 拆分为 111 行编排层 + `stars/apis/` 八文件（`_base`/`_schema`/`overview`/`system`/`collection`/`slang`/`conversation`/`worldview`），同时落地配置校验复用 schema（`coerce_by_schema`，90 字段）、接口异常兜底一致化（`@api_endpoint` 统一 try/except → error_response + record_failure）、能力注册表自描述化（`features.py` 补 `config_key`/`default`，降级护栏列表由注册表驱动）；补全 P0 后台任务可观测、HTTP 客户端收敛、KV 缓存、`devtools` 与运行时物理隔离（`.gitattributes` 分发包 -85%，import 闭包隔离测试）。顺带修复三处既有缺陷：①「启用主动关怀」关闭时 `_on_llm_request_body` 无条件调用 `self.proactive.track_session` 致每条私聊消息崩溃（P0 线上故障，`main.py` 无 try/except）；② `_maybe_onboard` 先落库标记再崩断，用户被永久标记为已引导却收不到欢迎/初始角色（毒标记）；③ `weight_threshold` 死配置——引擎只读模块常量、配置项赋值给无人读取的属性。测试 561 → 708（新增 147 项护栏，含热路径每轮均摊预算、可选引擎逐项关闭、跨层依赖闭包检查），ruff 零告警，零回归

**v4.8.3** · 发布元数据补全 + 面板版本回填 + 测试断言有效性审计：补上 `metadata.yaml` 缺失的 `repo`（AstrBot 更新检查与插件市场溯源的依据，缺失时面板无法检查更新）与 `astrbot_version`（README 三处声明「需要 AstrBot ≥ 4.26」，元数据却从未写入，PEP 440 装载校验等于没生效，低版本用户只会在运行时报错）；修复管理面板页脚版本号——`/stats` 早已返回 `version` 字段，`app.js` 却从不消费，`index.html` 硬编码在 `4.7.4` 长期滞后三个版本，现改为由接口回填、`_version.py` 成为唯一数据源，字面量降级为离线占位；审计 `test_metadata_consistency.py` 自身的三处失准——必填字段清单凭印象写成（误把可选的 `display_name` 当必填、真正关键的 `repo` 反而漏检）、changelog 断言用全文子串匹配致「删掉变更条目仍能通过」、以及漏掉面板这第四处版本号，已逐条收紧并对每条新断言做变异测试验证确能拦住回归。测试 556 → 561，双版本 3.9 + 3.13 全绿，ruff 零告警

**v4.8.2** · 上帝类拆解收官 + Python 3.9 真机验证 + 测试桩根治：`core.py` 927 → 390 行，共享常量下沉 `constants.py`、8 组横切能力下沉 `star_mixins/` 子包，`MingbuStar` 退化为只含 `__init__`/`__getattr__` 的薄基类，依赖方向 `constants ← star_mixins ← core ← main/stars/*` 单向无环；修复 Python 3.9 下模块级 `asyncio.Lock/Event/Semaphore` 在构造时即绑定当时事件循环、换 loop 后 await 抛 `RuntimeError` 的运行时缺陷（3.10+ 无此行为，只跑新版 CI 无法暴露），改为惰性创建，CI 矩阵补入 3.13；根治测试桩两处长期静默失效——finder 在「模块已加载」分支把真实 loader 交回 import 机制导致**同一模块被加载两份**（`core._CORE` 出现两个内核、`isinstance` 对同名两类返回 `False`），以及 `_Filter` 缺 `regex` 等装饰器致 `main.py` 与 4 个 Star **导入期异常被吞、长期零加载覆盖**；消除 `background_watch.py` 手抄的命理方法常量副本。测试 377 → 556（新增 179 项拆分/桩身份护栏），双版本 3.9 + 3.13 全绿，ruff 零告警

**v4.8.1** · 稳定性与热路径优化：修复情景/情绪记忆写入必然抛 `TypeError` 导致记忆链路静默中断、记忆衰减因类属性共享而永不生效、`list_conversations` 恒返回空、`api_user_detail` 读取不存在字段而必然 500；对话日志 id 改用 CRC32（消除 `hash()` 随机化导致的跨重启失效）、uid 解析改用 `rsplit` 兼容 `wxid_xxx` 形式；记忆与对话日志改为写回缓存 + 防抖落盘，消除每条消息全量序列化；危机提示词去重注入；`HookChain` 支持短路；测试 209 → 248，ruff 零告警

**v4.8.0** · 外部服务凭据面板：API Key / Base / Model 三件套配置 + 直连调用

**v4.7.4** · `dream.py` 暗盘引用清理：移除所有 `SilentDiviner` 直写引用，改为"暗盘模块"/"暗盘"表述；原功能逻辑不变

**v4.7.3** · `admin.py` 日志数硬编码修复：14→动态 `len(specs)`(31)；`test_proactive_antispam` 方法名同步；209 测试全通过

**v4.7.2** · `client.py` 全面重构：SSE 解析修复（字节块→逐行读取 + delta.text 提取）；补齐 7 个缺失 API 端点（health/ensure_client/kline/techniques/cache/library/ab_assign）；`liuyao` 全参数对齐后端；`interpret` 新增 view/allFlowData 流盘视图参数

**v4.7.1** · 剩余模块批量优化：token_monitor 迁移 dataclass 去重、character_pool 压缩模板、main.py cron 去重、counseling.py EMOTION_KEYWORDS 去重

**v4.7.0** · Persona 模块优化：提取 `_NUDGE_RULES` 数据表消除三重 if/any 重复；`from_dict` 改用字段映射表消除逐字段赋值；`live_a_bit` 增加时间守卫避免高频刷新；合并 `note` 取值为单行表达式

**v4.6.9** · 对话检视系统优化：`calculate_weight` 提取通用关键词打分方法消除 4 段重复迭代；`from_dict` 改用字段映射表消除逐字段赋值；`list_conversations`/`get_stats` 增加最大用户限制避免全量 KV 加载

**v4.6.8** · 主动引擎优化：ProactiveEngine 增加 `_session_cache` 避免重复 KV 查询已活跃会话；关怀间隔改为可配置 `proactive_min_interval`；新增 `flush_inactive` 批量清理过期会话

**v4.6.7** · 记忆系统优化：MemoryCore 衰减改为惰性计算（仅检索时触发），减少日常操作的开销；LRU 淘汰策略改为保留最近交互的一半用户，而非固定 250 上限

**v4.6.6** · LLM 注入钩子拆解为职责链模式：`HookProcessor` 基类 + 5 个具体处理器（CrisisHook/PersonaHook/CharacterHook/CounselingHook/BlessingHook）通过 `HookChain` 按序执行，消除 200+ 行上帝类

**v4.6.5** · 提取 LLM 注入钩子至独立 `HookProcessor` 类，开始拆解上帝类逻辑（架构过渡版本）

**v4.6.4** · MingbuStar 基类引入 `__getattr__` 动态属性代理，消除 50+ 行冗余属性代理代码

**v4.6.3** · ProviderRouter 缓存 + 日志降级；SelfEvolution 引入 `asyncio.Semaphore` 并发控制 + 指数退避重试

**v4.6.2** · 部署工具化：新增 `deploy_to_vps.sh` 一键部署脚本（纳入仓库跟踪），默认克隆开发分支 `buddy版`，支持 Docker/原生两种模式与分支可配

**v4.6.1** · 定位澄清：明确插件是「聊天机器人的灵魂，而非算命工具」；命理仅作后台静默洞察通道，结论伪装成「直觉」织入对话，默认绝不主动以算命面貌出场

**v4.6.0** · 记忆语义向量召回(Embedding) 接入：按当前消息对相关记忆做余弦相似度语义召回，提升注入 LLM 的记忆相关性；provider 获取兼容 AstrBot 原生 Embedding 与社区 astrbot_plugin_embedding_adapter，缺 provider 优雅退回原序/Rerank

**v4.5.0** · 语音转文字(STT) 让 Bot 听语音 + 记忆重排序(Rerank) 提升相关性；provider 获取跨 AstrBot 版本兼容（STT/TTS/Rerank），缺 provider 优雅降级

**v4.4.0** · 每月后台静默关注与命理方法分流

**v4.5.0** · 语音转文字(STT) 让 Bot 听语音 + 记忆重排序(Rerank) 提升相关性；provider 获取跨 AstrBot 版本兼容（STT/TTS/Rerank），缺 provider 优雅降级
> **技法突破**：命理后台从"被动查询"升级为"主动按月留意"，并按用户命理方法分流测算/卜问

- 新增 `background_watch` 模块：每月 1 号 cron 静默推算各用户当前大限/流年/**流月**
- 流月由流年干支按五虎遁纯 Python 推导，零依赖、零命理库
- 按命理方法分流：命盘类（紫微/八字）走 `/api/combined` 测算；卜卦类（六爻/奇门/梅花）走 `/api/liuyao` `/api/qimen` 卜问情况
- 高权重情绪话语实时触发后台重算（带冷却），捕捉"特殊事件"前兆
- 新增 `/命理方法` 命令，用户可设定 紫微/八字/六爻/奇门/梅花/自动
- 暗盘守护 `disguise_guard`：命理输出前术语扫描，泄露即拦截改写
- 危机两线识别（私聊自伤意念→技术开导；自杀进行中→热线注入）
- 新增 11 个测试模块，全量测试通过；ruff/mypy 门禁干净

**v4.3.0** · 对话权重评估与智能记忆抽取
> **技法突破**：引入信息密度评估机制，解决"什么值得记"的筛选问题

- 对话存储上限提升至每用户 5000 条原始记录
- 建立 9 维度对话权重评分模型：情感密度、亲密度、自我披露、重要话题、长度价值、表达强度、私聊加成、回复质量、负面惩罚
- 高权重对话（≥ 0.45）自动入记忆库，按主导权重因素分配记忆类型（情景/情绪/语义）
- 新增高权重对话索引（每用户 200 条上限），支持快速检索
- 管理面板新增高权重对话子面板：权重因素可视化、批量入记、全量重算权重
- 统计面板扩展为 6 卡片概览：总对话数、高权重数、已入记数、入记率、日均对话、活跃用户

**v4.2.9** · 全量对话检视与学习系统
> **技法突破**：从单点黑话学习升级为全域对话语料库管理

- 群黑话检视模块升级为「对话检视与学习」系统
- 新增 `conversation_inspector` 模块，统一管理所有用户/群组对话
- 支持对话列表浏览、多维度搜索筛选、学习标注、知识提取
- 管理面板改版为三栏布局：全部对话 / 已学知识 / 统计概览
- 新增 6 个对话检视管理 API

**v4.2.8** · 管理面板交互体验重塑
> **技法突破**：运营工具的可用性直接影响系统调试效率

- 管理面板全面重新设计，采用神秘/魔法视觉主题
- 星空背景动画、毛玻璃卡片、渐变按钮、进度条动效
- 配置项按功能域重新分类，每项添加详细说明文字
- 角色收集模块卡片化展示，实时进度条动画
- 前端交互逻辑完全重写，与新 UI 架构匹配

**v4.2.7** · 平台架构兼容性升级
> **技法突破**：适配宿主平台演进，降低接入摩擦

- 适配 AstrBot v4.26+ 配置 schema 格式规范
- 配置结构扁平化，统一使用 AstrBot 标准类型名（int/float/bool/string 等）
- KV 存储接口迁移：`get_kv` / `set_kv` → `get_kv_data` / `put_kv_data`
- 插件中文名称更新为「命灵·AI 伴侣」

**v4.2.6** · 群聊黑话自动学习
> **技法突破**：语境自适应能力的初步探索

- 新增 `group_slang.py` 群黑话管理器
- 自动识别并学习群内新词、置信度动态评估、上下文注入
- 管理面板新增群黑话检视页：查看、编辑、删除、注入状态切换

### v3.x — 拟人增强期

**v3.0.0** · 拟人系统全面搭建
> **技法突破**：从单一人格对话升级为完整的拟人陪伴系统

- 综合参考十余个成熟开源插件的设计模式
- 记忆系统：5 类记忆分层 + 时间衰减 + 夜间巩固
- 人格系统：Izard 四维情绪模型 + 行为模式库
- 主动关怀：cron 决策引擎 + 免打扰规则 + 冷却机制
- 梦境系统：夜间记忆重放巩固 + 意象叙事生成
- 成长系统：LLM 对话反思 + 用户画像迭代 + 里程碑事件
- 多 Star 拆分架构，模块职责清晰分离

### v2.x — 心理咨询引擎期

**v2.0.0** · 心理咨询能力框架
> **技法突破**：命理工具化——用命理做镜子，以咨询为方法

- 情绪信号追踪：压力 / 迷茫 / 难过 / 期待 四类情绪模式检测
- 近况分析引擎：情绪信号 + 命盘信息 → 心理状态推演
- 引导式对话钩子：温和抛出话题，不强行推进
- 四步咨询流程：共情 → 倾听 → 命理镜映 → 开放式提问
- 正向预言：运势向好时的鼓励式表达

### v1.x — 基础命理期

**v1.0.0** · 初始版本
> **技法突破**：命理能力的 LLM 工具化封装

- 八字排盘：四柱、日主、五行、旺衰、格局、用神、大运、流年
- 紫微斗数：十二宫星曜分布、五行局、命宫/身宫、大限走势
- 六爻占卜：铜钱起卦、卦名、六爻排列、动爻分析
- LLM 函数调用集成，命理解释自然语言化
- 基础被动信息收集机制

---

## 文件结构

```
astrbot_plugin_mingbu/
├── main.py                    # 核心 Star，极薄生命周期
├── core.py                    # 共享内核单例 MingbuCore + 薄基类 MingbuStar（390 行）
├── constants.py               # 全插件共享常量（KV 键名/命理方法集/预编译正则/超时）
├── _version.py                # 版本号（与 metadata.yaml、README 徽章由测试钉死一致）
├── _conf_schema.json          # 配置 schema
├── metadata.yaml              # 插件元数据
├── .env.example               # 环境变量示例
├── client.py                  # Mingbu API 客户端
├── kv_store.py                # KV 持久化封装
├── kv_cache.py                # KV 写回缓存 + 防抖落盘
├── formatters.py              # 输出格式化
├── metrics.py                 # 运行指标采集
├── background_watch.py        # 每月后台静默关注（按命理方法分流测算/卜问）
├── silent_divination.py       # 暗盘：命理洞察蒸馏 + 术语伪装
├── disguise_guard.py          # 暗盘术语守护（防命理术语泄露）
├── emotion_detect.py          # 情绪分类与权重评估
├── self_evolution.py          # 自我演化（对话反思 / 画像迭代）
├── health.py                  # 健康生活引导
├── tarot.py                   # 塔罗占卜
├── astrology.py               # 星象 / 流月推演
├── stt.py                     # 语音转文字接入
│
├── memory_core.py             # 记忆生命周期管理
├── persona.py                 # 人格与情绪状态机
├── character_pool.py          # 角色池与模板
├── character_collection.py    # 角色收集逻辑
├── conversation_inspector.py  # 对话检视与权重评分
├── group_slang.py             # 群黑话学习管理
├── growth.py                  # 成长与反思引擎
├── dream.py                   # 梦境生成与巩固
├── proactive.py               # 主动行为编排
├── provider_router.py         # 多 Provider 编排
├── token_monitor.py           # Token 用量监控
├── worldview_adapter.py       # 世界观转译
├── news_search.py             # 新闻阅读与主动搜索
├── social.py                  # 社交关系与群组观察
├── verify_hooks.py            # 部署后钩子自检脚本
│
├── star_mixins/               # MingbuStar 的横切能力（v4.8.2 从 core.py 拆出）
│   ├── conversation.py        # 结构化轮次记录、话题追踪
│   ├── health.py              # 健康档案存取
│   ├── persona_ctx.py         # 人格块拼装、记忆语义召回与重排
│   ├── profile.py              # 用户档案、出生信息与校验
│   └── llm_hook.py            # LLM 请求钩子主体（driver → HookChain）
│
├── stars/apis/                # 管理 API 按领域拆分
│   ├── diagnostics.py         # 能力状态与外部服务连通性诊断
│   ├── system.py              # 配置、KV、模型目录与 Provider
│   └── overview.py            # 概览、用户、关系图与手动触发
│
├── counseling/                # 心理咨询引擎（子包）
│   ├── session.py             # 会话管理
│   ├── crisis.py              # 危机两线识别
│   ├── therapy.py             # 疗法策略
│   ├── prompts.py             # 咨询提示词
│   ├── tracker.py             # 情绪追踪
│   └── emotion_lexicon.py     # 情绪词库
│
├── hooks/                     # LLM 请求钩子链（可插拔、支持短路）
│   ├── base.py                # HookChain 与基类
│   ├── persona.py             # 人格注入
│   ├── character.py           # 角色注入
│   ├── counseling.py          # 咨询注入
│   ├── crisis.py              # 危机干预
│   └── blessing.py            # 祝福语注入
│
├── stars/
│   ├── admin.py               # 管理面板 Web API
│   ├── collectors.py          # 被动收集器（regex）
│   ├── commands.py            # 斜杠命令
│   ├── tools.py               # LLM 工具
│   └── enhancements.py        # 消息增强（TTS/图片/撤回/防抖）
│
├── pages/admin/               # 管理面板前端
│   ├── index.html
│   ├── style.css
│   └── app.js
│
├── devtools/                  # 开发工具（不参与插件运行时）
│   ├── astrbot_stub.py        # AstrBot 内存桩，让测试脱离真实运行时
│   ├── devserver.py           # 免 AstrBot 的本地调试服务器
│   └── smoke.py               # 冒烟检查
│
└── tests/                     # 测试用例（随 CI 实测，当前 779 项，覆盖 3.9 / 3.11 / 3.13）
```

> **依赖方向**：`constants ← star_mixins ← core ← main / stars/*`，单向无环。
> `star_mixins` 里的 mixin 不 import `core`——方法体内的 `self.persona` / `self.memory`
> 等访问在运行时由 `MingbuStar.__getattr__` 代理到 `MingbuCore`（鸭子类型注入），
> 共享常量则统一取自 `constants`。这是 v4.8.2 能把 `core.py` 从 927 行降到 390 行
> 而不引入循环导入的前提。

---

## 排错

### 插件加载失败

```bash
# 查看 AstrBot 日志
docker logs astrbot 2>&1 | grep mingbu

# 常见问题：
# - No module named 'core' → 确保插件目录正确
# - 配置类型错误 → 升级 AstrBot 到 4.26+
# - get_kv 错误 → 升级插件到 v4.2.7+
```

### 命理服务不可用

```bash
# 检查服务
curl http://localhost:2963/api/health
```

### 管理面板打不开

确保 AstrBot 版本 ≥ 4.16，插件版本 ≥ 4.2.0。

---

## 开发

### 环境准备

```bash
python -m venv .venv
.venv/Scripts/python -m pip install pytest ruff aiohttp    # Windows
# source .venv/bin/activate && pip install pytest ruff aiohttp   # Linux / macOS
```

### 代码检查与测试

```bash
# 代码格式化
ruff check . --fix
ruff format .

# 运行测试（当前以 pytest 实测收集数为准）
python -m pytest tests/ -q
```

测试**不需要真实的 AstrBot 运行时**：`tests/conftest.py` 会注入 `devtools/astrbot_stub.py`
提供的内存桩，因此在任何干净的 Python 环境里都能直接跑通。

### 跨版本验证（重要）

`pyproject.toml` 声明的下限是 **Python 3.9**（AstrBot 的运行环境），CI 矩阵为
`3.9 / 3.11 / 3.13`。**改动涉及 asyncio 时请务必在 3.9 上真跑一遍**：

```bash
py -3.9 -m pytest tests/ -q     # 或用 uv/pyenv 准备 3.9 环境
```

原因是 3.9 与 3.10+ 存在运行时行为差异，静态检查抓不到。典型的一条：
模块级 `asyncio.Lock()` 在 3.9 构造时就绑定「当时的」事件循环，之后 `asyncio.run()`
起新 loop 再 await 会抛 `RuntimeError: is bound to a different event loop`；3.10+
构造时不绑定，所以只跑新版本永远看不到。**写法上一律改为惰性创建**（首次使用时
在当前 loop 内构造），不要在模块顶层或类属性上直接实例化 asyncio 原语。

### 本地调试服务器

不启动 AstrBot 也能调管理面板与业务逻辑：

```bash
python devtools/devserver.py                 # 默认 127.0.0.1:8848
python devtools/devserver.py --port 9000
python devtools/devserver.py --fresh         # 清空本地数据重新播种
python devtools/devserver.py --no-seed       # 不播种，验证空态
```

### 发布前检查清单

版本号分散在四个文件里，`tests/test_metadata_consistency.py` 会强制它们一致：

| 文件 | 用途 |
|------|------|
| `_version.py` | 唯一数据源；经 `/stats` 接口透出给管理面板 |
| `metadata.yaml` | AstrBot 插件管理器读取、更新检查、插件市场展示 |
| `README.md` 徽章 | 仓库门面 |
| `pages/admin/index.html` | 面板页脚的**离线降级占位**（正常情况由 `app.js` 从 `/stats` 回填） |

此外还要在 README「版本演进」里补上对应条目——测试会限定在该章节内按
`**vX.Y.Z**` 格式校验，正文其他位置顺带提及版本号不算数。

改完跑一遍 `python -m pytest tests/test_metadata_consistency.py` 即可确认没漏项。

> **为什么面板占位值也要钉死**：v4.8.2 之前 `index.html` 的页脚硬编码在 `4.7.4`，
> 而 `app.js` 从不消费接口返回的 `version` 字段，导致面板整整滞后三个版本无人察觉。
> 现已改为接口回填 + 占位值纳入测试，两条路径互为兜底。

---

## 许可证

MIT License
