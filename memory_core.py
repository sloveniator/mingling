"""
memory_core.py -- 记忆系统（拟人伴侣的核心）
=========================================
参考自：iris_memory / livingmemory / angel_memory 的记忆生命周期模式
  - 写入(strength/importance) → 检索(近期/重要) → 衰减(decay) → 巩固(consolidate)

记忆类型：
  EPISODIC  情景记忆（对话片段、发生的事）
  SEMANTIC  语义记忆（关于用户的事实、偏好、约定）
  EMOTIONAL 情绪记忆（情绪快照、感受）
  DREAM     梦境记忆（夜间巩固生成的意象叙事）
  GROWTH    成长记忆（里程碑、自我觉察）
  THOUGHT   想法记忆（bot 自己的念头、灵感）

存储：AstrBot KV Store（JSON 序列化），按 user_id 分桶。
"""
from __future__ import annotations

import json
import re
import time
import uuid
from datetime import datetime
from typing import Dict, List, Optional, Set, Tuple

from astrbot.api import logger

from .bj_time import now_bj
from .kv_cache import WriteBehindScheduler

# 记忆统计（memory.stats）的缓存时长：管理面板 api_stats 会对每个用户各调一次
# stats（各自一次 KV 读 + 全量解析），高并发刷新下压力显著。统计用于展示，
# 30s 内的陈旧完全可接受；写入经 _mark_dirty 失效，保证活跃用户永远拿到最新值。
_STATS_TTL = 30.0

# 记忆类型
EPISODIC = "episodic"
SEMANTIC = "semantic"
EMOTIONAL = "emotional"
DREAM = "dream"
GROWTH = "growth"

# 去重模糊相似度门槛：归一化内容 >= 此长度才启用 bigram 模糊判重。
# 短内容（如「用户0的记忆0」这类同模板不同数字的高区分度合成短句）仍只走
# 精确指纹判重，不牺牲区分度；长内容才做模糊合并防同一事实反复膨胀。
_DEDUP_FUZZY_MIN_LEN = 12
THOUGHT = "thought"

ALL_TYPES = [EPISODIC, SEMANTIC, EMOTIONAL, DREAM, GROWTH, THOUGHT]

# 记忆可见范围（scope）-- 2.0 群聊/私聊隔离（方案 B：加字段 + 读取过滤，不改桶键）
#   PRIVATE  私密记忆：私聊中说的话、亲密/情绪内容、梦境成长等后台私密产物。
#            仅私聊上下文可注入，绝不在群里复述（防跨上下文泄漏）。
#   GROUP    群聊可分享记忆：在群聊里公开说过、适合在群里被召回的内容。
SCOPE_PRIVATE = "private"
SCOPE_GROUP = "group"
ALL_SCOPES = [SCOPE_PRIVATE, SCOPE_GROUP]

# 老数据（KV 里无 scope 键）保守回退为 private：宁可过滤掉一条本可在群分享的
# 记忆，也不能把私聊秘密注入群聊。故存量记忆升级后默认不出现在群聊注入里。
DEFAULT_SCOPE = SCOPE_PRIVATE

# ---- 记忆元数据（2.0 第二阶段：来源可信度 / 确认状态 / 生命周期）----
# confidence：这条记忆的可信度 0~1。用户明确表达=高；模型推测=低。
#   守卫「模型幻觉写入」：推测性内容默认低 confidence + verified=False，
#   不得等同于用户明确表达的事实（对应方案 4.1 / 10.2 第三条）。
CONFIDENCE_USER_STATED = 0.95   # 用户明确表达
CONFIDENCE_INFERRED = 0.4       # 模型推测（默认，未确认）
DEFAULT_CONFIDENCE = CONFIDENCE_INFERRED
# expires_at：过期时间戳（now_bj().isoformat()），None=不过期。
# 用于情境记忆按时间衰减归档（对应方案 4.1 L3 情境记忆）。

# 提及策略（mention_policy）：控制记忆被召回后如何进入主 LLM 上下文。
# 见 Memory._default_mention_policy 的四档语义说明。
MENTION_DIRECT = "direct"
MENTION_SOFT_ECHO = "soft_echo"
MENTION_TONE_ONLY = "tone_only"
MENTION_AVOID = "avoid_unless_asked"
ALL_MENTION_POLICIES = [
    MENTION_DIRECT, MENTION_SOFT_ECHO, MENTION_TONE_ONLY, MENTION_AVOID,
]

# 记忆类型 → 默认提及策略（负向情绪记忆在 _default_mention_policy 里再降档）
_TYPE_POLICY_DEFAULT = {
    SEMANTIC: MENTION_DIRECT,
    GROWTH: MENTION_DIRECT,
    EMOTIONAL: MENTION_TONE_ONLY,
    EPISODIC: MENTION_SOFT_ECHO,
    DREAM: MENTION_SOFT_ECHO,
    THOUGHT: MENTION_SOFT_ECHO,
}

KV_PREFIX = "mingbu.mem"

# 跨轮回写反馈：记录「本轮注入了哪些记忆 id」的 KV 键前缀（按 uid 隔离）。
# 注意：这些 id 只存 KV 元数据，绝不写进注入给主 LLM 的文本，避免内部标识泄漏。
KV_INJECTED_PREFIX = "mingbu.mem.injected"
# 每用户最多登记的「上轮被注入 id」数（钳制，防无限增长/串用户）。
MAX_TRACKED_INJECTED = 20

