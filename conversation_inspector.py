"""
conversation_inspector.py -- 对话检视与学习
============================================
维护与所有用户/群组的原始对话记录，支持：
  - 对话列表查看（按用户/群筛选）
  - 对话搜索（关键词/时间范围）
  - 对话标注与学习（标记重要对话、添加笔记）
  - 从对话中提取知识（类似黑话学习，但面向所有对话）
  - 高权重对话自动抽取并入记忆库

存储：AstrBot KV Store，按用户分桶存储最近 N 条对话。
"""
import json
import re as _re
import zlib
from collections import Counter
from typing import Dict, List, Optional, Set

from astrbot.api import logger

from .bj_time import now_bj
from .kv_cache import WriteBehindScheduler


def _stable_hash6(text: str) -> str:
    """稳定的 6 位十六进制摘要。

    此前用内置 hash()，而 CPython 对 str 的 hash 带 PYTHONHASHSEED 随机化，
    重启进程后同一条内容算出的 id 不同，导致跨重启的 id 引用全部失效。
    """
    return f"{zlib.crc32(text.encode('utf-8')) & 0xFFFFFF:06x}"


def uid_from_log_id(log_id: str) -> str:
    """从 log_id 还原 uid。

    id 形如 ``{uid}_{YYYYMMDDhhmmss}_{hash6}``。此前用 split("_")[0] 取首段，
    在 uid 自身含下划线时（如微信 wxid_xxx、Telegram tg_123）会截断成错误的
    uid，使 detail/learn/unlearn/delete 作用到别人头上。改用 rsplit 从右侧
    剥掉固定的两段，兼容历史数据。
    """
    if "_" not in log_id:
        return ""
    parts = log_id.rsplit("_", 2)
    return parts[0] if len(parts) == 3 else ""

KV_CONVERSATION = "mingbu.conversation"
KV_WEIGHT_INDEX = "mingbu.conversation_weight"  # 权重索引：按用户记录高权重对话
# 已学知识：每用户分桶（mingbu.learned.<uid>），避免全局 500 条上限跨用户互相挤占
LEARNED_PER_USER_MAX = 200
MAX_LOGS_PER_USER = 5000  # 每用户最多保存 5000 条对话

# 高权重关键词（情感、亲密、计划、自我披露等）
EMOTION_KEYWORDS = {
    "喜欢": 0.3, "讨厌": 0.3, "爱": 0.4, "想": 0.2, "梦": 0.3,
    "怕": 0.3, "恨": 0.4, "希望": 0.25, "绝望": 0.4, "开心": 0.25,
    "难过": 0.3, "生气": 0.3, "后悔": 0.3, "遗憾": 0.3, "想念": 0.35,
    "感动": 0.35, "温暖": 0.3, "孤独": 0.3, "失落": 0.3, "焦虑": 0.3,
    "压力": 0.25, "担心": 0.25, "害怕": 0.3, "紧张": 0.2, "尴尬": 0.2,
    "兴奋": 0.3, "满足": 0.25, "幸福": 0.35, "痛苦": 0.4, "崩溃": 0.45,
    "暴哭": 0.4, "暴怒": 0.4,
}

INTIMACY_KEYWORDS = {
    "我": 0.05, "你": 0.05, "我们": 0.15, "一起": 0.2, "陪": 0.25,
    "爱": 0.3, "宝贝": 0.4, "亲": 0.3, "亲爱的": 0.4, "想你": 0.3,
    "抱": 0.25, "拥": 0.2, "结婚": 0.5, "交往": 0.4, "恋爱": 0.4,
    "喜欢": 0.2, "爱慕": 0.4, "暗恋": 0.4, "表白": 0.4, "约会": 0.3,
    "老公": 0.45, "老婆": 0.45, "宝宝": 0.4, "崽": 0.3, "家人": 0.3,
    "爸妈": 0.25, "朋友": 0.15, "闺蜜": 0.25, "兄弟": 0.25,
}

