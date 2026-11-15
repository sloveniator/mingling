# 暗盘（SilentDiviner）准确性压力测试方案

> 版本：v2（全面扩充版）· 制定：2026-07-15 · 执行：Senior Developer（高级开发工程师）
> 对应需求：基于 mingbu 旧经验，对暗盘系统做全面、准确的高强度压力测试
> 适用代码：`astrbot_plugin_mingbu` 暗盘静默排盘 → 心理咨询洞察 → 预感梦 cue 全链路

---

## 一、背景与目标

暗盘（SilentDiviner）是命卜插件的核心隐蔽能力：后台用真实命理引擎（八字 `lunar-javascript` + 紫微 `iztro`）为用户**静默算盘**，再将命理结构**翻译**为"朋友般的直觉式心理咨询"，经 `premonition_cue` 单向流入"预感梦"通道。它对外**绝不能**出现任何命理术语（硬性铁律）。

本次压力测试目标：

1. **准确性**：暗盘选运（大运/流年）、宫位定位在真实出生数据下与命理真值**完全一致**。
2. **健壮性**：高并发、大数据量、边界值、异常输入下**不崩、不串扰、不串味**。
3. **合规护栏**：300+ 组合零命理术语泄漏；暗盘**单向**抵达梦境，绝不反向依赖。
4. **环境一致**：测试 oracle 与飞牛生产后端**同源引擎**，杜绝"测试通过、上线漂移"。

---

## 二、借鉴的 mingbu 旧经验与教训

| 旧经验 / 教训 | 本次如何落地 |
|--------------|--------------|
| **真值 oracle 工作流**：后端不可达时，用同源引擎（lunar-javascript + iztro）离线生成金标准 | 继续用 `oracle.mjs` 生成 36 样本，新增 `oracle_large.mjs` 生成 **353 大样本**（同库同口径） |
| **大运口径漂移教训**：周岁/虚岁之争在边界年 ±1 运漂移 → 改用公历 `startYear` 主路径 | 已修复 `_find_current_da_yun`，本次用 353 样本回归验证漂移率归零 |
| **质量门禁三件套**：`ruff` + `mypy` + `pytest` 全绿才算过 | 作为本方案强制通过门槛（见第七节） |
| **暗盘单向护栏**：`dream.py` 永不 import 暗盘通道，仅经 `premonition_cue` 单向流入 | 禁令牌扫描 + 单向性断言持续纳入回归 |
| **零术语模糊测试**：5 元素×5 旺衰×12 地支×主星 300+ 组合断言零禁用词 | 扩充为全链路断言（洞察抽取 + cue + weave 文案） |
| **⚠️ NEW 教训·宫名后缀**：iztro 2.5.8 仅 `命宫` 带"宫"后缀，其余 10 宫无后缀 → `KEY_PALACES` 全带后缀时 `extract_key_palaces` 在真实数据上**只返回 1/6 宫位** | 新增 `normalize_palace_name` 归一化，并加 `test_crossvalidate_all_key_palaces_against_iztro` 回归护栏（断言每个样本 6 宫全中） |
| **⚠️ NEW 教训·weave 文案泄漏**：`weave_insights_into_prompt` 的"使用规则"文本曾含 `命盘/八字/紫微` 禁用词 | 重写为不显式拼写禁用词的措辞，并加 `test_output_module_weave_and_extract` 断言零泄漏 |

---

## 三、全模块覆盖矩阵（对应需求 #2）

