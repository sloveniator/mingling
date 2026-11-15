"""
group_slang.py -- 群黑话学习与管理
==================================
维护群内学到的词、释义、用法、置信度和注入状态。
"""
import re as _re
from typing import Dict, List, Optional

from .bj_time import now_bj
from .kv_store import KVStoreBase

KV_SLANG = "mingbu.group_slang"

# 候选黑话提取：引号/括号包裹的词，或中英数字混合的短串（2-10 位）
_BRACKET_RE = _re.compile(r"[「『【\[(\"']([^「』】\])\"']{2,20}?)[」』】\])\"']")
_SLANG_RE = _re.compile(r"(?<![a-zA-Z0-9])[a-zA-Z0-9]{2,10}(?![a-zA-Z0-9])")


class GroupSlangManager(KVStoreBase):
    def __init__(self, plugin):
        super().__init__(plugin, KV_SLANG)

    async def _load(self) -> Dict[str, dict]:
        return await self._load_json(self.prefix, default={})

    async def _save(self, data: Dict[str, dict]):
        await self._save_json(self.prefix, data)

    async def _threshold(self) -> float:
        """黑话置信度阈值（配置 slang_confidence_threshold，非法回退 0.5）。"""
        try:
            v = float((getattr(self.plugin, "_config", {}) or {})
                      .get("slang_confidence_threshold", 0.5))
            return max(0.0, min(1.0, v))
        except Exception:
            return 0.5

    async def learn(self, gid: str, word: str, context: str = "",
                    confidence: Optional[float] = None):
        """从群消息中学习一个新词。confidence 缺省取配置阈值。"""
        if len(word) < 2 or len(word) > 20:
            return
        if confidence is None:
            confidence = await self._threshold()
        data = await self._load()
        key = f"{gid}:{word}"
        existing = data.get(key)
        if existing:
            existing["seen"] += 1
            existing["confidence"] = min(1.0, existing["confidence"] + 0.05)
            existing["updated_at"] = now_bj().isoformat()
        else:
            data[key] = {
                "gid": gid,
                "word": word,
                "meaning": "",
                "usage": context[:200],
                "confidence": confidence,
                "seen": 1,
                "injected": False,
                "manual": False,
                "created_at": now_bj().isoformat(),
                "updated_at": now_bj().isoformat(),
            }
        await self._save(data)

    async def learn_candidates(self, gid: str, text: str):
        """从群消息中自动提取候选黑话并尝试学习。

        初始置信度以配置阈值为基准：括号包裹内容至少达阈值，
        字母/数字短串取阈值一半（保留「更可信」的区分度）。
        """
        if not gid or not text or len(text) < 2:
            return
        threshold = await self._threshold()
        seen = set()
        # 引号/括号包裹的内容置信更高
        for m in _BRACKET_RE.finditer(text):
            word = m.group(1).strip()
            if word and word not in seen and 2 <= len(word) <= 20:
                seen.add(word)
                await self.learn(gid, word, context=text[:200],
                                 confidence=max(0.45, threshold))
        # 字母/数字短串（如 yyds、2333、xswl）
        for m in _SLANG_RE.finditer(text):
            word = m.group(0).strip()
            if word and word not in seen and not word.isdigit():
                seen.add(word)
                await self.learn(gid, word, context=text[:200],
                                 confidence=max(0.25, threshold * 0.5))

    async def list_slang(self, gid: Optional[str] = None) -> List[dict]:
        """列出群黑话。"""
        data = await self._load()
        items = list(data.values())
        if gid:
            items = [i for i in items if i["gid"] == gid]
        items.sort(key=lambda x: x["confidence"], reverse=True)
        return items

    async def update(self, key: str, meaning: str, injected: bool = False) -> bool:
        """手动校正或设置注入状态。"""
        data = await self._load()
        item = data.get(key)
        if not item:
            return False
        if meaning:
            item["meaning"] = meaning
            item["manual"] = True
        item["injected"] = injected
        item["updated_at"] = now_bj().isoformat()
        await self._save(data)
        return True

    async def delete(self, key: str) -> bool:
        """删除一个黑话条目。"""
        data = await self._load()
        if key not in data:
            return False
        del data[key]
        await self._save(data)
        return True

    async def get_injected_words(self, gid: str) -> List[str]:
        """获取要注入群聊上下文的黑话词列表。"""
        data = await self._load()
        words = []
        for item in data.values():
            if item["gid"] == gid and item["injected"] and item["meaning"]:
                words.append(f"{item['word']}：{item['meaning']}")
        return words
