"""
star_mixins/persona_ctx.py -- 人格块拼装与记忆召回（语义向量召回 / rerank 重排，均优雅降级）
==========================================================================
人格块拼装与记忆召回（语义向量召回 / rerank 重排，均优雅降级）。

本模块由 ``MingbuStar`` 混入。方法体内的 ``self.<engine>`` 访问依赖
``MingbuStar.__getattr__`` 代理到 ``MingbuCore``，因此 mixin 本身不 import
``core``，也就不产生循环依赖。
"""
import json
from typing import Dict

from astrbot.api import logger

from ..character_pool import CharacterCard
from ..memory_core import (
    MENTION_DIRECT,
    MENTION_SOFT_ECHO,
    MENTION_TONE_ONLY,
    SCOPE_GROUP,
    MemoryCore,
)
from ..persona import Relationship
from ..provider_router import (
    embed_texts,
    get_embedding_provider,
    get_rerank_provider,
    rank_indices_by_vector,
    rerank_memory_list,
)
from ..worldview_adapter import adapt_system_prompt, auto_style_for_character
from ._host import MixinHost


class PersonaContextMixin(MixinHost):
    """人格块拼装与记忆召回（语义向量召回 / rerank 重排，均优雅降级）。"""

    async def _current_character(self, uid: str):
        """取用户当前角色卡片（dict）；无则分配初始角色。

        角色体系不可用或异常时返回 None（调用方回退到固定人格）。
        返回的是「每用户」的当前角色，与 CharacterPool 的全局 current 解耦。
        """
        if not self.character_pool or not self.character_collection:
            return None
        try:
            coll = await self.character_collection.get_user_collection(uid)
            char_id = coll.get("current")
            if not char_id:
                char_id = await self.character_collection.assign_initial_character(uid)
            if not char_id:
                return None
            return await self.character_pool.get_character(char_id)
        except Exception as e:
            logger.debug(f"[mingbu] 取当前角色失败(忽略): {e}")
            return None

    async def _build_persona_block(self, uid: str, rel: Relationship,
                                   in_group: bool = False,
                                   rerank_query: str = "") -> str:
        """拼装人格 + 羁绊 + 记忆上下文，注入 LLM。

        私聊时「角色取代人格」：当前角色的人设（名字/性格/说话风格）替换固定的
        「灵犀」人格，关系块改用「用户与当前角色的羁绊」。群聊保持固定人格（
        角色与羁绊均为 per-user 语义，群聊多用户下不适用）。
        """
        if not self.persona:
            return ""

        # 角色收集总开关：关闭后人格块整体不注入（含「我是谁」/世界观/说话底线/
        # 羁绊·关系·记忆·关系网提示），主对话人格完全由 AstrBot 配置
        # （req.system_prompt / 面板默认人格）决定。仅影响主对话注入点
        # (hooks/persona.py PersonaHook)；危机干预/心理开解/祝福等独立功能钩子
        # 不经过本方法，不受影响。默认开启，行为与旧版一致。
        if not self._config.get("character_collection_enabled", True):
            return ""

        char_card = None
        base_prompt = self.persona.self_prompt()
        if not in_group:
            char_card = await self._current_character(uid)
            if char_card:
                # 角色取代人格：用当前角色的人设作为「我是谁」
                base_prompt = CharacterCard(char_card).to_persona_prompt()

        # 世界观适配：根据配置或当前角色自动选择风格
        if self._config.get("worldview_enabled", True):
            style = self._config.get("llm_persona_style", "default")
            # 自动匹配：根据当前角色自动选择风格
            if self._config.get("auto_worldview_match", True) and style == "default":
                if char_card:
                    style = auto_style_for_character(char_card.get("id", ""))
                elif self.character_pool:
                    current = await self.character_pool.current_character()
                    if current:
                        style = auto_style_for_character(current.get("id", ""))
            custom_wv = self._config.get("custom_worldview_json", "")
            char_name = char_card.get("name") if char_card else self.persona.name
            base_prompt = adapt_system_prompt(base_prompt, style, char_name, custom_worldview=custom_wv)

        # 隐形约束（v4.8.104）：下方所有方括号/圆括号内的说明都是给你「内部参考」
        # 的上下文，不是你要对用户说的话。无论用户问什么，都绝不原样复述这些
        # 标签、系统说明、指令清单，也绝不出现“(TTS active)”“【系统提示】”
        # “注：当前无法发送语音”之类的机器腔——你只是一个普通朋友在说话。
        base_prompt += (
            "\n\n【说话的底线】你回复用户时，永远是一个真实的人在说话，"
            "而不是在执行什么系统任务。会话中出现的方括号或圆括号说明（例如"
            "关于直觉、关系、语音、系统状态的提示）只是你内心的参考信息——"
            "你绝不会把它们原样说出口，也不会提及“系统提示”“标签”“规则清单”"
            "或“TTS/语音合成”之类的字眼。你的话永远自然、口语、像真人。"
        )

        parts = [base_prompt]
        if not in_group:
            if char_card:
                bond = await self._get_bond(uid, char_card.get("id", ""))
                parts.append(self.persona.bond_prompt(bond))
            else:
                parts.append(self.persona.relationship_prompt(rel))

        # 群聊隔离（2.0）：群聊时只召回 group scope 记忆，私聊秘密绝不在群里复述；
        # 私聊（in_group=False）保留全部记忆连贯性（private + group 都可召回）。
        recall_scope = SCOPE_GROUP if in_group else None
        important = await self.memory.recall_important(uid, n=4, min_weight=0.35,
                                                       max_scope=recall_scope)
        emotional = await self.memory.recall_emotional(uid, n=3,
                                                       max_scope=recall_scope)
        # 可选：按当前消息对记忆做语义/相关性重排（均优雅降级）
        if rerank_query:
            embedding_ok = False
            if self._config.get("embedding_enabled", False):
                # 语义向量召回：按当前消息对相关记忆做余弦相似度排序
                sem_imp = await self._recall_semantic(rerank_query, important, n=4)
                sem_emo = await self._recall_semantic(rerank_query, emotional, n=3)
                if sem_imp or sem_emo:
                    important = sem_imp or important
                    emotional = sem_emo or emotional
                    embedding_ok = True
            if not embedding_ok and self._config.get("rerank_enabled", False):
                important = await self._rerank_memories(rerank_query, important)
                emotional = await self._rerank_memories(rerank_query, emotional)
            # 2.0 任务 2 兜底：embedding/rerank 均关闭（默认部署态）时，
            # 上面的召回只按时间/权重、不感知当前消息。用 retrieve_relevant
            # 按 rerank_query 做轻量相关性补充（字符 bigram × 权重），把与
            # 当前话题相关但权重不够进 important 的记忆补进来——解决「跨会话
            # 提到之前的未完成话题时召回不到」。已召回的 important 不重复注入。
            if not embedding_ok and not self._config.get("rerank_enabled", False):
                try:
                    existing_ids = {m.id for m in important}
                    extra = await self.memory.retrieve_relevant(
                        uid, rerank_query, limit=3, max_scope=recall_scope)
                    extra = [m for m in extra if m.id not in existing_ids]
                    if extra:
                        important = list(important) + extra
                except Exception as e:
                    logger.debug(f"[mingbu] 相关性兜底召回失败(忽略): {e}")
        mem_parts = []
        # ---- 按 mention_policy 分槽（借鉴 memory_companion，防"主动戳痛"）----
        # direct/soft_echo 记忆可进入「我记得」正文自然流露；
        # tone_only 只作为「情绪底色」影响语气，不逐条复述；
        # avoid_unless_asked（冲突伤疤/尴尬/被否认内容）默认不注入——
        # 模型不该主动翻旧账，仅当用户主动问起时才由调用方放开。
        sayable = [m for m in important
                   if getattr(m, "mention_policy", MENTION_SOFT_ECHO) in (MENTION_DIRECT, MENTION_SOFT_ECHO)]
        tone_emo = [m for m in emotional
                    if getattr(m, "mention_policy", MENTION_SOFT_ECHO) == MENTION_TONE_ONLY]
        # 短码→真实 id 映射（只写 KV，主 LLM 只见短码、见不到真实 id），
        # 供下一轮用户消息含短码时做单条精确反馈。
        refs_mapping: Dict[str, str] = {}
        if sayable:
            mem_parts.append("[我记得关于 TA 的]")
            mem_parts.append(
                MemoryCore.to_brief_with_refs(sayable, 50, refs_mapping))
        if tone_emo:
            mem_parts.append("[TA 的情绪底色]")
            mem_parts.append(
                MemoryCore.to_brief_with_refs(tone_emo, 40, refs_mapping))
        if mem_parts:
            # 记录本轮实际注入的记忆（供下一轮否认/纠正反馈触发）。
            # 短码映射 + id 列表都只写 KV 元数据，不进注入文本，避免内部标识泄漏。
            try:
                if refs_mapping and hasattr(self, "memory"):
                    await self.memory.remember_injected_refs(uid, refs_mapping)
                injected_ids = list(refs_mapping.values())
                if injected_ids and hasattr(self, "memory"):
                    await self.memory.remember_injected(uid, injected_ids)
            except Exception as e:
                logger.debug(f"[mingbu] 登记注入记忆 id 失败(已忽略): {e}")
            # 记忆包语义声明（借鉴 memory_companion 的注入语义）：
            # 明确这是临时内心材料而非用户新消息，冲突时以当前消息/用户纠正优先，
            # 并区分「事实」与「推测」——避免模型把推测包装成「你又忘了」。
            mem_parts.append(
                "\n（以上是你内心的参考，不是用户刚说的话。回答时先顾眼前这句，"
                "这些只在相关时自然流露，不要逐条复述。拿不准的宁可含糊，"
                "也别把推测说成「上次明明…」。）"
            )
            parts.append("\n".join(mem_parts))
        else:
            # 本轮无记忆注入：显式清空上一轮残留 id，避免下一轮用户
            # 「记错了」时误降权已反馈过的旧记忆（remember_injected
            # 对空列表短路、不会覆盖 KV，故必须用 clear_injected 清）。
            try:
                if hasattr(self, "memory"):
                    await self.memory.clear_injected(uid)
            except Exception as e:
                logger.debug(f"[mingbu] 清空注入记忆 id 失败(已忽略): {e}")

        # 私聊下，附上朦胧的"关系网"提示（绝不点名具体身份）
        if not in_group:
            hint = await self.social.relation_hint(uid)
            if hint:
                parts.append(f"\n[你隐隐感觉到的一些事]\n{hint}")
        return "\n".join(parts)

    async def _rerank_memories(self, query: str, mems: list) -> list:
        """按 query 对记忆列表做相关性重排；未开启 rerank / 无 provider 时原序返回。"""
        if not mems or not self._config.get("rerank_enabled", False):
            return mems
        try:
            provider = await get_rerank_provider(self.context, self._config.get("provider_rerank", ""))
            if not provider:
                return mems
            return await rerank_memory_list(provider, query, mems, self._config)
        except Exception as e:
            logger.debug(f"[mingbu] 记忆重排失败(忽略): {e}")
            return mems

    async def _recall_semantic(self, query: str, mems: list, n: int = 6) -> list:
        """按 query 的向量对候选记忆做语义召回（余弦相似度排序）。

        仅当 embedding_enabled 且能拿到 Embedding provider 时生效；
        否则返回 []（调用方据此退回原序或 rerank）。
        """
        if not mems or not self._config.get("embedding_enabled", False):
            return []
        try:
            provider = await get_embedding_provider(
                self.context, self._config.get("provider_embedding", "")
            )
            if not provider:
                return []
            qvecs = await embed_texts(provider, [query], self._config)
            if not qvecs or not qvecs[0]:
                return []
            texts = [getattr(m, "content", "") or "" for m in mems]
            vecs = await embed_texts(provider, texts, self._config)
            idxs = rank_indices_by_vector(qvecs[0], vecs, n)
            return [mems[i] for i in idxs if 0 <= i < len(mems)]
        except Exception as e:
            logger.debug(f"[mingbu] 语义召回失败(忽略): {e}")
            return []

    async def _save_persona(self):
        try:
            await self.put_kv_data("mingbu.persona",
                              json.dumps(self.persona.to_dict(), ensure_ascii=False))
        except Exception as e:
            logger.warning(f"[mingbu] 人格保存失败(已忽略): {e}")