# 隐式引用标记：注入文本携带不透明短码（〔M1〕…），主 LLM 只见短码、
# 见不到真实 id；用户消息含短码时可精确定位到具体某条记忆做单条反馈。
KV_REFS_PREFIX = "mingbu.mem.refs"
# 短码用 CJK 括号包裹，降低主 LLM 把标记当正文照抄/模仿的概率。
REF_CODE_FMT = "〔M{0}〕"
REF_CODE_PATTERN = re.compile("〔M(\\d+)〕")

# 用户否认/纠正/不愿被提及的信号词（借鉴 memory_companion 的 mentionability 反馈）。
# 命中这些信号时，若上一轮注入过相关记忆，应下调其权重与提及策略。
# 复核：全仓原无此类词典（grep 否认/纠正/记错了 → 仅 therapy.py:37 注释文案），故此处新建。
_REJECT_SIGNALS = (
    "不是这样", "不是的", "记错了", "说错了", "别乱说", "胡说", "才不是",
    "别提了", "不想提", "别再提", "翻旧账", "谁让你提", "少提", "不提",
    "你怎么又", "又提", "哪有", "没有的事", "乱记", "记岔了", "搞错了",
)
# 用户明确纠正（给出正确信息）→ 比单纯否认更确定
_CORRECT_SIGNALS = (
    "其实是", "我说的是", "应该是", "纠正一下", "不是…是", "我上次说的是",
)


def classify_reaction(text: str) -> Optional[str]:
    """把用户消息粗分类为对上轮记忆的反应信号。

    返回 "correct" / "reject" / None（None = 无否认/纠正信号，不触发反馈）。
    先判 correct（纠正隐含否认，且信息更明确），再判 reject。
    这是启发式信号，宁可漏判也不误判——误判会把正常消息当成否认而错误降权。
    """
    if not text:
        return None
    t = str(text)
    for w in _CORRECT_SIGNALS:
        if w in t:
            return "correct"
    for w in _REJECT_SIGNALS:
        if w in t:
            return "reject"
    return None


def _now_iso() -> str:
    return now_bj().isoformat()


def _parse_dt(s: str) -> datetime:
    try:
        return datetime.fromisoformat(s)
    except Exception as e:
        logger.debug(f"[mingbu.mem] 时间解析失败(用当前时间): {e}")
        return now_bj()


