"""
kv_store.py -- KV 存储基类（统一 JSON 序列化 + 异常处理）
========================================================
为所有基于 AstrBot KV Store 的模块提供统一的读写基类，
消除重复的 json.loads / json.dumps + try-except 模式。

用法：
    class MyManager(KVStoreBase):
        def __init__(self, plugin):
            super().__init__(plugin, "mingbu.my_prefix")

        async def get_something(self, key):
            return await self._load_json(f"{self.prefix}:{key}", default={})

        async def set_something(self, key, data):
            await self._save_json(f"{self.prefix}:{key}", data)
"""
from __future__ import annotations

import json
from typing import Any

from astrbot.api import logger


class KVStoreBase:
    """KV 存储基类：封装 JSON 序列化与异常处理。"""

    def __init__(self, plugin, prefix: str = ""):
        """
        Args:
            plugin: AstrBot 插件实例（需提供 get_kv_data / put_kv_data）
            prefix: KV key 前缀（可选，子类可用 self.prefix 访问）
        """
        self.plugin = plugin
        self.prefix = prefix

    async def _load_json(self, key: str, default: Any = None) -> Any:
        """加载 JSON 格式的 KV 数据。

        Args:
            key: KV 键
            default: 不存在或解析失败时的默认值

        Returns:
            解析后的数据，失败返回 default
        """
        try:
            raw = await self.plugin.get_kv_data(key, None)
            if raw is None:
                return default
            if isinstance(raw, (dict, list)):
                return raw
            return json.loads(raw)
        except Exception as e:
            logger.debug(f"[kv_store] 加载 {key} 失败(用默认值): {e}")
            return default

    async def _save_json(self, key: str, data: Any) -> bool:
        """保存 JSON 格式的 KV 数据。

        Args:
            key: KV 键
            data: 要保存的数据（需可 JSON 序列化）

        Returns:
            True 成功，False 失败
        """
        try:
            await self.plugin.put_kv_data(key, json.dumps(data, ensure_ascii=False))
            return True
        except Exception as e:
            logger.warning(f"[kv_store] 保存 {key} 失败: {e}")
            return False

    async def _delete_key(self, key: str) -> bool:
        """删除一个 KV 键（如果底层支持的话）。

        底层不支持时通过存空值模拟，失败返回 False。
        """
        try:
            delete_fn = getattr(self.plugin, "delete_kv_data", None)
            if delete_fn:
                await delete_fn(key)
                return True
            await self.plugin.put_kv_data(key, "")
            return True
        except Exception as e:
            logger.debug(f"[kv_store] 删除 {key} 失败: {e}")
            return False