| 核心模块 | 覆盖子项 | 测试手段 | 状态 |
|---------|---------|---------|------|
| **A. 数据输入** | 出生信息解析、字段缺失/畸形、性别/时辰边界、晚子时、闰月 | `test_input_module_degradation`、`test_missing_fields_robust`、`test_child_below_first_da_yun_returns_none`、`test_exact_start_year_boundary`、`test_liu_nian_missing_for_current_da_yun` | ✅ |
| **B. 数据处理** | 大运选择（公历 startYear 主路径 + 虚岁兜底 + 童限/超龄）、流年定位、宫位抽取（归一化）、大限宫名归一、大运序列差分 | `test_crossvalidate_da_yun_liu_nian_against_engine`、`test_crossvalidate_key_palaces_against_iztro`、`test_crossvalidate_all_key_palaces_against_iztro`、`test_last_da_yun_and_overage`、`test_virtual_age_fallback_without_start_year`、`test_daxian_palace_normalization`、`test_synthetic_large_scale_da_yun_differential` | ✅ |
| **C. 结果输出** | 洞察抽取（零术语）、weave 织入提示（零术语）、cue 产出（零术语、朦胧）、知识映射覆盖、daXian 领域化转译 | `test_extract_insights_zero_forbidden_all_branches`、`test_premonition_cue_zero_forbidden`、`test_knowledge_mapping_coverage`、`test_output_module_weave_and_extract`、`test_daxian_palace_normalization` | ✅ |
| **D. 并发与规模** | 32 线程纯函数零串扰、asyncio 60×2 并发洞察+cue、None 输入不退化、353 样本精度+吞吐、800 序列差分吞吐 | `test_high_concurrency_pure_functions_no_contamination`、`test_high_concurrency_async_insights_and_cue`、`test_high_concurrency_none_degradation`、`test_large_data_da_yun_liu_nian_accuracy_and_throughput`、`test_synthetic_large_scale_da_yun_differential` | ✅ |
| **E. 架构护栏** | 暗盘单向（dream 不反向 import）、禁令牌扫描 | `test_darkchannel_single_direction_guard` | ✅ |

---

## 四、压力场景设计与准确性指标 / 通过标准（对应需求 #3、#4）

> 每个场景均设**准确性指标**与**明确通过标准**。

### 场景 1：交叉验证（基准准确性）
- **设计**：暗盘输出 vs oracle 真值（公历口径）逐条对齐。
- **样本**：小样本 36 + 大样本 353 = **389** 条真实出生。
- **准确性指标**：大运选中值 = `truth_gregorian_da_yun`；流年 = `_expected_liu_nian`；六关键宫位全部命中。
- **通过标准**：**100%**（0 偏差）。

### 场景 2：边界值
- **设计**：童限（当前年早于首运起点）、精确边界年（`startYear == cur`）、超龄（晚于末运起点）、末运。
- **准确性指标**：童限→`None`；边界年→精确对应运；超龄→末运。
- **通过标准**：覆盖上述 4 类各≥1，断言全过。

### 场景 3：异常输入
- **设计**：缺 `daYun`、缺 `birthInfo`、缺 `liuNian`、缺 `startYear`（触发虚岁兜底）、`None` 入参。
- **准确性指标**：不抛异常；兜底路径产出与独立参考实现一致。
- **通过标准**：全部分支无异常且结果可解释。

### 场景 4：大数据量
- **设计**：353 真实样本全量跑 `current_da_yun_liu_nian` + 800 合成大运序列差分。
- **准确性指标**：误差率 = 0；与独立参考实现（按 `startYear` 重算）逐条一致。
- **通过标准**：**0 偏差**；吞吐 `current_da_yun_liu_nian` ≥ 100 条/秒、差分 ≥ 2000 序列/秒（性能基线，非否决项）。

### 场景 5：高并发
- **设计**：
  - 同步：32 线程 `ThreadPoolExecutor` 跑纯函数，键=样本出生、值=结果，断言零串扰。
  - 异步：`asyncio.gather` 60×2 并发跑 `extract_insights` + `premonition_cue`。
  - 退化：`None` 入参并发下不退化、不抛异常。
- **准确性指标**：并发结果与单线程真值**逐条相等**；零全局状态污染。
- **通过标准**：所有并发结果 = 单线程基准；无异常。

