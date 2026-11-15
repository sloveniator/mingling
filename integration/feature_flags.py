"""core/feature_flags.py -- 命灵 2.0 增强能力统一门控。

2.0 第一阶段任务 4：把散落各处的 ``config.get('xxx_enabled', True)`` 收拢为
一个统一门控对象，并提供 Observe/Enhance/Assist 三种运行模式 + 总开关。

设计原则（对应 2.0 方案 1.2 / 7.3）：
- enabled=False 时命灵完全退出增强路径，AstrBot 原生聊天 100% 可用。
- 三种模式：
    observe  只观察记录、不修改模型请求（测试/调试）。
    enhance  按需注入少量动态上下文（默认）。
    assist   允许调用记忆/情绪/关系等工具（深度陪伴）。
- 门控只读配置，不持久化；配置来源仍是现有 _conf_schema.json（不建第二套配置）。

本模块不 import core，避免循环依赖；由 core 持有并按需读取配置刷新。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict

# 三种运行模式
MODE_OBSERVE = "observe"
MODE_ENHANCE = "enhance"
MODE_ASSIST = "assist"
VALID_MODES = (MODE_OBSERVE, MODE_ENHANCE, MODE_ASSIST)


@dataclass
class FeatureFlags:
    """命灵增强能力门控。字段默认值与现有 _conf_schema 开关语义对齐。

    通过 :meth:`from_config` 从插件配置构造；调用方在每轮请求开始时刷新，
    拿到的是当轮生效的门控快照（避免请求中途配置变化导致半开半关）。
    """
    enabled: bool = True
    mode: str = MODE_ENHANCE
    memory: bool = True
    persona: bool = True
    relationship: bool = True
    emotion: bool = True
    proactive: bool = True
    background_watch: bool = True

    def __post_init__(self):
        if self.mode not in VALID_MODES:
            self.mode = MODE_ENHANCE

    # ---- 模式判定 ----
    @property
    def is_observe(self) -> bool:
        return self.mode == MODE_OBSERVE

    @property
    def is_enhance(self) -> bool:
        return self.mode == MODE_ENHANCE

    @property
    def is_assist(self) -> bool:
        return self.mode == MODE_ASSIST

    def should_modify_request(self) -> bool:
        """本模式是否允许修改模型请求（注入上下文）。

        observe 模式只记录不注入；enhance/assist 均可注入。
        """
        return self.enabled and self.mode in (MODE_ENHANCE, MODE_ASSIST)

    def is_enabled(self, name: str) -> bool:
        """总开关 + 单项能力开关都为真才放行。"""
        if not self.enabled:
            return False
        return bool(getattr(self, name, False))

    @classmethod
    def from_config(cls, config: Dict) -> "FeatureFlags":
        """从插件配置构造门控快照。

        兼容现有配置键：总开关 ``enabled``（缺省 True）、运行模式
        ``integration_mode``（缺省 enhance）。各能力子开关沿用现有
        ``xxx_enabled`` 键（缺省 True，与既有行为一致）。
        """
        cfg = config or {}
        return cls(
            enabled=bool(cfg.get("enabled", True)),
            mode=str(cfg.get("integration_mode", MODE_ENHANCE)),
            memory=bool(cfg.get("memory_enabled", True)),
            persona=bool(cfg.get("persona_enabled", True)),
            relationship=bool(cfg.get("relationship_enabled", True)),
            emotion=bool(cfg.get("emotion_enabled", True)),
            proactive=bool(cfg.get("proactive_enabled", True)),
            background_watch=bool(cfg.get("background_watch_enabled", True)),
        )