SELF_DISCLOSURE_KEYWORDS = {
    "我": 0.1, "我的": 0.1, "我觉得": 0.2, "我认为": 0.2, "我喜欢": 0.3,
    "我讨厌": 0.3, "我希望": 0.25, "我害怕": 0.3, "我一直": 0.2,
    "从小": 0.3, "曾经": 0.2, "过去": 0.15, "秘密": 0.4, "没人知道": 0.4,
    "只有": 0.2, "自己": 0.1,
    "梦想": 0.35, "目标": 0.25, "计划": 0.2, "打算": 0.2, "准备": 0.2,
    "决定": 0.25, "想法": 0.2,
}

IMPORTANT_TOPIC_KEYWORDS = {
    "生日": 0.35, "纪念日": 0.4, "死亡": 0.4, "去世": 0.4, "生病": 0.3,
    "医院": 0.25, "考试": 0.3, "毕业": 0.35, "工作": 0.2, "辞职": 0.3,
    "离婚": 0.4, "分手": 0.4, "失恋": 0.4, "怀孕": 0.4, "生子": 0.4,
    "买房": 0.35, "买车": 0.3, "搬家": 0.25, "出国": 0.35, "留学": 0.35,
    "升职": 0.3, "加薪": 0.3, "失业": 0.35, "面试": 0.25,
    "婚礼": 0.4, "求婚": 0.4, "订婚": 0.4,
}

NEGATIVE_PATTERNS = [
    (r"(.+?)\1{3,}", 0.2),  # 重复
    (r"^[a-zA-Z0-9]+$", -0.3),  # 纯英文/数字
    (r"^[\s]*$", -0.5),  # 空白
    (r"^(哈|啊|呵|嘿){3,}", -0.2),  # 简单笑声
]

HIGH_WEIGHT_THRESHOLD = 0.45  # 权重阈值，超过则视为高权重对话
WEIGHT_INDEX_MAX = 200  # 每用户最多保存 200 条高权重对话索引