class Memory:
    """单条记忆。纯数据容器，带衰减/强化逻辑。"""

    def __init__(self, user_id: str, mtype: str, content: str,
                 source: str = "chat", importance: float = 0.5,
                 emotional_weight: float = 0.0, tags: Optional[List[str]] = None,
                 mention_policy: Optional[str] = None,
                 scope: Optional[str] = None,
                 confidence: Optional[float] = None,
                 verified: bool = False,
                 expires_at: Optional[str] = None):
        self.id = uuid.uuid4().hex[:12]
        self.user_id = user_id
        self.type = mtype
        self.content = content
        self.source = source
        self.importance = max(0.0, min(1.0, importance))
        self.emotional_weight = max(-1.0, min(1.0, emotional_weight))
        self.tags = tags or []
        now = _now_iso()
        self.created_at = now
        self.last_accessed = now
        self.access_count = 0
        self.decay_score = 1.0  # 1=鲜活, 0=消散
        # 可见范围（2.0 群聊/私聊隔离）：private 仅私聊注入，group 可在群分享。
        # None（调用方未指定）时保守按 DEFAULT_SCOPE=private 处理。
        self.scope = scope or DEFAULT_SCOPE
        # 元数据（2.0 第二阶段）：
        #   confidence 可信度 0~1（用户明确表达=高，模型推测=低）；
        #   verified   是否经用户确认（推测性内容默认 False，防幻觉写入）；
        #   expires_at 过期时间 ISO 串（情境记忆按期归档），None=不过期。
        self.confidence = DEFAULT_CONFIDENCE if confidence is None \
            else max(0.0, min(1.0, float(confidence)))
        self.verified = bool(verified)
        self.expires_at = expires_at
        # 提及策略（借鉴 memory_companion 的 mention_policy，防"主动戳痛"）：
        #   direct            稳定事实/用户主动要求记住的，可自然说出
        #   soft_echo         关系/约定/未闭合线索，温和呼应，不复述档案
        #   tone_only         情绪线/bot 自身状态/敏感背景，只影响语气
        #   avoid_unless_asked 冲突伤疤/尴尬/被否认纠正的内容，除非用户主动问起
        # None 时按内容特征推导（见 _default_mention_policy），可在写入时显式指定覆盖。
        self.mention_policy = mention_policy or self._default_mention_policy()

    def _default_mention_policy(self) -> str:
        """按记忆类型 + 情绪极性推导默认提及策略（防"主动戳痛"）。

        - 语义/成长记忆多为稳定事实与里程碑 → direct（可自然说出）；
        - 情绪记忆默认 tone_only（只影响语气）；负向情绪进一步降为
          avoid_unless_asked，避免模型主动翻旧账（借鉴 memory_companion）；
        - 情景/梦境/想法 → soft_echo（可温和呼应，不作档案复述）。

        写入侧若判断为冲突伤疤/尴尬/被否认内容，可显式传 mention_policy 覆盖。
        """
        policy = _TYPE_POLICY_DEFAULT.get(self.type, "soft_echo")
        # 负向情绪记忆默认不主动提（情感愈合：可被回忆，但不该由 bot 先开口）
        if self.type == EMOTIONAL and self.emotional_weight < 0:
            policy = "avoid_unless_asked"
        return policy

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "user_id": self.user_id,
            "type": self.type,
            "content": self.content,
            "source": self.source,
            "importance": self.importance,
            "emotional_weight": self.emotional_weight,
            "tags": self.tags,
            "created_at": self.created_at,
            "last_accessed": self.last_accessed,
            "access_count": self.access_count,
            "decay_score": self.decay_score,
            "mention_policy": self.mention_policy,
            "scope": self.scope,
            "confidence": self.confidence,
            "verified": self.verified,
            "expires_at": self.expires_at,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Memory":
        m = cls.__new__(cls)
        m.id = d.get("id", uuid.uuid4().hex[:12])
        m.user_id = d.get("user_id", "")
        m.type = d.get("type", EPISODIC)
        m.content = d.get("content", "")
        m.source = d.get("source", "chat")
        m.importance = d.get("importance", 0.5)
        m.emotional_weight = d.get("emotional_weight", 0.0)
        m.tags = d.get("tags", [])
        m.created_at = d.get("created_at", _now_iso())
        m.last_accessed = d.get("last_accessed", m.created_at)
        m.access_count = d.get("access_count", 0)
        m.decay_score = d.get("decay_score", 1.0)
        # 老数据无 mention_policy 键：回退到类型默认（保持与 __init__ 推导一致）
        m.mention_policy = d.get("mention_policy") or _TYPE_POLICY_DEFAULT.get(
            m.type, MENTION_SOFT_ECHO
        )
        # 老数据无 scope 键：保守回退 private（私聊秘密绝不在群里复述）。
        # 仅接受合法值，非法/缺失一律 private。
        m.scope = d.get("scope") if d.get("scope") in ALL_SCOPES else DEFAULT_SCOPE
        # 元数据回退（2.0 第二阶段）：老数据无这些键时给安全默认值。
        # confidence 缺失回退为「模型推测」档（不假装用户明确表达过）。
        try:
            conf = float(d.get("confidence", DEFAULT_CONFIDENCE))
        except (TypeError, ValueError):
            conf = DEFAULT_CONFIDENCE
        m.confidence = max(0.0, min(1.0, conf))
        m.verified = bool(d.get("verified", False))
        m.expires_at = d.get("expires_at") or None
        return m

    def is_expired(self) -> bool:
        """是否已过期（expires_at 早于当前时间）。

        用于情境记忆按期归档（方案 4.1 L3）。expires_at 为 None 视为不过期；
        时间解析失败按不过期处理（宁保守保留，不误删）。
        """
        if not self.expires_at:
            return False
        try:
            return now_bj() > datetime.fromisoformat(self.expires_at)
        except Exception as e:  # noqa: BLE001
            logger.debug(f"[mingbu.mem] 过期时间解析失败(视为未过期): {e}")
            return False

    def touch(self):
        self.last_accessed = _now_iso()
        self.access_count += 1
        # 被回忆会略微强化
        self.decay_score = min(1.0, self.decay_score + 0.05)

    def decay(self, days: float = 1.0) -> float:
        """按天衰减。重要性越高衰减越慢；情绪权重调节衰减速度
        （参考 iris_memory 的差异情感衰减：负面情绪衰减更快→情感愈合，
        正面情绪衰减更慢→留住美好）。

        emotional_weight ∈ [-1, 1]：负值加速衰减，正值减速衰减。
        """
        base_rate = 0.04 * (1.0 - self.importance)  # 0.04~0
        # -1 → 1.5x（快忘/愈合），+1 → 0.5x（慢忘/留存）
        emo_factor = 1.0 - self.emotional_weight * 0.5
        rate = base_rate * emo_factor
        self.decay_score = max(0.0, self.decay_score - rate * days)
        return self.decay_score

    def effective_weight(self) -> float:
        """检索排序权重：重要性 × 鲜活度 × (1 + 情绪强度)。"""
        emo = 1.0 + abs(self.emotional_weight) * 0.5
        return self.importance * self.decay_score * emo