### 场景 6：伪装护栏（零术语模糊测试）
- **设计**：5 元素×5 旺衰×12 地支×主星 **300+** 组合，断言洞察/cue/weave 文案零命中 11+18 禁用词。
- **准确性指标**：禁用词命中数 = 0。
- **通过标准**：**0 泄漏**。

### 场景 7：知识映射覆盖
- **设计**：12 地支 / 5 元素 / 14 主星映射表全覆盖。
- **准确性指标**：每个键都有非空前向文案；全表零禁用词。
- **通过标准**：覆盖率 100%，零泄漏。

### 场景 8：架构单向护栏
- **设计**：扫描 `dream.py` 不得 import 暗盘通道；`premonition` 仅经 `premonition_cue` 接收 cue。
- **准确性指标**：禁令牌命中数 = 0。
- **通过标准**：**0 命中**。

---

## 五、测试环境一致性（对应需求 #5）

| 维度 | 生产环境（飞牛） | 测试环境（本地隔离 venv） | 一致性措施 |
|------|----------------|------------------------|-----------|
| 八字引擎 | `lunar-javascript` | 同版本 `lunar-javascript 1.7.7`（node workspace） | oracle 用**同库**生成 fixture，金标准同源 |
| 紫微引擎 | `iztro` | 同版本 `iztro 2.5.8`（node workspace） | 宫名无后缀现象在 oracle + 测试端**同时复现并归一化**，规避版本差 |
| 大运口径 | 公历 `startYear` | 同口径（测试端按 `startYear` 真值对齐） | 杜绝岁数口径之争 |
| 时间基准 | 容器真实年 | 测试固定 `_ast._current_year` = fixture `currentYear` | 消除时间漂移 |
| 质量门禁 | — | `ruff`+`mypy`+`pytest` 全绿 | 同标准 |

**声明**：本测试**无法**在本地跑 AstrBot 真运行时（隔离 venv 未装 `astrbot`），故暗盘逻辑通过"同源 oracle 交叉验证 + 桩测试 + 禁令牌扫描"验证；**整体上线仍须在飞牛 AstrBot 容器实测**（遵循"只改源码 → `git pull && docker compose up -d --no-recreate`"法则）。

---

## 六、执行流程

1. 用 `oracle.mjs` / `oracle_large.mjs` 生成小/大两套 fixture（已生成：36 + 353）。
2. 跑 `pytest tests/test_darkchannel_stress.py` 全量（当前 **21 用例**）。
3. 跑质量门禁：`ruff check .` / `mypy .` / `pytest tests/ -q`（当前全绿：35 passed）。
4. 汇总偏差、产出报告、更新记忆。

---

## 七、强制通过门槛

- ✅ 所有 8 类场景 0 偏差 / 0 泄漏 / 0 单向违规。
- ✅ 质量门禁三件套全绿。
- ⚠️ 飞牛容器实测为上线前最后一关（本地不可代验）。

---

## 八、交付物

| 文件 | 说明 |
|------|------|
| `astrology.py`（已改） | `_find_current_da_yun` 公历 startYear 主路径 + 虚岁兜底 + 童限/超龄；`normalize_palace_name` 宫名归一 |
| `silent_divination.py`（已改） | 3 处归一化调用 + `weave_insights_into_prompt` 零术语重写 |
| `oracle.mjs` / `oracle_large.mjs`（新增） | node 真值生成器（lunar-javascript + iztro） |
| `oracle_fixtures.json`（36） / `oracle_fixtures_large.json`（353） | 真实出生样本三套大运真值 + daYun fixture + 紫微宫位 |
| `tests/test_darkchannel_stress.py`（21 用例） | 交叉验证 / 边界异常 / 大数据 / 高并发 / 护栏模糊 / 知识映射 / 单向护栏 |
| `DARKCHANNEL_STRESS_REPORT.md` | 详细测试报告（结果/偏差/改进建议） |
