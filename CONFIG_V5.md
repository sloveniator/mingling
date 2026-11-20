# 命灵 v5 配置模板（mingling）

v5 只读取下列键；`_conf_schema.json` 中保留的 v4 旧键不会影响运行，但已不再生效。
AstrBot 面板会把本文件对应的 JSON 作为 `mingling` 插件配置。

```json
{
  "enabled": true,
  "state_layer_enabled": true,
  "emotion_trace_enabled": true,
  "relationship_event_enabled": true,
  "topic_stack_enabled": true,
  "open_loop_enabled": true,
  "relation_memory_enabled": true,
  "proactive_enabled": true,
  "divination_tools_enabled": true,
  "counseling_tools_enabled": true,
  "state_layer_max_tokens": 220,
  "state_layer_max_anchors": 2,
  "proactive_max_per_day": 2,
  "proactive_mood_window_hours": 48,
  "proactive_daily_greet_min_gap_hours": 20,
  "proactive_quiet_start_hour": 0,
  "proactive_quiet_end_hour": 7,
  "proactive_max_unanswered_open_loop": 2,
  "proactive_max_unanswered_daily": 1,
  "open_loop_window_days": 7,
  "relation_memory_max_items": 120,
  "emotion_anchor_threshold": 0.7,
  "emotion_anchor_multiplier": 3.0,
  "timeline_max_lines": 500
}
```

| 键 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `enabled` | bool | `true` | 总开关；关闭时不注册任何钩子。 |
| `state_layer_enabled` | bool | `true` | 是否把状态层注入 `system_prompt`。 |
| `emotion_trace_enabled` | bool | `true` | 记录情绪四维轨迹。 |
| `relationship_event_enabled` | bool | `true` | 记录关系阶段变化。 |
| `topic_stack_enabled` | bool | `true` | 话题栈跟踪。 |
| `open_loop_enabled` | bool | `true` | 未闭环话题跟踪。 |
| `relation_memory_enabled` | bool | `true` | 关系记忆召回。 |
| `proactive_enabled` | bool | `true` | 主动消息总开关。 |
| `divination_tools_enabled` | bool | `true` | 命理工具可见性。 |
| `counseling_tools_enabled` | bool | `true` | 开解工具可见性。 |
| `state_layer_max_tokens` | int | `220` | 状态层注入预算。 |
| `state_layer_max_anchors` | int | `2` | 单次最多注入记忆条数。 |
| `proactive_max_per_day` | int | `2` | 每日主动消息上限。 |
| `proactive_mood_window_hours` | int | `48` | 情绪信号有效窗口。 |
| `proactive_daily_greet_min_gap_hours` | int | `20` | 日常问候最小间隔。 |
| `proactive_quiet_start_hour` | int | `0` | 免打扰开始（可跨午夜）。 |
| `proactive_quiet_end_hour` | int | `7` | 免打扰结束。 |
| `proactive_max_unanswered_open_loop` | int | `2` | 未闭环话题追问上限。 |
| `proactive_max_unanswered_daily` | int | `1` | 日常问候未回复上限。 |
| `open_loop_window_days` | int | `7` | 未闭环话题有效期。 |
| `relation_memory_max_items` | int | `120` | 关系记忆条数上限。 |
| `emotion_anchor_threshold` | float | `0.7` | 情绪锚点阈值。 |
| `emotion_anchor_multiplier` | float | `3.0` | 情绪锚点权重倍率。 |
| `timeline_max_lines` | int | `500` | 时间线接口单次返回上限。 |

## 从 v4 迁移

```bash
# 先 dry-run 查看动作
python migrate_v4_to_v5.py --data-dir ~/.astrbot

# 确认后执行
python migrate_v4_to_v5.py --data-dir ~/.astrbot --apply
```

脚本会：

1. 把 `config/astrbot_plugin_mingbu.json` 映射为 `config/mingling.json`；
2. 把 `plugin_data/astrbot_plugin_mingbu/` 复制为 `plugin_data/mingling/`；
3. 旧版 `quiet_hours_*` 自动映射到 `proactive_quiet_*`，并强制 `enabled=true`。

> AstrBot 数据库内的 KV 按插件名隔离，脚本不会自动改写。旧关系/情绪/记忆若需保留，
> 需在数据库层把 `astrbot_plugin_mingbu` 命名空间导出后导入 `mingling`。