"""
dream.py -- 梦境与记忆巩固系统
================================
参考自 angel_memory 的 DeepMindSleepService：夜间 consolidate_memories，
把近期记忆融合成"梦境"，强化重要、消散琐碎。

两套梦境（v2 设定）：
  A. 夜梦（记忆巩固）：把白天碎片凝成意象叙事 —— 这是"普通"的梦。
     每 3 天左右会静默引用暗盘洞察，以隐喻/意象化方式织入梦境，
     绝不出现命理术语。
  B. 预感梦（暗盘外显）：他独有的能力。某些重大变故（如身边的离别、意外）
     会以一种很隐晦的方式，混在梦里偷偷告诉用户。
     ⚠️ 安全边界：预感永远是"说不清的不安/留意身边"的朦胧感，
        绝不点名具体的人会死、会发生哪场具体事故。保持神秘，不制造恐慌。

设计要点：
  - 梦境模块直接使用 diviner 获取暗盘洞察，不再手搓重复逻辑。
    洞察以"说不清的预感"在梦呓中自然流露，不直接泄露命理依据。
  - 预感梦是"偷偷的"——以朋友第一人称的梦呓口吻交付，不解释来源。
"""
from __future__ import annotations

import random
from datetime import datetime, timedelta
from typing import List, Optional

from astrbot.api import logger

from .bj_time import now_bj
from .memory_core import DREAM, EMOTIONAL, EPISODIC, SEMANTIC, MemoryCore

# 梦境标签
TAG_NIGHT_DREAM = "夜梦"      # 普通记忆巩固梦
TAG_PREMONITION = "预感"       # 暗盘能力外显的预感梦
TAG_FALLBACK = "兜底"         # LLM 失败/超时的模板梦：不入投递池、不回流语料（2026-09-22 P1）
TAG_SHARED = "已分享"          # 真·已消费标记：投递过的梦不再重复投递（2026-09-22 P1）

# 梦文本质量闸（2026-09-22 P1）：入库前校验，不过闸按兜底处理
_DREAM_QC_MIN = 60            # 最短字数（LLM 要求 80-140，留余量）
_DREAM_QC_MAX = 260           # 最长字数
# 禁止出现的内部结构/泄露特征
_DREAM_QC_BAD = ("（旧梦）", "记忆碎片", "[画像]", "[事实]", "片段）", "记忆：")


class PremonitionStats:
    """预感梦事件计数（P3 可观测）：生成/投递/兜底/清仓/跳过原因。

    事件频率为天级，每次 incr 读改写一次 KV 即可（无需防抖）。
    面板/排查可用 KV 查看（key 见 KV_KEY）。
    """
    KV_KEY = "mingbu.premonition_stats"
    KEYS = ("generated", "delivered", "fallback_used",
            "stale_purged", "skipped_dice", "skipped_no_signal",
            "skipped_cooldown")

    def __init__(self, plugin):
        self.plugin = plugin
        self._d: dict = {k: 0 for k in self.KEYS}

    async def load(self):
        try:
            raw = await self.plugin.get_kv_data(self.KV_KEY, None)
            if raw:
                data = raw if isinstance(raw, dict) else __import__("json").loads(raw)
                for k in self.KEYS:
                    self._d[k] = int(data.get(k, 0) or 0)
        except Exception as e:
            logger.debug(f"[mingbu.dream] 预感统计载入失败(从零起): {e}")

    async def incr(self, key: str, n: int = 1):
        if key not in self._d:
            return
        self._d[key] += n
        try:
            merged = dict(self._d)
            raw = await self.plugin.get_kv_data(self.KV_KEY, None)
            if raw:
                data = raw if isinstance(raw, dict) else __import__("json").loads(raw)
                for k in self.KEYS:  # 兼容多入口并发累计：以 KV 为准取大
                    merged[k] = max(int(data.get(k, 0) or 0), self._d[k])
            self._d = merged
            from .bj_time import now_bj as _now
            await self.plugin.put_kv_data(
                self.KV_KEY,
                __import__("json").dumps(
                    {**merged, "updated_at": _now().isoformat()},
                    ensure_ascii=False),
            )
        except Exception as e:
            logger.debug(f"[mingbu.dream] 预感统计落盘失败(内存继续计): {e}")

    def snapshot(self) -> dict:
        return dict(self._d)