class MemoryCore:
    """记忆管理器。通过 plugin 的 KV 接口持久化。

    写入策略为「写回 + 防抖」：add/consolidate 只更新内存缓存并打脏标记，
    由 `_schedule_flush` 在静默 FLUSH_DEBOUNCE_SEC 后合并落盘一次。
    这避免了「每条消息全量序列化整个记忆列表」的 O(N) 热点。
    需要强一致时调用 `flush(uid)` / `flush_all()`（插件 terminate 时必调）。
    """

    MAX_CACHE_USERS = 500
    MAX_MEMORIES_PER_USER = 400  # 默认值；运行时以配置 memory_max_per_user 为准
    FLUSH_DEBOUNCE_SEC = 2.0

    def __init__(self, plugin):
        self.plugin = plugin
        self._cache: Dict[str, List[Memory]] = {}
        self._access_counter: Dict[str, int] = {}  # 真正 LRU 计数器
        # 用户最后衰减时间（实例级；此前误用类属性导致多实例串状态）
        self._last_decay_ts: Dict[str, str] = {}
        self._dirty: Set[str] = set()
        self._scheduler = WriteBehindScheduler(
            self.flush, lambda: self.FLUSH_DEBOUNCE_SEC
        )
        # 记忆统计缓存：(user_id -> (时间戳, 统计结果))；写入经 _mark_dirty 失效。
        self._stats_cache: Dict[str, Tuple[float, dict]] = {}

    def _max_memories(self) -> int:
        """每用户记忆上限：优先读取配置 memory_max_per_user（缺省/非法回退默认）。"""
        try:
            v = int((getattr(self.plugin, "_config", {}) or {})
                    .get("memory_max_per_user", self.MAX_MEMORIES_PER_USER))
            return max(50, min(v, 2000))
        except Exception:
            return self.MAX_MEMORIES_PER_USER

    def _evict_cache_if_needed(self):
        """LRU：超出上限时淘汰访问最少的用户（脏用户不淘汰，避免丢写）。"""
        if len(self._cache) <= self.MAX_CACHE_USERS:
            return
        # 按访问次数升序排，淘汰后一半
        sorted_users = sorted(self._cache.keys(),
                              key=lambda u: self._access_counter.get(u, 0))
        for key in sorted_users[:len(sorted_users) // 2]:
            if key in self._dirty:
                continue  # 尚未落盘，保留到 flush 之后再由下一轮淘汰
            self._cache.pop(key, None)
            self._access_counter.pop(key, None)
            self._last_decay_ts.pop(key, None)

    def _apply_decay(self, mems: List[Memory], days: float = 1.0):
        """惰性衰减：批量应用到记忆列表。"""
        for m in mems:
            m.decay(days)

    def _apply_pending_decay(self, user_id: str, mems: List[Memory]) -> bool:
        """应用挂起的衰减。返回是否真的衰减过（需要落盘）。

        此前该检查只写在「缓存未命中」分支里，而 decay_all() 又会先 _load()
        把用户塞进缓存，导致后续 _load 永远命中缓存、衰减永不生效。
        现改为无论缓存命中与否都检查。
        """
        last = self._last_decay_ts.get(user_id)
        if not last:
            # 首次见到该用户：以此刻为衰减计时起点，不追溯历史
            self._last_decay_ts[user_id] = _now_iso()
            return False
        if not mems:
            return False
        try:
            diff = (now_bj() - _parse_dt(last)).total_seconds() / 86400
        except Exception:
            self._last_decay_ts.pop(user_id, None)
            return False
        if diff <= 0.8:
            return False
        self._apply_decay(mems, diff)
        self._last_decay_ts[user_id] = _now_iso()
        return True

    # ---- 存储 ----
    async def _load(self, user_id: str) -> List[Memory]:
        self._evict_cache_if_needed()
        if user_id in self._cache:
            self._access_counter[user_id] = self._access_counter.get(user_id, 0) + 1
            mems = self._cache[user_id]
            if self._apply_pending_decay(user_id, mems):
                self._mark_dirty(user_id)
            return mems

        raw = await self.plugin.get_kv_data(f"{KV_PREFIX}:{user_id}", None)
        mems = []
        if raw:
            try:
                mems = [Memory.from_dict(d) for d in json.loads(raw)]
            except Exception as e:
                logger.warning(f"[mingbu.mem] 记忆数据损坏，已重置: {e}")
                mems = []

        self._cache[user_id] = mems
        self._access_counter[user_id] = 1
        if self._apply_pending_decay(user_id, mems):
            self._mark_dirty(user_id)
        return mems

    def _compact(self, user_id: str) -> List[Memory]:
        """清理消散记忆 + 超额裁剪，返回压缩后的列表。"""
        cap = self._max_memories()
        mems = [m for m in self._cache.get(user_id, []) if m.decay_score > 0.02]
        if len(mems) > cap:
            mems.sort(key=lambda m: m.effective_weight(), reverse=True)
            mems = mems[:cap]
        self._cache[user_id] = mems
        return mems

    def _mark_dirty(self, user_id: str):
        """标记待落盘并安排一次防抖 flush（无事件循环时降级为立即同步标记）。"""
        self._dirty.add(user_id)
        # 该用户记忆已变更：让可能缓存的统计失效，下次 stats 重新聚合
        self._stats_cache.pop(user_id, None)
        # 防抖调度交给 WriteBehindScheduler（机械逻辑去重，见 kv_cache.py）
        self._scheduler.schedule(user_id)

    async def flush(self, user_id: str):
        """强制把某用户的记忆写回 KV。"""
        if user_id not in self._dirty:
            return
        self._dirty.discard(user_id)
        mems = self._compact(user_id)
        try:
            await self.plugin.put_kv_data(
                f"{KV_PREFIX}:{user_id}",
                json.dumps([m.to_dict() for m in mems], ensure_ascii=False),
            )
        except Exception as e:
            logger.warning(f"[mingbu.mem] 保存失败: {e}")
            self._dirty.add(user_id)  # 失败重新入队，等下次 flush 重试

    async def flush_all(self):
        """落盘全部脏用户（插件 terminate / 重载前必调）。"""
        self._scheduler.cancel_all()
        for uid in list(self._dirty):
            await self.flush(uid)

    async def _save(self, user_id: str):
        """兼容旧调用：转为标记脏 + 防抖落盘。"""
        self._mark_dirty(user_id)

    # ---- 写入 ----
    async def add(self, user_id: str, mtype: str, content: str,
                  source: str = "chat", importance: float = 0.5,
                  emotional_weight: float = 0.0, tags: Optional[List[str]] = None,
                  mention_policy: Optional[str] = None,
                  scope: Optional[str] = None,
                  confidence: Optional[float] = None,
                  verified: bool = False,
                  expires_at: Optional[str] = None
                  ) -> Memory:
        mems = await self._load(user_id)
        # 去重（2.0 第二阶段任务 3 强化）：
        #   第一层——归一化指纹精确判重：同 type + 归一化内容完全一致 → 视为
        #   同一条（标点/空格/大小写差异不再漏判）。
        #   第二层——字符重叠相似度判重：归一化后不完全一致但 bigram 重叠度
        #   极高（轻微改写）→ 仍视为重复，避免同一事实反复写入膨胀。
        #   两层命中都走 touch()（刷新访问时间 + 略微强化），不新增条目。
        norm_query = self._normalize_content(content)
        query_bi = self._bigrams(norm_query)
        for m in mems:
            if m.type != mtype:
                continue
            norm_m = self._normalize_content(m.content)
            if not norm_query or not norm_m:
                continue
            if norm_query == norm_m:
                m.touch()
                await self._save(user_id)
                return m
            # 相似度判重：只对「足够长」的内容启用，避免短句（bigram 集合小，
            # 单字变化就跨越阈值）被误杀——如同模板不同数字的高区分度短记忆。
            # 长内容才做模糊合并，防同一事实反复膨胀。
            if len(norm_query) < _DEDUP_FUZZY_MIN_LEN:
                continue
            m_bi = self._bigrams(norm_m)
            if query_bi and m_bi and len(norm_m) >= _DEDUP_FUZZY_MIN_LEN:
                denom = max(len(query_bi), len(m_bi))
                if denom and len(query_bi & m_bi) / denom >= 0.85:
                    m.touch()
                    await self._save(user_id)
                    return m
        mem = Memory(user_id, mtype, content, source, importance,
                     emotional_weight, tags, mention_policy=mention_policy,
                     scope=scope, confidence=confidence,
                     verified=verified, expires_at=expires_at)
        mems.append(mem)
        # 上限保护即时生效（纯内存过滤+排序，不触发序列化，成本远低于落盘）
        if len(mems) > self._max_memories():
            self._compact(user_id)
        await self._save(user_id)
        return mem

    async def add_episode(self, user_id: str, content: str, **kw):
        # 注意：importance 必须从 kw 中弹出，否则会与显式实参重复传递
        # （历史缺陷：add(..., importance=kw.get(...), **kw) 在调用方显式传
        #  importance 时抛 TypeError，导致情景记忆写入整条链路中断）
        kw.setdefault("importance", 0.45)
        # scope 经 kw 透传（llm_hook 写入时按 is_group 传 group/private）
        return await self.add(user_id, EPISODIC, content, **kw)

    async def add_emotion(self, user_id: str, content: str, weight: float, **kw):
        kw.setdefault("importance", 0.6)
        kw["emotional_weight"] = weight
        return await self.add(user_id, EMOTIONAL, content, **kw)

    # ---- 相关性检索（2.0 第一阶段任务 2：轻量兜底，不依赖向量库） ----
    @staticmethod
    def _normalize_content(text: str) -> str:
        """内容归一化（2.0 第二阶段任务 3：去重强化）。

        去除全部空白与常见标点、统一小写，使「标点差异/空格差异/大小写」
        不影响判重。用于去重时的内容指纹，比旧版「截前 40 字 + 去尾标点」
        更鲁棒（旧版只在尾部去标点，句中标点/空格差异会漏判）。
        """
        if not text:
            return ""
        t = str(text).lower()
        # 去除所有空白（含全角空格）与中英文常见标点
        return re.sub(r"[\s\u3000，。！？；：、,.!?;:()（）【】\[\]\"'“”‘’…—\-]+",
                      "", t)

    @staticmethod
    def _bigrams(text: str) -> Set[str]:
        """中文字符 bigram 集合（近似相关性用）。

        中文无空格分词，字符 bigram 是零依赖的相关性近似。纯 ASCII 词串
        退化为按空格切词。过滤单字符噪声，保留有效片段。
        """
        if not text:
            return set()
        t = str(text)
        if not any('\u4e00' <= ch <= '\u9fff' for ch in t):
            # 无中文：按空格/标点切词，保留长度>=2 的词
            import re as _re
            return {w for w in _re.split(r"[\s，。！？,.!?]+", t.lower())
                    if len(w) >= 2}
        return {t[i:i + 2] for i in range(len(t) - 1)}

    async def retrieve_relevant(self, user_id: str, query: str, limit: int = 5,
                                min_score: float = 0.05,
                                max_scope: Optional[str] = None,
                                exclude_contents: Optional[List[str]] = None,
                                ) -> List[Memory]:
        """按当前消息相关性检索历史记忆（轻量兜底，无需 embedding/rerank）。

        2.0 Memory Retrieval 兜底路径：当 embedding_enabled / rerank_enabled
        均关闭（多数部署的默认态）时，recall_important/recall_emotional 只按
        时间/权重排序、完全不感知当前消息。本方法用「字符 bigram 重叠 × 有效
        权重」给记忆打相关性分，返回最相关的 top-limit 条。

        - 打分：overlap = |query_bigrams ∩ mem_bigrams| / |query_bigrams|，
          score = overlap × (0.3 + 0.7 × effective_weight)。effective_weight
          已含 importance×decay×情绪强度，故相关且重要/鲜活的记忆优先。
        - min_score：相关性门槛，低于此值不返回（宁缺毋滥，减少无关注入）。
        - max_scope：可见范围（群聊传 GROUP，只返回可分享记忆）。
        - exclude_contents：当前会话历史已有内容的列表，命中的记忆跳过
          （避免重复注入 AstrBot 当前上下文已包含的信息）。

        无 embedding 时这是近似（非语义精确匹配），但对「代词/未完成话题/
        关键实体复现」类连续性足够；embedding/rerank 开启时调用方应优先用
        _recall_semantic/_rerank_memories，本方法作为其都不可用时的降级。
        """
        mems = await self._load(user_id)
        query_bi = self._bigrams(query)
        if not mems or not query_bi:
            return []
        # 会话历史去重：把已有内容也切成 bigram，命中则跳过该记忆
        excl_bi = [self._bigrams(c) for c in (exclude_contents or []) if c]
        total = len(query_bi)
        scored = []
        for m in mems:
            if max_scope == SCOPE_GROUP and getattr(m, "scope", DEFAULT_SCOPE) != SCOPE_GROUP:
                continue
            content = getattr(m, "content", "") or ""
            mem_bi = self._bigrams(content)
            if not mem_bi:
                continue
            overlap = len(query_bi & mem_bi) / total
            if overlap <= 0:
                continue
            score = overlap * (0.3 + 0.7 * m.effective_weight())
            if score < min_score:
                continue
            # 会话去重：该记忆内容与当前历史某条高度重叠则跳过
            if excl_bi:
                mem_total = max(1, len(mem_bi))
                dup = any(len(mem_bi & h) / mem_total >= 0.8 for h in excl_bi)
                if dup:
                    continue
            scored.append((score, m))
        scored.sort(key=lambda t: t[0], reverse=True)
        return [m for _, m in scored[:limit]]

    # ---- 检索 ----
    async def recall_recent(self, user_id: str, n: int = 8,
                            mtype: Optional[str] = None) -> List[Memory]:
        mems = await self._load(user_id)
        if mtype:
            mems = [m for m in mems if m.type == mtype]
        mems.sort(key=lambda m: m.created_at, reverse=True)
        return mems[:n]

    async def recall_important(self, user_id: str, n: int = 6,
                               min_weight: float = 0.3,
                               max_scope: Optional[str] = None) -> List[Memory]:
        """召回重要记忆。max_scope 控制可见范围上限（2.0 群聊隔离）：

        - max_scope=GROUP：只返回 group scope（群聊只召回可在群分享的记忆，
          私聊秘密绝不在群里复述）。
        - max_scope=PRIVATE / None：不过滤（私聊上下文保留全部记忆连贯性，
          private 与 group 都可召回）。
        """
        mems = await self._load(user_id)
        # 原地过滤避免额外列表分配
        kept = []
        for m in mems:
            if max_scope == SCOPE_GROUP and getattr(m, "scope", DEFAULT_SCOPE) != SCOPE_GROUP:
                continue
            if m.effective_weight() >= min_weight:
                kept.append(m)
        kept.sort(key=lambda m: m.effective_weight(), reverse=True)
        return kept[:n]

    async def recall_emotional(self, user_id: str, n: int = 5,
                               max_scope: Optional[str] = None) -> List[Memory]:
        """召回情绪记忆。max_scope 语义同 recall_important（群聊只取 group）。"""
        mems = await self._load(user_id)
        emo = []
        for m in mems:
            if m.type != EMOTIONAL:
                continue
            if max_scope == SCOPE_GROUP and getattr(m, "scope", DEFAULT_SCOPE) != SCOPE_GROUP:
                continue
            emo.append(m)
        emo.sort(key=lambda m: abs(m.emotional_weight), reverse=True)
        return emo[:n]

    async def sample_recent(self, user_id: str, n: int = 12,
                            exclude_types: Optional[Set[str]] = None) -> List[Memory]:
        """用于梦境/成长的近期抽样（混合类型，偏近期）。

        exclude_types: 需排除的记忆类型集合。梦境等"生成型"记忆不应回流进
            下一轮生成语料——2026-09-22 生产取证：兜底模板梦被当作"记忆碎片"
            回喂后产出套娃文本，调用方（dream/growth）应排除 DREAM。
        """
        mems = await self._load(user_id)
        if exclude_types:
            mems = [m for m in mems if m.type not in exclude_types]
        mems.sort(key=lambda m: m.created_at, reverse=True)
        return mems[:n]

    # ---- 衰减 / 巩固 ----
    async def decay_all(self, user_id: str, days: float = 1.0) -> int:
        """按距上次衰减的真实间隔执行衰减（_load 已处理过的部分不会重复算）。"""
        mems = await self._load(user_id)
        if not mems:
            return 0
        last = self._last_decay_ts.get(user_id)
        elapsed = days
        if last:
            try:
                elapsed = max(
                    0.0,
                    (now_bj() - _parse_dt(last)).total_seconds() / 86400,
                )
            except Exception:
                elapsed = days
        if elapsed > 0:
            self._apply_decay(mems, elapsed)
            self._last_decay_ts[user_id] = _now_iso()
            self._mark_dirty(user_id)
        return len(mems)

    async def consolidate(self, user_id: str, keep_ids: List[str]) -> int:
        """巩固：把传入的 id 列表的衰减进度拉满（视为"被点亮的记忆"）。

        2026-09-22（P1）：不再对 importance 做 +0.1 递增。此前每晚被巩固的
        恰是最近条目（高频含新增的梦），连续 +0.1 会把 importance 推到
        0.85+（生产实测），形成"越新越重要"的自我放大通胀。
        """
        mems = await self._load(user_id)
        keep = set(keep_ids)
        reinforced = 0
        for m in mems:
            if m.id in keep:
                m.decay_score = 1.0
                m.last_accessed = _now_iso()
                reinforced += 1
        if reinforced:
            await self._save(user_id)
        return reinforced

    async def new_count_since(self, user_id: str, ref_type: str,
                              count_types: Optional[Set[str]] = None) -> int:
        """统计"上次 ref_type 类记忆产出之后"新增的素材记忆条数。

        触发条件化用（2026-09-22 P2）：夜梦/成长反思是"消费新素材"的任务，
        素材不足时跳过可省一次 LLM 调用。ref_type 取该任务上次产出的类型
        （dream/growth）；从未跑过则视为全部素材都新。
        count_types 默认只算真实互动素材（episodic/emotional）——semantic
        里混着反思自己重写的 [画像]，夜梦又在成长反思（02:00）之后跑
        （03:00），把 semantic 算进去会让"凌晨产出的画像"伪装成新素材。
        """
        mems = await self._load(user_id)
        types = count_types or {EPISODIC, EMOTIONAL}
        ref_ts = None
        for m in mems:
            if m.type == ref_type and (ref_ts is None or m.created_at > ref_ts):
                ref_ts = m.created_at
        if ref_ts is None:
            return sum(1 for m in mems if m.type in types)
        return sum(1 for m in mems if m.type in types and m.created_at > ref_ts)

    async def remove(self, user_id: str, mem_id: str) -> bool:
        """从用户记忆桶中删除指定记忆（梦池硬预算淘汰用，2026-09-22 P2）。

        返回是否删除了记忆；找不到返回 False。调用方需自行落盘（本方法
        内部直接标记脏 + 立即 flush，保证 KV 一致）。
        """
        mems = await self._load(user_id)
        kept = [m for m in mems if m.id != mem_id]
        if len(kept) == len(mems):
            return False
        self._cache[user_id] = kept
        self._mark_dirty(user_id)
        await self.flush(user_id)
        return True

    async def add_tag(self, user_id: str, mem_id: str, tag: str) -> bool:
        """给指定记忆追加标签（幂等）。

        2026-09-22（P1）：真正的"已消费/来源"标记。此前投递侧用 consolidate()
        冒充消费（只会强化记忆、不改变可投递性），同一梦会被重复投递。
        返回是否找到了该记忆；标签已存在时不重复追加。
        """
        mems = await self._load(user_id)
        for m in mems:
            if m.id != mem_id:
                continue
            tags = list(m.tags or [])
            if tag not in tags:
                tags.append(tag)
                m.tags = tags
                m.last_accessed = _now_iso()
                await self._save(user_id)
            return True
        return False

    # ---- 统计 ----
    async def stats(self, user_id: str) -> dict:
        now = time.monotonic()
        cached = self._stats_cache.get(user_id)
        if cached is not None and (now - cached[0]) < _STATS_TTL:
            return cached[1]
        mems = await self._load(user_id)
        by_type: Dict[str, int] = {}
        for m in mems:
            by_type[m.type] = by_type.get(m.type, 0) + 1
        result = {
            "total": len(mems),
            "by_type": by_type,
            "avg_importance": round(
                sum(m.importance for m in mems) / max(1, len(mems)), 2),
        }
        self._stats_cache[user_id] = (now, result)
        return result

    # ---- 序列化辅助 ----
    @staticmethod
    def to_brief(mems: List[Memory], max_len: int = 80) -> str:
        if not mems:
            return "（暂无记忆）"
        lines = []
        for m in mems:
            tag = {
                EPISODIC: "事", SEMANTIC: "知", EMOTIONAL: "情",
                DREAM: "梦", GROWTH: "长", THOUGHT: "念",
            }.get(m.type, "?")
            c = m.content[:max_len]
            lines.append(f"  [{tag}] {c}")
        return "\n".join(lines)

    @staticmethod
    def to_brief_with_refs(mems: List["Memory"], max_len: int,
                           mapping: Dict[str, str]) -> str:
        """生成带隐式引用短码的记忆摘要，短码→真实 id 写入 mapping。

        每条记忆前缀一个不透明短码（〔M1〕〔M2〕…），真实 id 绝不出现在
        文本里（mapping 只写 KV），主 LLM 只见短码。用户消息含短码时，
        反馈侧据此定位到具体某条记忆做单条精确反馈。
        mapping 为调用方传入的空 dict，本函数填充 {短码: 真实 id}。
        """
        if not mems:
            return "（暂无记忆）"
        lines = []
        for i, m in enumerate(mems, 1):
            code = REF_CODE_FMT.format(i)
            mapping[code] = m.id
            tag = {
                EPISODIC: "事", SEMANTIC: "知", EMOTIONAL: "情",
                DREAM: "梦", GROWTH: "长", THOUGHT: "念",
            }.get(m.type, "?")
            c = m.content[:max_len]
            lines.append(f"  {code}[{tag}] {c}")
        return "\n".join(lines)

    @staticmethod
    def split_by_mention(mems: List["Memory"]) -> Dict[str, List["Memory"]]:
        """把记忆按 mention_policy 分组，供注入侧分槽处理（防"主动戳痛"）。

        返回 {policy: [Memory]}，只含非空组。注入侧据此决定各组的呈现方式：
        - direct / soft_echo → 可进入「我记得」正文，自然流露；
        - tone_only          → 只作为「情绪底色」影响语气，不逐条复述；
        - avoid_unless_asked → 默认完全注入（模型不该主动翻旧账），
          仅当用户主动问起相关话题时才由调用方决定是否放开。
        """
        out: Dict[str, List["Memory"]] = {}
        for m in mems:
            out.setdefault(getattr(m, "mention_policy", MENTION_SOFT_ECHO) or MENTION_SOFT_ECHO,
                           []).append(m)
        return out

    # ---- 回写反馈（借鉴 memory_companion 的 mentionability 反馈机制） ----
    async def feedback(self, user_id: str, mem_id: str, reaction: str) -> bool:
        """根据用户对已注入记忆的反应，调整该记忆的权重与提及策略。

        reaction 取值：
        - "accept"  用户接受或正面回应 → importance 微升（cap 1.0），touch()
        - "reject"  用户否认或表现出不适 → importance 降 0.15（floor 0.05），
                    mention_policy 降为 avoid_unless_asked（模型下次不再主动提）
        - "correct" 用户纠正（"上次我说的是…"）→ importance 降 0.1，
                    mention_policy 降为 avoid_unless_asked，tags 追加「纠正」

        返回是否找到并更新了该记忆。
        """
        mems = await self._load(user_id)
        for m in mems:
            if m.id != mem_id:
                continue

            if reaction == "accept":
                m.importance = min(1.0, m.importance + 0.05)
                m.touch()
            elif reaction == "reject":
                m.importance = max(0.05, m.importance - 0.15)
                m.mention_policy = MENTION_AVOID
            elif reaction == "correct":
                m.importance = max(0.05, m.importance - 0.10)
                m.mention_policy = MENTION_AVOID
                tags = list(m.tags or [])
                if "纠正" not in tags:
                    tags.append("纠正")
                    m.tags = tags

            await self._save(user_id)
            return True
        return False

    # ---- 跨轮回写反馈：登记/读取「本轮注入的记忆 id」 ----
    async def remember_injected(self, user_id: str, mem_ids: List[str]):
        """登记本轮实际注入给主 LLM 的记忆 id（供下一轮反馈触发用）。

        id 只写入 KV 元数据，绝不进入注入给主 LLM 的文本，避免内部标识泄漏。
        按 uid 隔离并钳制在 MAX_TRACKED_INJECTED，防无限增长/串用户。
        """
        if not mem_ids:
            return
        try:
            ids = [str(i) for i in mem_ids][:MAX_TRACKED_INJECTED]
            await self.plugin.put_kv_data(
                f"{KV_INJECTED_PREFIX}:{user_id}",
                json.dumps(ids, ensure_ascii=False),
            )
        except Exception as e:
            logger.debug(f"[mingbu.mem] 登记注入 id 失败(已忽略): {e}")

    async def get_last_injected(self, user_id: str) -> List[str]:
        """读取上一轮登记的被注入记忆 id 列表（无则空列表）。"""
        try:
            raw = await self.plugin.get_kv_data(
                f"{KV_INJECTED_PREFIX}:{user_id}", None)
            if not raw:
                return []
            ids = json.loads(raw)
            if not isinstance(ids, list):
                return []
            return [str(i) for i in ids][:MAX_TRACKED_INJECTED]
        except Exception as e:
            logger.debug(f"[mingbu.mem] 读取注入 id 失败(已忽略): {e}")
            return []

    async def clear_injected(self, user_id: str):
        """清空上一轮登记的被注入记忆 id（本轮无注入时调用，防下轮误反馈旧 id）。"""
        try:
            await self.plugin.put_kv_data(
                f"{KV_INJECTED_PREFIX}:{user_id}", json.dumps([]))
        except Exception as e:
            logger.debug(f"[mingbu.mem] 清空注入 id 失败(已忽略): {e}")

    # ---- 隐式引用标记：短码→真实 id 映射（KV），单条精确反馈 ----

    async def remember_injected_refs(
            self, user_id: str, mapping: Dict[str, str]):
        """登记本轮注入的短码→真实 id 映射（只写 KV，不进文本）。

        与 remember_injected 并存：refs 映射供精确反馈，id 列表供全量回退。
        """
        if not mapping:
            return
        try:
            data = {str(k): str(v) for k, v in list(mapping.items())[:MAX_TRACKED_INJECTED]}
            await self.plugin.put_kv_data(
                f"{KV_REFS_PREFIX}:{user_id}",
                json.dumps(data, ensure_ascii=False),
            )
        except Exception as e:
            logger.debug(f"[mingbu.mem] 登记引用映射失败(已忽略): {e}")

    async def parse_referenced_ids(self, user_id: str, text: str) -> List[str]:
        """从用户消息里提取短码，查映射返回真实 id 列表。

        无短码或无映射 → 返回空列表（调用方据此回退全量 feedback）。
        """
        if not text:
            return []
        try:
            raw = await self.plugin.get_kv_data(
                f"{KV_REFS_PREFIX}:{user_id}", None)
            if not raw:
                return []
            mapping = json.loads(raw)
            if not isinstance(mapping, dict):
                return []
            found = REF_CODE_PATTERN.findall(text)
            ids = [mapping[f"〔M{n}〕"] for n in found if f"〔M{n}〕" in mapping]
            return list(dict.fromkeys(ids))[:MAX_TRACKED_INJECTED]  # 去重保序
        except Exception as e:
            logger.debug(f"[mingbu.mem] 解析引用 id 失败(已忽略): {e}")
            return []
