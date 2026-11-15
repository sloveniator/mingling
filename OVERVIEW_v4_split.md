# astrbot_plugin_mingbu — main.py 多 Star 拆分（v4）

## 背景
`main.py` 原为 ~1180 行 god-file，承载生命周期、钩子、6 个 LLM 工具、6 个斜杠指令、4 个被动收集、7 个管理面板 Web API 全部逻辑。用户要求进一步拆成多 Star 模块，并确认采用「真·多 Star 类」架构。

## 拆分结果

| 文件 | 角色 | 关键内容 |
|------|------|---------|
| `core.py` | 共享内核 + 基类 | `MingbuCore` 单例（`api/tracker/diviner` 构造即建；`ensure(owner)` 幂等建 `memory/dream/growth/proactive/social/persona` + 载入人格；`shutdown()` 释放资源）；`MingbuStar(Star)` 基类（引擎属性代理 + `_llm_text` + 全部共享助手方法 + 钩子主体 `_on_llm_request_body`）；模块级预编译正则 |
| `main.py` | 核心 Star `MingbuPlugin` | 极薄：lifecycle（`initialize`→`core.ensure`+cron+`proactive.register_jobs`；`terminate`→`core.shutdown`）+ 唯一 `@filter.on_llm_request` 钩子（转发到 `_on_llm_request_body`）；顶层 `import stars.*` 触发 AstrBot 注册 |
| `stars/collectors.py` | `StarCollectors` | 4 个 `@filter.regex`：出生年 / 属相 / 命卜关键词 / 情绪信号 |
| `stars/tools.py` | `StarTools` | 6 个 `@filter.llm_tool`：query_bazi / query_ziwei / divination_liuyao / query_combined / query_situation / guided_reflection |
| `stars/commands.py` | `StarCommands` | 6 个 `@filter.command`：/排盘 /卜卦 /我的命盘 /设置命盘 /心情 /开解 |
| `stars/admin.py` | `StarAdmin` | `initialize` 注册 7 个 Plugin Pages Web API：stats / users / user_detail / trigger / graph / kv_get / kv_set |

## 关键设计约束（已守住）
- **单例共享**：所有 Star 共享同一个 `MingbuCore`，避免重复初始化引擎 / HTTP 连接池；`ensure(owner)` 幂等，首个初始化者完成真实创建。
- **AstrBot 发现机制**：AstrBot 扫描插件目录下所有含 `Star` 子类的模块；`main.py` 必须在顶层 `import` 各子 Star 才能注册——已满足。
- **暗盘→预感梦连线保留**：`admin.api_trigger`(premonition) 与编排层仍先 `diviner.premonition_cue` 取零命理术语 cue 传入 `dream.premonition(cue=)`；`dream.py` 仍零暗盘引用，`test_dream_does_not_reference_divination` 护栏不受影响。
- **兼容既有校验**：`verify_hooks.py` 仍 `from main import MingbuPlugin` 且 `_on_llm_request(self,event,req)` 签名不变；pytest 只 import `astrology/client/counseling/...` 等，**不碰 main**，拆分不影响测试。

## 验证结果
- **RUFF** `All checks passed!`
- **MYPY** `Success: no issues found in 22 source files`
- **PYTEST** `14 passed`
- **结构校验**（注入最小 astrbot 桩）：`import main` 连带加载 5 个 `MingbuStar` 子类、各子 Star 装饰方法齐全（6+6+4+7）、`MingbuCore` 单例在多 Star 间共享、引擎属性代理正确。

## ⚠️ 待飞牛运行时复验
本地隔离 venv 未装 `astrbot`，只能验证「导入 / 类层级 / 单例共享」结构正确性。以下需在飞牛 AstrBot 容器实测确认：
- AstrBot 实际能否发现并加载 `stars/` 子 Star；
- 7 个 Web API 路由注册是否生效；
- cron 任务在多 Star 下的幂等注册。
部署按既定黄金法则：仅改 .py 源码 → `git pull && docker compose up -d --no-recreate`（AstrBot 插件重载生效，不重建容器）。