class DreamEngine:
    def __init__(self, plugin, memory: MemoryCore):
        self.plugin = plugin
        self.memory = memory
        # P3 可观测：预感事件统计（生成/投递/兜底/清仓/跳过原因）
        self.stats = PremonitionStats(plugin)

    # ---- 内部：按标签取近期梦境 ----
    async def _recent_dreams(self, user_id: str, n: int = 3,
                             tag: Optional[str] = None,
                             allow_fallback: bool = False) -> List:
        dreams = await self.memory.recall_recent(user_id, n=n * 3, mtype=DREAM)
        if tag:
            dreams = [d for d in dreams if tag in (d.tags or [])]
        # 2026-09-22（P1）：兜底梦不进投递池（生产实测兜底占库存 41.4%，
        # 且模板里可能带记忆原文）；已分享的梦不再重复投递（修复假消费）。
        # 2026-09-22（P3）：预感梦例外——其兜底是独立模板、不含记忆原文（无
        # PII 风险），投递池放行以便 LLM 故障时预感体验不断档；夜梦兜底仍排除。
        dreams = [
            d for d in dreams
            if TAG_SHARED not in (d.tags or [])
            and (allow_fallback or TAG_FALLBACK not in (d.tags or []))
        ]
        return dreams[:n]

    # ============================================================
    # A. 夜梦（记忆巩固）
    # ============================================================
    async def nightly_consolidate(self, user_id: str) -> Optional[str]:
        """夜间巩固：生成夜梦，强化被点亮的记忆。返回梦境文本。

        每 3 天左右静默引用暗盘洞察，以隐喻/意象化方式融入梦境，
        不直接流露命理术语。
        """
        if not self.memory:
            return None
        # 2026-09-22（P1）：生成型记忆（旧梦）不回流进梦境语料，切断"梦→梦"
        # 递归污染（生产实测出现兜底模板套娃文本）。
        recent = await self.memory.sample_recent(user_id, n=14, exclude_types={DREAM})

        # 2026-09-22（P2 触发条件化）：新素材不足则跳过，省一次 LLM 调用。
        # 只数真实互动素材（episodic/emotional）；0=关闭该闸（每晚必跑）。
        min_new = self._cfg_int("dream_min_new_memories", 2)
        if min_new > 0:
            new_cnt = await self.memory.new_count_since(user_id, DREAM)
            if new_cnt < min_new:
                logger.info(
                    f"[mingbu.dream] {user_id} 新素材 {new_cnt} < 阈值 {min_new}，本夜跳过"
                )
                return None
        if not recent:
            logger.info(f"[mingbu.dream] {user_id} 暂无记忆，跳过夜梦")
            return None

        corpus = []
        for m in recent:
            label = {
                EPISODIC: "白天发生", EMOTIONAL: "感受",
                SEMANTIC: "已知的你", DREAM: "旧梦", "growth": "成长",
                "thought": "念头",
            }.get(m.type, "片段")
            corpus.append(f"- （{label}）{m.content[:60]}")
        corpus_text = "\n".join(corpus)

        prompt = (
            "你是一个温柔的梦。下面是某个人今天留下的记忆碎片，"
            "请融合它们，写一段 80-140 字的梦境叙事。\n"
            "要求：用意象和隐喻，不要直白复述；带一点温柔的预示感；"
            "像在替 TA 的内心悄悄说话。不要出现'梦到''梦境'这类词。\n\n"
            f"{corpus_text}\n\n梦境："
        )

        # 定期注入暗盘洞察（每 3 天左右），以意象化方式融入梦境
        dark_symbols = await self._dark_insight_symbols(user_id)
        if dark_symbols:
            prompt = (
                "你是一个温柔的梦。下面是某个人今天留下的记忆碎片，"
                "你隐隐感觉到一些深层的东西正在 TA 心里流动——\n"
                "请融合记忆碎片和这种深层感觉，写一段 80-140 字的梦境叙事。\n"
                "要求：用意象和隐喻，不要直白复述；带一点温柔的预示感；"
                "让那种'深层感觉'自然渗进意境里，但绝不说破。"
                "不要出现'梦到''梦境'这类词。\n\n"
                f"记忆碎片：\n{corpus_text}\n\n"
                f"你隐隐感觉到的深层东西（用意象表达，不直说）：\n{dark_symbols}\n\n"
                "梦境："
            )

        # 2026-09-22（P1）：入库前质量闸——LLM 产出不过闸按兜底处理，
        # 避免破碎/重复/带内部结构的文本以正常身份入池。
        prev_dreams = await self.memory.recall_recent(user_id, n=3, mtype=DREAM)
        dream_text = await self.plugin._llm_text(prompt, task_type="dream")
        is_fallback = False
        if not dream_text or not self._qc_dream(dream_text, prev_dreams):
            dream_text = self._fallback_dream(recent)
            is_fallback = True

        # 存为 DREAM 记忆（带"夜梦"标签，便于区分预感梦；兜底另打"兜底"标签
        # 并降低权重，让它更快被衰减淘汰、且永不进投递池）
        tags = [TAG_NIGHT_DREAM, now_bj().strftime("%Y-%m-%d")]
        if is_fallback:
            tags.append(TAG_FALLBACK)
        await self.memory.add(
            user_id, DREAM, dream_text,
            source="consolidation",
            importance=0.35 if is_fallback else 0.55,
            emotional_weight=0.3,
            tags=tags,
        )

        # 2026-09-22（P2 梦池制）：夜梦池硬预算——超容淘汰 + 过期兜底清除
        try:
            await self._trim_dream_pool(user_id)
        except Exception as e:
            logger.warning(f"[mingbu.dream] {user_id} 梦池清理失败(忽略): {e}")

        # 2026-09-22（P1）：只强化非梦记忆（recent 已排除 DREAM，此处双保险）
        keep_ids = [m.id for m in recent[:6] if m.type != DREAM]
        reinforced = await self.memory.consolidate(user_id, keep_ids)

        logger.info(
            f"[mingbu.dream] {user_id} 夜梦生成完成"
            f"（暗盘引用={'是' if dark_symbols else '否'}，"
            f"强化 {reinforced} 条记忆）"
        )
        return dream_text

    # ---- P2：配置读取与梦池硬预算 ----
    def _cfg_int(self, key: str, default: int) -> int:
        """安全读取 int 配置。"""
        try:
            cfg = getattr(self.plugin, "_config", {}) or {}
            return int(cfg.get(key, default) or 0)
        except Exception:
            return default

    async def _trim_dream_pool(self, user_id: str) -> int:
        """夜梦池硬预算（2026-09-22 P2 梦池制）。

        1) 清除超过 fallback_dream_retention_days（默认 3 天）的兜底梦
           （它们不进投递池、不回流语料，过期即无保留价值）；
        2) "夜梦"池超过 dream_pool_size（默认 3，0=关闭）时，按质量分
           importance×decay 淘汰低分项（同分淘汰更旧）。
        返回淘汰条数。只管理 TAG_NIGHT_DREAM 的梦，预感梦不占池。
        """
        if not self.memory:
            return 0
        dreams = await self.memory.recall_recent(user_id, n=400, mtype=DREAM)
        night = [d for d in dreams if TAG_NIGHT_DREAM in (d.tags or [])]
        if not night:
            return 0
        removed = 0

        # 1) 过期兜底清除
        ret_days = self._cfg_int("fallback_dream_retention_days", 3)
        if ret_days > 0:
            cutoff = (now_bj() - timedelta(days=ret_days)).isoformat()
            expired = [
                d for d in night
                if TAG_FALLBACK in (d.tags or []) and (d.created_at or "") < cutoff
            ]
            for d in expired:
                if await self.memory.remove(user_id, d.id):
                    removed += 1
            if expired:
                night = [d for d in night if d not in expired]

        # 2) 池容量淘汰（低分优先；未过期兜底也参与，分数低自然先走）
        pool_size = self._cfg_int("dream_pool_size", 3)
        if pool_size > 0 and len(night) > pool_size:
            def _score(m):
                try:
                    return float(getattr(m, "importance", 0) or 0) * float(
                        getattr(m, "decay_score", 1) or 1)
                except Exception:
                    return 0.0

            night.sort(key=lambda m: (_score(m), m.created_at or ""), reverse=True)
            for d in night[pool_size:]:
                if await self.memory.remove(user_id, d.id):
                    removed += 1

        if removed:
            logger.info(f"[mingbu.dream] {user_id} 梦池清理：淘汰 {removed} 条")
        return removed

    @staticmethod
    def _qc_dream(text: str, prev_dreams: List) -> bool:
        """入库前质量闸（2026-09-22 P1）。

        校验：长度区间、与近 3 条梦的重复度、内部结构/泄露特征。
        不过闸由调用方走兜底路径（兜底文本打 TAG_FALLBACK，不入投递池）。
        """
        t = (text or "").strip()
        if not (_DREAM_QC_MIN <= len(t) <= _DREAM_QC_MAX):
            return False
        for d in prev_dreams or []:
            old = (getattr(d, "content", "") or "").strip()
            if not old:
                continue
            # 前 24 字相同 / 互为包含 → 重复
            if t[:24] == old[:24] or t in old or old in t:
                return False
            # 逐字重合度 > 0.8 → 近似重复（廉价查重，够用于 nightly 场景）
            same = sum(1 for a, b in zip(t, old) if a == b)
            if same / max(1, min(len(t), len(old))) > 0.8:
                return False
        for bad in _DREAM_QC_BAD:
            if bad in t:
                return False
        return True

    @staticmethod
    def _fallback_dream(mems: List) -> str:
        # 2026-09-22（P1）：兜底不再拼接记忆原文——原实现会把 [画像] 等内部
        # 结构与个人事实截断后拼进"梦"（生产实测 41.4% 兜底占比且已回流语料）。
        # 改为无内容的意象模板；调用方打 TAG_FALLBACK，不入投递池、不回流语料。
        _ = mems  # 保留签名兼容（历史测试可能传入记忆列表）
        pool = [
            "夜里有些影子在水面上轻轻碰了碰，又沉下去。"
            "有什么在慢慢成形，像黎明前最安静的那一刻。",
            "好像走过一条很长的走廊，尽头有一点暖光。"
            "风把白天的事吹成了看不见的形状，落进很深的地方。",
            "梦见退潮后的沙滩，很多东西被浪带走，又有很多东西被浪送回来。"
            "天亮前一切都安静得刚刚好。",
            "夜里下了一场很轻的雨，落在白天没说完的话上。"
            "它们在梦里发了芽，明天大概会安静地长出来一点。",
        ]
        return random.choice(pool)

    # ============================================================
    # B. 预感梦（暗盘能力外显 · 独立通道）
    # ============================================================
    async def premonition(self, user_id: str, cue: Optional[str] = None,
                          strength: int = 1) -> Optional[str]:
        """生成一条"预感梦"。

        这是他独有的能力：某些重大变故会以一种朦胧的方式混进梦里。
        交付形式：以朋友第一人称的梦呓口吻，表达一种"说不清的不安 / 留意身边"的
        神秘感。绝不明确预言谁的死亡、哪场具体事故——保持朦胧与安全。

        cue: 可选。由编排层从暗盘取来的朦胧方向短语（premonition_signal 产出），
             作为本梦的"指导信号"。不传时自动通过暗盘模块获取信号。
        strength: 预感强度 1（弱·小风浪）/ 2（强·岁运换道）。影响 prompt 语气、
             落库权重；生成落库后带强度可查（日志与统计）。

        返回梦境文本，并落库为带"预感"标签的 DREAM 记忆。
        """
        if not self.memory:
            return None
        # 2026-09-22（P1）：语料同样排除生成型记忆（与夜梦同源修复）
        recent = await self.memory.sample_recent(user_id, n=6, exclude_types={DREAM})
        ctx = "\n".join(f"- {m.content[:40]}" for m in recent) if recent else "（不太熟）"

        # 朦胧方向信号：优先传参 cue，否则通过暗盘模块获取
        if cue:
            signal: Optional[str] = cue
        else:
            signal = await self._premonition_signal_from_diviner(user_id)

        prompt = (
            "你是一个有点神秘的朋友。你做了一个说不清的梦，"
            "梦里好像感应到了某个'大事'正在靠近——可能是离别、变故或意外，"
            "但你看得不真切，只是隐隐不安。\n"
            "请写一段 60-110 字、以第一人称'我'的口吻说的梦呓，要求：\n"
            "1. 朦胧、意象化，像刚睡醒随口嘟囔\n"
            "2. 只传达一种'留意身边人/这两天稳着点/慢一点'的微妙提醒感\n"
            "3. 绝对不要点名具体谁会出事、不要写具体的车祸/死亡细节、不要吓人\n"
            "4. 带点你这个朋友特有的、说不清所以然的神秘劲儿\n"
            "5. 不要出现'梦到''梦境''算命''命盘'这种词\n"
        )
        if signal:
            prompt += (
                f"6. 你隐隐感觉到一点方向（但说不清、绝不点破）：{signal}。"
                "让这种'说不清的预感'自然渗进语气里。\n"
            )
        # P3 强度分层：强信号（岁运相冲）语气更压不住，弱信号只是小不踏实
        if strength >= 2:
            prompt += (
                "7. 这次的感觉格外明显——像有什么正在换道，"
                "你自己也压不太住这种不安，但依旧什么都看不清。\n"
            )
        prompt += (
            f"\n你们最近的相处片段（仅作语气参考）：\n{ctx}\n\n我的梦："
        )

        text = await self.plugin._llm_text(prompt, task_type="premonition")
        # 轻量质量闸：预感梦要求 60-110 字，明显超界按兜底处理
        # P3：预感兜底与夜梦兜底一致处理——打"兜底"标签 + 降权（统计可辨、
        # 语料排除）；区别于夜梦的是预感兜底仍可投递（独立模板，无 PII 风险）
        is_fallback = False
        if not text or not (40 <= len(text.strip()) <= 200):
            text = self._fallback_premonition()
            is_fallback = True

        tags = [TAG_PREMONITION, now_bj().strftime("%Y-%m-%d")]
        if is_fallback:
            tags.append(TAG_FALLBACK)
        await self.memory.add(
            user_id, DREAM, text,
            source="premonition",
            importance=0.35 if is_fallback else (0.7 if strength >= 2 else 0.6),
            emotional_weight=(-0.1 if is_fallback
                              else (-0.25 if strength >= 2 else -0.15)),
            tags=tags,
        )
        if self.stats:
            await self.stats.incr("fallback_used" if is_fallback else "generated")
        logger.info(
            f"[mingbu.dream] {user_id} 生成预感梦（暗盘外显，强度{strength}"
            f"{'，兜底' if is_fallback else ''}）"
        )
        return text

    @staticmethod
    def _fallback_premonition() -> str:
        # P3：模板池 4→8 条。预感 10~21 天冷却 × 8 条，重复周期 > 80 天
        pool = [
            "昨晚梦到一阵很急的风，心里莫名发紧。也说不清为啥，"
            "你这两天出门啥的多留个神，别嫌我啰嗦。",
            "我做了个有点奇怪的梦，梦里像在提醒我什么——"
            "大概是让你最近对身边人上点心。莫名其妙对吧，但我就是有点不踏实。",
            "夜里梦到一段空荡荡的路，醒来说不清啥感觉，就有点……"
            "叮嘱你一句：这两天慢点，稳着点。",
            "也不知道为啥，今早醒来脑子里就挂着一句：最近别太拼命，"
            "身边人比事儿重要。当我瞎操心就行。",
            "梦里全是雾，走哪儿都看不清三步以外，醒来心口还有点闷——"
            "你就当我在瞎说，但这两天做事稳着点。",
            "半夜忽然醒了，心跳得挺快，像是有话没说完。"
            "说不上来是啥，反正你最近多注意点休息，也别太大意。",
            "梦见水涨得很高，漫过了脚背。醒来琢磨半天没头绪——"
            "可能就是提醒你一句：最近凡事别硬扛。",
            "做了个乱糟糟的梦，醒来就剩一个念头："
            "这两天要是有人找你帮忙，多看一眼再答应。",
        ]
        return random.choice(pool)

    # ============================================================
    # 预感信号蒸馏（通过暗盘模块获取，不再重复实现）
    # ============================================================
    async def _premonition_signal_from_diviner(self, user_id: str) -> Optional[str]:
        """从暗盘模块获取朦胧预感方向（P3 迁移到 premonition_signal 接口）。

        依赖 self.plugin.diviner（由 core 提供），兼容 diviner 不可用场景。
        """
        try:
            diviner = getattr(self.plugin, "diviner", None)
            if not diviner or not hasattr(diviner, "premonition_signal"):
                return None
            profile = await self.plugin._get_profile(user_id)
            birth = self.plugin._birth_from(profile)
            sig = await diviner.premonition_signal(user_id, birth)
            return (sig or {}).get("cue")
        except Exception as e:
            logger.debug(f"[mingbu.dream] 预感信号获取失败(退回纯氛围): {e}")
            return None

    # ============================================================
    # 预感库存管理（P3：时效与货真价实的库存）
    # ============================================================
    async def poll_premonition_stock(self, user_id: str,
                                     ttl_days: int = 3) -> Optional[str]:
        """取一条可投递的预感梦（未分享、保质期内），过期货顺手消费。

        P3 修复：预感文本自带"这两天/最近"的时效语义，但库存里可能囤着
        三天前生成的旧预感——过期才投就成了"三天前的不安今天才说"。
        超过 premonition_ttl_days 的未投预感在此直接标记已分享（清仓），
        让编排层走"现做"路径。

        返回预感文本，或 None（无货/全过期）。
        """
        if not self.memory:
            return None
        # 预感兜底是独立模板（无 PII），允许投递：allow_fallback=True
        dreams = await self._recent_dreams(user_id, n=5, tag=TAG_PREMONITION,
                                           allow_fallback=True)
        if not dreams:
            return None
        # 逐条验期（recall_recent 倒序 = 最新在前）：过期的清仓，新鲜的取最新
        fresh: List = []
        stale: List = []
        if ttl_days > 0:
            for d in dreams:
                try:
                    age = now_bj() - datetime.fromisoformat(d.created_at)
                    (stale if age.days >= ttl_days else fresh).append(d)
                except Exception:
                    fresh.append(d)  # 时间解析失败按新鲜处理（保守，不清仓）
        else:
            fresh = list(dreams)
        if stale:
            for d in stale:
                try:
                    await self.memory.add_tag(user_id, d.id, TAG_SHARED)
                except Exception as e:
                    logger.debug(f"[mingbu.dream] 过期预感清仓失败(忽略): {e}")
            if self.stats:
                await self.stats.incr("stale_purged", len(stale))
            logger.info(
                f"[mingbu.dream] {user_id} 预感库存过期清仓 {len(stale)} 条"
                f"（TTL={ttl_days} 天）"
            )
        return fresh[0].content if fresh else None

    # ============================================================
    # 暗盘洞察 → 梦境意象（周期性引用，隐喻化表达）
    # ============================================================
    async def _dark_insight_symbols(self, user_id: str) -> Optional[str]:
        """每 3 天左右从暗盘模块获取深度洞察，转化为梦境意象。

        设计要点：
        - 周期性：day % 3 == 0 时触发，避免每夜都调用（节省 API 也保持自然）
        - 只取"方向感"不取具体命理结果：返回值是"需要留意的领域/情绪"
          的朦胧描述，不含任何命理术语
        - 由 nightly_consolidate 的 LLM 以意象化方式织入梦叙事，
          不直接流露洞察原文
        - 暗盘模块不可用或不满足周期条件时返回 None
        """
        # 周期条件：day % 3 == 0（约每 3 天一次）
        today = now_bj().day
        if today % 3 != 0:
            return None

        try:
            diviner = getattr(self.plugin, "diviner", None)
            if not diviner or not hasattr(diviner, "get_insights"):
                return None

            profile = await self.plugin._get_profile(user_id)
            birth = self.plugin._birth_from(profile)
            if not birth:
                return None

            # 获取全量洞察（暗盘模块内部有 6 小时缓存，不会频繁打 API）
            insights = await diviner.get_insights(user_id, birth, "")
            if not insights:
                return None

            # 洞察已经不含命理术语（暗盘模块内部保证）
            # 直接作为"深层感觉"传递给 LLM，让梦境以意象化方式表达
            return insights
        except Exception as e:
            logger.debug(f"[mingbu.dream] 暗盘洞察→梦境意象失败(跳过): {e}")
            return None

    async def consume_latest_premonition(self, user_id: str):
        """标记最新一条预感梦为已消费，避免主动消息重复投递同一条。

        修复：proactive 在'无存货时新生成预感'的路径不会经过 maybe_premonition
        的消费逻辑，导致该条 10 天冷却后会被原样重说（回声 bug）。
        """
        if not self.memory:
            return
        dreams = await self._recent_dreams(user_id, n=1, tag=TAG_PREMONITION)
        if dreams:
            try:
                # 2026-09-22（P1）：真·消费标记（替代原先冒充消费的 consolidate）
                await self.memory.add_tag(user_id, dreams[0].id, TAG_SHARED)
            except Exception as e:
                logger.debug(f"[mingbu.dream] 预感标记消费失败(忽略): {e}")

    # ============================================================
    # 主动消息用的"取梦"
    # ============================================================
    async def get_dreams(self, user_id: str, n: int = 3) -> List[str]:
        if not self.memory:
            return []
        dreams = await self._recent_dreams(user_id, n=n)
        return [m.content for m in dreams]

    async def maybe_share_dream(self, user_id: str) -> Optional[str]:
        """主动消息时偶发分享一条'夜梦'（普通记忆梦）。

        2026-09-22（P1）：取到即打 TAG_SHARED 真·消费标记；已分享与兜底梦
        在 _recent_dreams 中被排除，同一梦不会被重复投递。
        """
        if not self.memory:
            return None
        dreams = await self._recent_dreams(user_id, n=2, tag=TAG_NIGHT_DREAM)
        if dreams:
            d = dreams[0]
            try:
                await self.memory.add_tag(user_id, d.id, TAG_SHARED)
            except Exception as e:
                logger.debug(f"[mingbu.dream] 夜梦标记已分享失败(忽略): {e}")
            return d.content
        return None

    async def maybe_premonition(self, user_id: str) -> Optional[str]:
        """主动消息时，偶发交付一条'预感梦'（暗盘外显）。

        P3：主编排改用 poll_premonition_stock（带 TTL 清仓）；本方法保留
        兼容旧调用点，并同步放行预感兜底（独立模板无 PII）。
        概率与冷却由调用方（proactive）控制；取到即打 TAG_SHARED 真·消费标记。
        """
        if not self.memory:
            return None
        dreams = await self._recent_dreams(user_id, n=2, tag=TAG_PREMONITION,
                                           allow_fallback=True)
        if dreams:
            d = dreams[0]
            try:
                await self.memory.add_tag(user_id, d.id, TAG_SHARED)
            except Exception as e:
                logger.debug(f"[mingbu.dream] 预感标记已分享失败(忽略): {e}")
            return d.content
        return None
