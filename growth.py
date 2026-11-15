"""
growth.py -- 自我成长 / 人生叙事 / 反思系统
============================================
参考自 self_evolution 的 reflection + profile：
  - LLM 回顾对话 → 抽取 explicit_facts / self_correction / cognitive_bias
  - 写入用户画像(category="user_profile")
  - 每日批处理生成"日报"、更新活跃用户画像

本模块让 bot 随交互"长大"：
  - reflect()：定期反思与某用户的对话，沉淀用户画像 + 自身成长里程碑
  - life_arc()：维护一段"关于这个人的理解"的叙事弧
  - milestones：可被主动消息引用的成长故事
"""
from __future__ import annotations

import json
from typing import Any, Dict, List

from astrbot.api import logger

from .memory_core import DREAM, EMOTIONAL, EPISODIC, GROWTH, SEMANTIC, THOUGHT, MemoryCore


class GrowthEngine:
    def __init__(self, plugin, memory: MemoryCore):
        self.plugin = plugin
        self.memory = memory

    async def reflect(self, user_id: str) -> Dict[str, Any]:
        """反思近期对话，沉淀用户画像 + 成长里程碑。返回摘要。"""
        if not self.memory:
            return {"profile": "", "milestone": ""}
        # 2026-09-22（P1）：反思语料排除生成型记忆（梦/成长/念头）。
        # 反思应基于真实互动，而不是自己昨天的产出；生产取证显示成长反思
        # 与夜梦同源地被旧梦/模板文本污染。
        recent = await self.memory.sample_recent(
            user_id, n=16, exclude_types={DREAM, GROWTH, THOUGHT}
        )

        # 2026-09-22（P2 触发条件化）：真实互动素材不足则跳过反思，省一次
        # LLM 调用（成长不应在空转时反复重写画像）；0=关闭该闸。
        try:
            min_new = int((getattr(self.plugin, "_config", {}) or {}).get(
                "growth_min_new_memories", 2) or 0)
        except Exception:
            min_new = 2
        if min_new > 0:
            new_cnt = await self.memory.new_count_since(
                user_id, GROWTH, count_types={EPISODIC, EMOTIONAL}
            )
            if new_cnt < min_new:
                logger.info(
                    f"[mingbu.growth] {user_id} 新素材 {new_cnt} < 阈值 {min_new}，跳过反思"
                )
                return {"profile": "", "milestone": "", "skipped": True}
        if not recent:
            return {"profile": "", "milestone": ""}

        corpus = "\n".join(
            f"- {m.content[:80]}" for m in recent
        )

        prompt = (
            "下面是你和某个用户的近期对话片段。请做两件事：\n"
            "1) 用 2-3 句话总结'你逐渐理解了的这个人是谁'（只写事实性观察，不写评价）。\n"
            "2) 用 1 句话写一条'你自己的成长觉察'（你作为陪伴者学会了什么）。\n"
            "严格按如下 JSON 输出，不要多余文字：\n"
            '{"profile": "...", "milestone": "..."}\n\n'
            f"{corpus}"
        )

        raw = await self.plugin._llm_text(prompt, task_type="growth")
        parsed = self._parse_json(raw)

        profile = parsed.get("profile", "").strip()
        milestone = parsed.get("milestone", "").strip()

        if not profile and not milestone:
            # 兜底：从语义记忆聚合
            profile = await self._fallback_profile(user_id)

        if profile:
            await self.memory.add(
                user_id, SEMANTIC, f"[画像] {profile}",
                source="reflection", importance=0.7, tags=["画像"],
            )
        if milestone:
            await self.memory.add(
                user_id, GROWTH, milestone,
                source="reflection", importance=0.6, tags=["成长"],
            )

        # 2026-09-22（P4 成长池制）：画像/里程碑硬预算——生产实测堆积
        # 画像 565 + 里程碑 451 = 1016 条，占全库 42%，且旧画像（importance
        # 0.7 高分）会回流进反思语料（sample_recent 不排 SEMANTIC），
        # 形成"画像写画像"的自我强化污染。
        try:
            await self._trim_growth_pool(user_id)
        except Exception as e:
            logger.warning(f"[mingbu.growth] {user_id} 成长池清理失败(忽略): {e}")

        logger.info(f"[mingbu.growth] {user_id} 反思完成")
        return {"profile": profile, "milestone": milestone}

    def _cfg_int(self, key: str, default: int) -> int:
        """安全读取 int 配置。"""
        try:
            cfg = getattr(self.plugin, "_config", {}) or {}
            return int(cfg.get(key, default) or 0)
        except Exception:
            return default

    async def _trim_growth_pool(self, user_id: str) -> int:
        """成长池硬预算（2026-09-22 P4，与 P2 梦池制同构）。

        - 画像池 growth_profile_pool_size（默认 1）：画像本质是"当前快照"，
          每用户只留最新 N 条，旧的删除（陈旧画像无保留价值且污染语料）；
        - 里程碑池 growth_milestone_pool_size（默认 8）：成长流水留最近 N 条。
        返回删除条数。
        """
        if not self.memory:
            return 0
        removed = 0

        # 1) 画像池：只留最新 N 条
        profile_pool = self._cfg_int("growth_profile_pool_size", 1)
        if profile_pool > 0:
            profiles = await self.memory.recall_recent(
                user_id, n=400, mtype=SEMANTIC)
            profiles = [
                m for m in profiles
                if "画像" in (m.tags or [])
                and (m.content or "").startswith("[画像]")
            ]
            if len(profiles) > profile_pool:
                # recall_recent 倒序 = 最新在前；超出池容的删最旧
                for m in profiles[profile_pool:]:
                    if await self.memory.remove(user_id, m.id):
                        removed += 1

        # 2) 里程碑池：只留最近 N 条
        mile_pool = self._cfg_int("growth_milestone_pool_size", 8)
        if mile_pool > 0:
            miles = await self.memory.recall_recent(user_id, n=400, mtype=GROWTH)
            if len(miles) > mile_pool:
                for m in miles[mile_pool:]:
                    if await self.memory.remove(user_id, m.id):
                        removed += 1

        if removed:
            logger.info(
                f"[mingbu.growth] {user_id} 成长池清理：删除 {removed} 条"
                f"（画像池={profile_pool}，里程碑池={mile_pool}）"
            )
        return removed

    @staticmethod
    def _parse_json(text: str) -> Dict[str, Any]:
        if not text:
            return {}
        try:
            s = text.strip()
            if s.startswith("```"):
                s = s.strip("`")
                s = s[s.find("{") : s.rfind("}") + 1]
            return json.loads(s)
        except Exception as e:
            logger.debug(f"[mingbu.growth] 解析反思 JSON 失败(用兜底): {e}")
            return {}

    async def _fallback_profile(self, user_id: str) -> str:
        sems = await self.memory.recall_recent(user_id, n=8, mtype=SEMANTIC)
        if not sems:
            return ""
        return "；".join(m.content[:40] for m in sems[:4])

    async def life_arc(self, user_id: str) -> str:
        """返回一段关于这个人的'理解叙事'（用于主动关怀时引用）。"""
        if not self.memory:
            return ""
        sems = await self.memory.recall_important(user_id, n=5, min_weight=0.4)
        growth = await self.memory.recall_recent(user_id, n=3, mtype=GROWTH)
        if not sems and not growth:
            return ""
        bits = [m.content for m in (sems + growth)][:5]
        return "。".join(bits)

    async def milestones(self, user_id: str, n: int = 3) -> List[str]:
        if not self.memory:
            return []
        gs = await self.memory.recall_recent(user_id, n=n, mtype=GROWTH)
        return [m.content for m in gs]
