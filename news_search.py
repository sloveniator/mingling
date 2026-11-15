"""
news_search.py -- 新闻阅读与主动搜索
====================================
调用 AstrBot 全局网页搜索或 MCP 搜索接口，形成 Bot 的"近期见闻"和"探索笔记"。
"""
import json
from typing import List, Optional

from astrbot.api import logger

from .bj_time import now_bj

KV_NEWS = "mingbu.news"
KV_SEARCH_NOTES = "mingbu.search_notes"

NEWS_TOPICS = [
    "今日热点", "科技新闻", "社会新闻", "娱乐八卦", "国际新闻", "财经新闻", "体育新闻"
]


class NewsSearchEngine:
    def __init__(self, plugin):
        self.plugin = plugin

    def _cfg(self) -> dict:
        return getattr(self.plugin, "_config", {}) if hasattr(self.plugin, "_config") else {}

    async def _web_search(self, query: str) -> List[str]:
        """调用 AstrBot 全局网页搜索。返回摘要列表。"""
        ctx = getattr(self.plugin, "context", None)
        if not ctx:
            return []
        try:
            # 尝试多种可能的搜索接口
            search_fn = None
            for attr in ["web_search", "search_web", "web_search_tool"]:
                fn = getattr(ctx, attr, None)
                if fn:
                    search_fn = fn
                    break
            if not search_fn:
                pm = getattr(ctx, "provider_manager", None)
                if pm:
                    search_fn = getattr(pm, "web_search", None)
            if not search_fn:
                return []
            result = await search_fn(query)
            if isinstance(result, list):
                return [str(r) for r in result[:5]]
            if isinstance(result, str):
                return [result]
            if isinstance(result, dict):
                return [json.dumps(result, ensure_ascii=False)]
        except Exception as e:
            logger.debug(f"[mingbu] 网页搜索失败: {e}")
        return []

    async def read_news(self, topic: Optional[str] = None) -> dict:
        """读取热点新闻并整理为见闻。"""
        if not self._cfg().get("news_enabled", False):
            return {"ok": False, "reason": "新闻阅读未启用"}
        query = topic or "今日热点新闻"
        results = await self._web_search(query)
        if not results:
            return {"ok": False, "reason": "未获取到新闻"}
        note = {
            "time": now_bj().isoformat(),
            "topic": query,
            "snippets": results,
        }
        # 存入 KV
        try:
            raw = await self.plugin.get_kv_data(KV_NEWS, None)
            notes = json.loads(raw) if raw else []
            notes.insert(0, note)
            notes = notes[:30]
            await self.plugin.put_kv_data(KV_NEWS, json.dumps(notes, ensure_ascii=False))
        except Exception as e:
            logger.debug(f"[mingbu] 保存新闻失败: {e}")
        return {"ok": True, "note": note}

    async def proactive_search(self, interest: str) -> dict:
        """按人格兴趣主动搜索并留下探索笔记。"""
        if not self._cfg().get("proactive_search_enabled", False):
            return {"ok": False, "reason": "主动搜索未启用"}
        results = await self._web_search(interest)
        if not results:
            return {"ok": False, "reason": "未获取到搜索结果"}
        note = {
            "time": now_bj().isoformat(),
            "interest": interest,
            "snippets": results,
        }
        try:
            raw = await self.plugin.get_kv_data(KV_SEARCH_NOTES, None)
            notes = json.loads(raw) if raw else []
            notes.insert(0, note)
            notes = notes[:50]
            await self.plugin.put_kv_data(KV_SEARCH_NOTES, json.dumps(notes, ensure_ascii=False))
        except Exception as e:
            logger.debug(f"[mingbu] 保存探索笔记失败: {e}")
        return {"ok": True, "note": note}

    async def get_recent_news(self, n: int = 5) -> List[dict]:
        try:
            raw = await self.plugin.get_kv_data(KV_NEWS, None)
            notes = json.loads(raw) if raw else []
            return notes[:n]
        except Exception:
            return []

    async def get_search_notes(self, n: int = 5) -> List[dict]:
        try:
            raw = await self.plugin.get_kv_data(KV_SEARCH_NOTES, None)
            notes = json.loads(raw) if raw else []
            return notes[:n]
        except Exception:
            return []
