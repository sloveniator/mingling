from __future__ import annotations

import asyncio
import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any

from astrbot.api import logger


def _data_root() -> Path:
    # 环境变量覆盖：测试/CI 可重定向到临时目录，避免依赖真实 AstrBot 数据路径
    override = os.environ.get("MINGLING_DATA_ROOT")
    if override:
        return Path(override)
    try:
        from astrbot.core.utils.astrbot_path import get_astrbot_data_path  # type: ignore
        return Path(get_astrbot_data_path()) / "plugin_data" / "mingling"
    except Exception:
        return Path.home() / ".astrbot" / "plugin_data" / "mingling"


def _umo_hash(umo: str) -> str:
    return hashlib.sha1(umo.encode("utf-8")).hexdigest()[:12]


class Store:
    """
    统一存储门面：
    - 结构化状态（情绪/关系/话题栈/未闭环/记忆）走 KV，每 key 一份 JSON
    - 可增长时间线走本地 jsonl（符合 AstrBot 对大文件的建议）
    """

    KNOWN_UMOS_KEY = "mingling.known_umos"

    def __init__(self, star):
        self._star = star
        self._cache: dict[str, Any] = {}
        self._lock = asyncio.Lock()
        self._root = _data_root()
        self._timeline_dir = self._root / "timeline"
        self._timeline_dir.mkdir(parents=True, exist_ok=True)

        self._known_umos: set[str] = set()
        self._known_umos_loaded = False

    # ---------- KV ----------
    async def kv_get(self, key: str, default: Any = None) -> Any:
        if key in self._cache:
            return self._cache[key]
        try:
            val = await self._star.get_kv_data(key, default)
        except Exception:
            import traceback
            logger.error(f"[mingling] kv_get failed: {key}\n{traceback.format_exc()}")
            return default
        self._cache[key] = val
        return val

    async def kv_set(self, key: str, value: Any) -> None:
        self._cache[key] = value
        try:
            await self._star.put_kv_data(key, value)
        except Exception:
            import traceback
            logger.error(f"[mingling] kv_set failed: {key}\n{traceback.format_exc()}")

    # ---------- 用户级 JSON ----------
    async def load(self, umo: str, suffix: str, default: Any) -> Any:
        return await self.kv_get(f"mingling.{_umo_hash(umo)}.{suffix}", default)

    async def save(self, umo: str, suffix: str, value: Any) -> None:
        await self.kv_set(f"mingling.{_umo_hash(umo)}.{suffix}", value)

    # ---------- 时间线 ----------
    def _timeline_path(self, umo: str) -> Path:
        return self._timeline_dir / f"{_umo_hash(umo)}.jsonl"

    async def append_timeline(self, umo: str, kind: str, payload: dict) -> None:
        record = {"ts": time.time(), "kind": kind, "payload": payload}
        path = self._timeline_path(umo)

        def _write() -> None:
            with path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")

        try:
            await asyncio.to_thread(_write)
        except Exception:
            import traceback
            logger.error(f"[mingling] append_timeline failed: {umo}\n{traceback.format_exc()}")

    async def read_timeline(self, umo: str, limit: int = 500) -> list[dict]:
        path = self._timeline_path(umo)
        if not path.exists():
            return []

        def _read() -> list[str]:
            with path.open("r", encoding="utf-8") as f:
                return f.readlines()[-limit:]

        try:
            lines = await asyncio.to_thread(_read)
            return [json.loads(x) for x in lines if x.strip()]
        except Exception:
            import traceback
            logger.error(f"[mingling] read_timeline failed: {umo}\n{traceback.format_exc()}")
            return []

    # ---------- umo 清单（内存去重） ----------
    async def remember_umo(self, umo: str) -> None:
        if not self._known_umos_loaded:
            raw = await self.kv_get(self.KNOWN_UMOS_KEY, [])
            self._known_umos = set(raw) if isinstance(raw, list) else set()
            self._known_umos_loaded = True
        if umo in self._known_umos:
            return
        self._known_umos.add(umo)
        if len(self._known_umos) > 2000:
            self._known_umos = set(sorted(self._known_umos)[-2000:])
        await self.kv_set(self.KNOWN_UMOS_KEY, list(self._known_umos))

    async def list_known_umos(self) -> list[str]:
        if not self._known_umos_loaded:
            raw = await self.kv_get(self.KNOWN_UMOS_KEY, [])
            self._known_umos = set(raw) if isinstance(raw, list) else set()
            self._known_umos_loaded = True
        return list(self._known_umos)