class ConversationLog:
    """单条对话记录。"""

    def __init__(self, uid: str, content: str, msg_type: str = "user",
                 gid: str = "", platform: str = "", session_id: str = "",
                 is_group: bool = False, sender_name: str = ""):
        self.id = (
            f"{uid}_{now_bj().strftime('%Y%m%d%H%M%S')}"
            f"_{_stable_hash6(content)}"
        )
        self.uid = uid
        self.gid = gid
        self.platform = platform
        self.msg_type = msg_type  # "user" | "bot"
        self.content = content
        self.session_id = session_id
        self.is_group = is_group
        self.sender_name = sender_name
        self.timestamp = now_bj().isoformat()
        self.learned = False
        self.notes = ""
        self.tags = []
        self.importance = 0.0  # 0-1，由学习系统打分
        self.weight = 0.0  # 自动权重评分
        self.weight_factors = {}  # 权重因素详情

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "uid": self.uid,
            "gid": self.gid,
            "platform": self.platform,
            "msg_type": self.msg_type,
            "content": self.content,
            "session_id": self.session_id,
            "is_group": self.is_group,
            "sender_name": self.sender_name,
            "timestamp": self.timestamp,
            "learned": self.learned,
            "notes": self.notes,
            "tags": self.tags,
            "importance": self.importance,
            "weight": self.weight,
            "weight_factors": self.weight_factors,
        }

    _FIELDS = {
        "id": "", "uid": "", "gid": "", "platform": "", "msg_type": "user",
        "content": "", "session_id": "", "is_group": False, "sender_name": "",
        "learned": False, "notes": "", "tags": list, "importance": 0.0,
        "weight": 0.0, "weight_factors": dict,
    }

    @classmethod
    def from_dict(cls, d: dict) -> "ConversationLog":
        obj = cls.__new__(cls)
        now = now_bj().isoformat()
        for field, default in cls._FIELDS.items():
            val = d.get(field)
            if val is None:
                val = [] if default is list else {} if default is dict else default
            setattr(obj, field, val)
        obj.timestamp = d.get("timestamp", now)
        return obj

    @staticmethod
    def _score_keywords(content: str, kw_dict: dict, max_hits: int = 3) -> tuple:
        """通用关键词打分。返回 (总分, [(命中词, 次数), ...])。"""
        total = 0.0
        hits = []
        for kw, score in kw_dict.items():
            count = content.count(kw)
            if count > 0:
                total += score * min(count, max_hits)
                hits.append((kw, count))
        return total, hits

    def calculate_weight(self) -> float:
        """计算对话权重。综合考虑情感、亲密、自我披露、重要话题、长度等。"""
        if not self.content:
            return 0.0

        content = self.content
        factors = {}

        emotion_score, emotion_hits = self._score_keywords(content, EMOTION_KEYWORDS)
        factors["emotion"] = round(emotion_score, 3)
        factors["emotion_hits"] = emotion_hits

        intimacy_score, intimacy_hits = self._score_keywords(content, INTIMACY_KEYWORDS)
        factors["intimacy"] = round(intimacy_score, 3)
        factors["intimacy_hits"] = intimacy_hits

        self_score, self_hits = self._score_keywords(content, SELF_DISCLOSURE_KEYWORDS)
        factors["self_disclosure"] = round(self_score, 3)
        factors["self_hits"] = self_hits

        topic_score, topic_hits = self._score_keywords(content, IMPORTANT_TOPIC_KEYWORDS, max_hits=2)
        factors["important_topic"] = round(topic_score, 3)
        factors["topic_hits"] = topic_hits

        # 5. 长度奖励（中等长度最佳）
        length = len(content)
        if length < 5:
            length_score = -0.3
        elif length < 15:
            length_score = 0.05
        elif length < 60:
            length_score = 0.15
        elif length < 200:
            length_score = 0.1
        else:
            length_score = 0.0  # 过长反而权重低
        factors["length"] = round(length_score, 3)
        factors["char_count"] = length

        # 6. 问号/感叹号密度（表达强度）
        expressive = content.count("?") + content.count("？") \
            + content.count("!") + content.count("！")
        expressive_score = min(0.2, expressive * 0.04)
        factors["expressive"] = round(expressive_score, 3)

        # 7. 负面模式惩罚
        penalty = 0.0
        for pattern, score in NEGATIVE_PATTERNS:
            if _re.search(pattern, content):
                penalty += score
        factors["penalty"] = round(penalty, 3)

        # 8. 私聊加成（私聊比群聊更亲密）
        if not self.is_group:
            factors["private_bonus"] = 0.1
        else:
            factors["private_bonus"] = 0.0

        # 9. Bot 回复加分（说明这是有意义的对话回合）
        if self.msg_type == "bot" and length > 20:
            factors["bot_response"] = 0.05
        else:
            factors["bot_response"] = 0.0

        # 综合权重
        total = (
            emotion_score
            + intimacy_score
            + self_score
            + topic_score
            + length_score
            + expressive_score
            + penalty
            + factors["private_bonus"]
            + factors["bot_response"]
        )
        total = max(0.0, min(1.0, total))

        self.weight = round(total, 3)
        self.weight_factors = factors
        return self.weight


class ConversationInspector:
    """对话检视与学习管理器。

    存储策略：每用户一个 KV 桶 + 进程内缓存 + 写回防抖。
    此前每条消息都要「全量反序列化 N 条日志 → append → 全量序列化 N 条」，
    N 最大 5000，一次问答触发两轮，是全插件最重的热点。现改为缓存命中读、
    脏标记写、防抖合并落盘。
    """

    MAX_CACHE_USERS = 200
    FLUSH_DEBOUNCE_SEC = 3.0
    MAX_EXTRACTED_IDS = 5000

    def __init__(self, plugin):
        self.plugin = plugin
        # 高权重判定阈值。必须是**实例字段**：配置项 weight_threshold 与面板
        # 的热重载都要能改到它。此前各处只读模块常量 HIGH_WEIGHT_THRESHOLD，
        # 于是 core/features 里那句 `inspector.weight_threshold = cfg[...]`
        # 只是挂了个没人读的属性——该配置项从未真正生效过。
        self.weight_threshold: float = HIGH_WEIGHT_THRESHOLD
        self._memory_extracted_ids: Set[str] = set()  # 已抽取到记忆库的对话ID
        self._cache: Dict[str, List[ConversationLog]] = {}
        self._weight_cache: Dict[str, List[dict]] = {}
        self._dirty: Set[str] = set()
        self._weight_dirty: Set[str] = set()
        self._scheduler = WriteBehindScheduler(
            self.flush, lambda: self.FLUSH_DEBOUNCE_SEC
        )

    def _kv_key(self, uid: str) -> str:
        return f"{KV_CONVERSATION}:{uid}"

    def _weight_key(self, uid: str) -> str:
        return f"{KV_WEIGHT_INDEX}:{uid}"

    def _learned_key(self, uid: str) -> str:
        return f"mingbu.learned.{uid}"

    # ---- 缓存与落盘 ----

    def _evict_if_needed(self):
        if len(self._cache) <= self.MAX_CACHE_USERS:
            return
        for uid in list(self._cache.keys()):
            if len(self._cache) <= self.MAX_CACHE_USERS:
                break
            if uid in self._dirty or uid in self._weight_dirty:
                continue
            self._cache.pop(uid, None)
            self._weight_cache.pop(uid, None)

    async def _load(self, uid: str) -> List[ConversationLog]:
        """加载某用户的对话记录（命中缓存则零 IO）。"""
        cached = self._cache.get(uid)
        if cached is not None:
            return cached
        logs: List[ConversationLog] = []
        raw = await self.plugin.get_kv_data(self._kv_key(uid), None)
        if raw:
            try:
                data = json.loads(raw) if isinstance(raw, str) else raw
                logs = [ConversationLog.from_dict(d) for d in data]
            except Exception as e:
                logger.warning(f"[mingbu.conv] 对话记录解析失败，按空处理: {e}")
        self._cache[uid] = logs
        self._evict_if_needed()
        return logs

    def _mark_dirty(self, uid: str, weight_index: bool = False):
        if weight_index:
            self._weight_dirty.add(uid)
        else:
            self._dirty.add(uid)
        # 防抖调度交给 WriteBehindScheduler（机械逻辑去重，见 kv_cache.py）
        self._scheduler.schedule(uid)

    async def flush(self, uid: str):
        """强制落盘某用户的对话记录与权重索引。"""
        if uid in self._dirty:
            self._dirty.discard(uid)
            logs = self._cache.get(uid)
            # 缓存条目缺失说明没有可落盘的内容，绝不能用空列表覆盖已有数据
            if logs is None:
                logger.warning(f"[mingbu.conv] {uid} 对话缓存缺失，跳过落盘以免清空数据")
            else:
                if len(logs) > MAX_LOGS_PER_USER:
                    logs = logs[-MAX_LOGS_PER_USER:]
                    self._cache[uid] = logs
                try:
                    await self.plugin.put_kv_data(
                        self._kv_key(uid),
                        json.dumps([x.to_dict() for x in logs], ensure_ascii=False),
                    )
                except Exception as e:
                    logger.warning(f"[mingbu.conv] 保存对话记录失败: {e}")
                    self._dirty.add(uid)
        if uid in self._weight_dirty:
            self._weight_dirty.discard(uid)
            items = self._weight_cache.get(uid)
            if items is None:
                logger.warning(f"[mingbu.conv] {uid} 权重缓存缺失，跳过落盘")
                return
            if len(items) > WEIGHT_INDEX_MAX:
                items.sort(key=lambda x: x.get("weight", 0), reverse=True)
                items = items[:WEIGHT_INDEX_MAX]
                self._weight_cache[uid] = items
            try:
                await self.plugin.put_kv_data(
                    self._weight_key(uid),
                    json.dumps(items, ensure_ascii=False),
                )
            except Exception as e:
                logger.warning(f"[mingbu.conv] 保存权重索引失败: {e}")
                self._weight_dirty.add(uid)

    async def flush_all(self):
        """落盘全部脏用户（插件 terminate 前必调）。"""
        self._scheduler.cancel_all()
        for uid in set(self._dirty) | set(self._weight_dirty):
            await self.flush(uid)

    async def _save(self, uid: str, logs: List[ConversationLog]):
        """兼容旧调用：更新缓存并标记脏。"""
        self._cache[uid] = logs
        self._mark_dirty(uid)

    async def _load_weight_index(self, uid: str) -> List[dict]:
        """加载某用户的高权重对话索引（命中缓存则零 IO）。"""
        cached = self._weight_cache.get(uid)
        if cached is not None:
            return cached
        items: List[dict] = []
        try:
            raw = await self.plugin.get_kv_data(self._weight_key(uid), None)
            if raw:
                loaded = json.loads(raw) if isinstance(raw, str) else raw
                if isinstance(loaded, list):
                    items = loaded
        except Exception as e:
            logger.warning(f"[mingbu.conv] 权重索引解析失败，按空处理: {e}")
        self._weight_cache[uid] = items
        # 索引已落盘（extracted_to_memory 标志）：按需重建「已抽取」内存集合，
        # 使重启后不会因该集合归零而重复把同一对话再抽入记忆库。
        for it in items:
            if it.get("extracted_to_memory") and it.get("id"):
                self._memory_extracted_ids.add(it["id"])
        return items

    async def _save_weight_index(self, uid: str, items: List[dict]):
        """更新权重索引缓存并标记脏（真正写入在 flush）。"""
        self._weight_cache[uid] = items
        self._mark_dirty(uid, weight_index=True)

    async def _update_weight_index(self, log: ConversationLog):
        """更新单条对话到权重索引（如果权重足够高）。"""
        if log.weight < self.weight_threshold:
            return
        index = await self._load_weight_index(log.uid)
        # 检查是否已存在
        existing_idx = next((i for i, x in enumerate(index) if x.get("id") == log.id), -1)
        entry = {
            "id": log.id,
            "content": log.content[:200],
            "weight": log.weight,
            "weight_factors": log.weight_factors,
            "msg_type": log.msg_type,
            "timestamp": log.timestamp,
            "is_group": log.is_group,
            "extracted_to_memory": log.id in self._memory_extracted_ids,
        }
        if existing_idx >= 0:
            index[existing_idx] = entry
        else:
            index.append(entry)
        await self._save_weight_index(log.uid, index)

    async def log(self, uid: str, content: str, msg_type: str = "user",
                  gid: str = "", platform: str = "", session_id: str = "",
                  is_group: bool = False, sender_name: str = ""):
        """记录一条对话。"""
        if not content or len(content.strip()) < 1:
            return
        logs = await self._load(uid)
        log = ConversationLog(
            uid=uid, content=content, msg_type=msg_type,
            gid=gid, platform=platform, session_id=session_id,
            is_group=is_group, sender_name=sender_name
        )
        # 自动计算权重
        log.calculate_weight()
        logs.append(log)
        await self._save(uid, logs)

        # 更新权重索引
        await self._update_weight_index(log)

        # 如果是用户消息且权重大于阈值，自动入记忆
        if log.msg_type == "user" and log.weight >= self.weight_threshold:
            await self._auto_extract_to_memory(log)

    async def log_bot_reply(self, uid: str, content: str, session_id: str = "",
                            gid: str = "", is_group: bool = False):
        """记录 Bot 的回复。"""
        await self.log(
            uid=uid, content=content, msg_type="bot",
            session_id=session_id, gid=gid, is_group=is_group
        )

    async def list_conversations(self, uid: Optional[str] = None,
                                 gid: Optional[str] = None,
                                 limit: int = 50,
                                 offset: int = 0,
                                 search: str = "",
                                 msg_type: Optional[str] = None,
                                 learned_only: bool = False) -> dict:
        """列出对话记录，支持筛选和搜索。"""
        from .core import KV_USERS

        target_uids = []
        if uid:
            target_uids = [uid]
        else:
            # 未指定 uid 时限制搜索范围（避免全量加载）
            raw_users = await self.plugin.get_kv_data(KV_USERS, None)
            all_uids = json.loads(raw_users) if raw_users else []
            target_uids = all_uids[:100]  # 最多查 100 个用户

        search_lower = search.lower() if search else ""

        all_logs: List[ConversationLog] = []
        for target_uid in target_uids:
            logs = await self._load(target_uid)
            for item in logs:
                if gid and item.gid != gid:
                    continue
                if msg_type and item.msg_type != msg_type:
                    continue
                if learned_only and not item.learned:
                    continue
                if search_lower and search_lower not in (item.content or "").lower():
                    continue
                all_logs.append(item)

        # 按时间倒序
        all_logs.sort(key=lambda x: x.timestamp, reverse=True)
        total = len(all_logs)
        page = all_logs[offset:offset + limit]

        return {
            "total": total,
            "offset": offset,
            "limit": limit,
            "items": [x.to_dict() for x in page],
        }

    async def get_conversation_detail(self, log_id: str) -> Optional[dict]:
        """获取单条对话详情。"""
        # log_id 格式: uid_YYYYMMDDhhmmss_hash
        uid = uid_from_log_id(log_id)
        if not uid:
            return None
        logs = await self._load(uid)
        for log in logs:
            if log.id == log_id:
                return log.to_dict()
        return None

    async def learn_from_conversation(self, log_id: str, notes: str = "",
                                      tags: Optional[List[str]] = None,
                                      importance: float = 0.5) -> bool:
        """标注学习一条对话。"""
        uid = uid_from_log_id(log_id)
        if not uid:
            return False
        logs = await self._load(uid)
        for log in logs:
            if log.id == log_id:
                log.learned = True
                log.notes = notes
                log.tags = tags or []
                log.importance = max(0.0, min(1.0, importance))
                log.timestamp = now_bj().isoformat()
                await self._save(uid, logs)

                # 同时保存到学习知识库
                await self._save_learned_knowledge(log)
                return True
        return False

    async def _save_learned_knowledge(self, log: ConversationLog):
        """将学习到的内容保存到「每用户分桶」知识库（KV 键 mingbu.learned.<uid>）。"""
        key = self._learned_key(log.uid)
        try:
            raw = await self.plugin.get_kv_data(key, None)
            knowledge = json.loads(raw) if raw else []
            knowledge.append({
                "id": log.id,
                "uid": log.uid,
                "content": log.content,
                "notes": log.notes,
                "tags": log.tags,
                "importance": log.importance,
                "timestamp": now_bj().isoformat(),
            })
            # 每用户独立上限，互不挤占
            if len(knowledge) > LEARNED_PER_USER_MAX:
                knowledge = knowledge[-LEARNED_PER_USER_MAX:]
            await self.plugin.put_kv_data(
                key, json.dumps(knowledge, ensure_ascii=False)
            )
        except Exception as e:
            logger.debug(f"[mingbu.conv] 保存学习知识失败: {e}")

    async def _remove_learned(self, uid: str, log_id: str):
        """从对应用户的已学知识分桶中移除一条。"""
        try:
            key = self._learned_key(uid)
            raw = await self.plugin.get_kv_data(key, None)
            if not raw:
                return
            knowledge = json.loads(raw) or []
            new_k = [k for k in knowledge if k.get("id") != log_id]
            if len(new_k) != len(knowledge):
                await self.plugin.put_kv_data(
                    key, json.dumps(new_k, ensure_ascii=False)
                )
        except Exception as e:
            logger.debug(f"[mingbu.conv] 移除学习知识失败: {e}")

    async def get_learned_knowledge(self, uid: Optional[str] = None,
                                    limit: int = 50) -> List[dict]:
        """获取已学习的知识。

        传入 ``uid`` 取该用户分桶；省略 ``uid`` 则聚合全部用户分桶（经 KV_USERS 枚举）。
        """
        try:
            if uid:
                raw = await self.plugin.get_kv_data(self._learned_key(uid), None)
                knowledge = json.loads(raw) if raw else []
            else:
                from .core import KV_USERS
                raw_users = await self.plugin.get_kv_data(KV_USERS, None)
                all_uids = json.loads(raw_users) if raw_users else []
                knowledge = []
                for u in all_uids:
                    raw = await self.plugin.get_kv_data(self._learned_key(u), None)
                    if raw:
                        knowledge.extend(json.loads(raw) or [])
            knowledge.sort(key=lambda x: x.get("timestamp", ""), reverse=True)
            return knowledge[:limit]
        except Exception as e:
            logger.debug(f"[mingbu.conv] 读取学习知识失败: {e}")
            return []

    async def unlearn(self, log_id: str) -> bool:
        """取消学习标注，并同步从已学知识分桶移除。"""
        uid = uid_from_log_id(log_id)
        if not uid:
            return False
        logs = await self._load(uid)
        target = next((x for x in logs if x.id == log_id), None)
        if target is None:
            return False
        target.learned = False
        target.notes = ""
        target.tags = []
        target.importance = 0.0
        await self._save(uid, logs)
        await self._remove_learned(uid, log_id)
        return True

    async def delete_conversation(self, log_id: str) -> bool:
        """删除一条对话记录。"""
        uid = uid_from_log_id(log_id)
        if not uid:
            return False
        logs = await self._load(uid)
        new_logs = [x for x in logs if x.id != log_id]
        if len(new_logs) != len(logs):
            await self._save(uid, new_logs)
            return True
        return False

    async def get_stats(self, max_users: int = 50) -> dict:
        """获取对话统计（最多统计 max_users 个活跃用户）。"""
        from .core import KV_USERS
        raw_users = await self.plugin.get_kv_data(KV_USERS, None)
        all_uids = (json.loads(raw_users) if raw_users else [])[:max_users]

        total_logs = total_learned = total_weighted = total_extracted = 0
        user_stats = []

        for uid in all_uids:
            logs = await self._load(uid)
            weight_index = await self._load_weight_index(uid)
            n = len(logs)
            if n == 0:
                continue
            learned = sum(1 for x in logs if x.learned)
            weighted = len(weight_index)
            extracted = sum(1 for x in weight_index if x.get("extracted_to_memory"))
            total_logs += n
            total_learned += learned
            total_weighted += weighted
            total_extracted += extracted
            user_stats.append({
                "uid": uid,
                "total": n,
                "learned": learned,
                "weighted": weighted,
                "extracted": extracted,
                "last_interaction": logs[-1].timestamp,
            })

        user_stats.sort(key=lambda x: x["total"], reverse=True)
        return {
            "total_users": len(all_uids),
            "total_logs": total_logs,
            "total_learned": total_learned,
            "total_weighted": total_weighted,
            "total_extracted": total_extracted,
            "user_stats": user_stats[:20],
        }

    async def get_weighted_conversations(self, uid: str,
                                         min_weight: Optional[float] = None,
                                         limit: int = 50) -> List[dict]:
        """获取某用户的高权重对话。

        ``min_weight`` 省略时取实例阈值（受配置项 weight_threshold 控制），
        而不是写死的模块常量——否则面板调高阈值后，列表仍按 0.45 过滤。
        """
        if min_weight is None:
            min_weight = self.weight_threshold
        index = await self._load_weight_index(uid)
        # 筛选并按权重降序
        filtered = [x for x in index if x.get("weight", 0) >= min_weight]
        filtered.sort(key=lambda x: x.get("weight", 0), reverse=True)
        return filtered[:limit]

    async def get_pending_extractions(self, uid: str, limit: int = 20) -> List[dict]:
        """获取尚未抽取到记忆库的高权重对话。"""
        index = await self._load_weight_index(uid)
        pending = [x for x in index if not x.get("extracted_to_memory")]
        pending.sort(key=lambda x: x.get("weight", 0), reverse=True)
        return pending[:limit]

    async def mark_extracted(self, log_id: str) -> bool:
        """标记一条对话已抽取到记忆库。"""
        uid = uid_from_log_id(log_id)
        if not uid:
            return False
        index = await self._load_weight_index(uid)
        for entry in index:
            if entry.get("id") == log_id:
                entry["extracted_to_memory"] = True
                self._memory_extracted_ids.add(log_id)
                await self._save_weight_index(uid, index)
                return True
        return False

    async def _auto_extract_to_memory(self, log: ConversationLog):
        """将高权重对话自动入记忆库（调用 memory_core）。"""
        try:
            from .memory_core import EMOTIONAL, EPISODIC, SEMANTIC
            # 优先使用 plugin.memory（如果存在）
            memory = getattr(self.plugin, "memory", None) or getattr(self.plugin, "mem", None)
            if memory is None:
                return
            # 根据权重因素决定记忆类型
            factors = log.weight_factors or {}
            if factors.get("emotion", 0) >= 0.3:
                mtype = EMOTIONAL
                emo = min(1.0, factors.get("emotion", 0))
            elif factors.get("self_disclosure", 0) >= 0.2 or factors.get("important_topic", 0) >= 0.2:
                mtype = SEMANTIC
                emo = 0.0
            else:
                mtype = EPISODIC
                emo = 0.0
            importance = max(0.5, log.weight)
            tags = []
            if log.is_group:
                tags.append("群聊")
            else:
                tags.append("私聊")
            if factors.get("intimacy", 0) > 0.3:
                tags.append("亲密对话")
            if factors.get("important_topic", 0) > 0.2:
                tags.append("重要话题")
            if factors.get("emotion", 0) > 0.3:
                tags.append("情绪强烈")
            await memory.add(
                user_id=log.uid, mtype=mtype, content=log.content,
                source="conversation_inspector", importance=importance,
                emotional_weight=emo, tags=tags,
            )
            self._memory_extracted_ids.add(log.id)
            await self.mark_extracted(log.id)
        except Exception as e:
            logger.debug(f"[mingbu.conv] 自动入记忆失败(忽略): {e}")

    async def extract_high_weight_to_memory(self, uid: str, top_n: int = 5) -> int:
        """批量将某用户尚未抽取的高权重对话入记忆库。"""
        pending = await self.get_pending_extractions(uid, limit=top_n)
        if not pending:
            return 0
        # 一次加载 + 建索引，避免循环里反复线性扫描整份日志（原 N+1）
        logs = await self._load(uid)
        by_id = {x.id: x for x in logs}
        count = 0
        for entry in pending:
            log = by_id.get(entry.get("id", ""))
            if log:
                await self._auto_extract_to_memory(log)
                count += 1
        return count

    async def reweight_all(self, uid: str) -> int:
        """重新计算某用户所有对话的权重。"""
        # 先加载权重索引，使 _memory_extracted_ids 含已落盘的抽取标记，
        # 重建索引时才能正确保留已抽取对话的 extracted_to_memory 状态。
        await self._load_weight_index(uid)
        logs = await self._load(uid)
        count = 0
        for log in logs:
            log.calculate_weight()
            count += 1
        await self._save(uid, logs)
        # 重建权重索引
        new_index = []
        for log in logs:
            if log.weight >= self.weight_threshold:
                new_index.append({
                    "id": log.id,
                    "content": log.content[:200],
                    "weight": log.weight,
                    "weight_factors": log.weight_factors,
                    "msg_type": log.msg_type,
                    "timestamp": log.timestamp,
                    "is_group": log.is_group,
                    "extracted_to_memory": log.id in self._memory_extracted_ids,
                })
        await self._save_weight_index(uid, new_index)
        return count

    async def get_user_conversation_summary(self, uid: str) -> dict:
        """获取某用户的对话摘要。"""
        logs = await self._load(uid)
        if not logs:
            return {"uid": uid, "total": 0, "learned": 0, "recent_topics": []}

        # 提取近期话题（简单关键词提取）
        recent_contents = [x.content for x in logs[-20:] if x.msg_type == "user"]
        topics = self._extract_topics("\n".join(recent_contents))

        weight_index = await self._load_weight_index(uid)
        high_weight_count = len(weight_index)
        extracted_count = sum(1 for x in weight_index if x.get("extracted_to_memory"))

        return {
            "uid": uid,
            "total": len(logs),
            "learned": sum(1 for x in logs if x.learned),
            "user_msgs": sum(1 for x in logs if x.msg_type == "user"),
            "bot_msgs": sum(1 for x in logs if x.msg_type == "bot"),
            "last_interaction": logs[-1].timestamp if logs else "",
            "recent_topics": topics[:10],
            "high_weight_count": high_weight_count,
            "extracted_count": extracted_count,
        }

    def _extract_topics(self, text: str) -> List[str]:
        """简单话题提取：找出高频词组。"""
        words = _re.findall(r"[\u4e00-\u9fa5]{2,4}", text)
        counter = Counter(words)
        stopwords = {"什么", "怎么", "这个", "那个", "就是", "还是", "今天", "现在", "没有", "可以"}
        return [w for w, c in counter.most_common(20) if w not in stopwords and c >= 2]
