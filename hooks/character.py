"""
hooks/character.py -- 角色收集玩法 + 羁绊事件注入

收集玩法用户感知改造（2026-09-07）：
  此前解锁/羁绊升级只 ``inject_text_part`` —— 把事件塞进 LLM 上下文，
  用户能不能看到取决于模型有没有把话自然说出口，等于开发者自嗨、
  用户 0 感知。现在事件改为**确定性直发**一条用户可见消息
  （角色卡/升级提示），企微走 proactive 直连通道，其余平台走
  context.send_message；直发通道不可用时才降级回注入 LLM 兜底。
"""
from ..bond import detect_bond_event
from ..character_pool import VISIBLE_PRESETS
from ..util import inject_text_part
from .base import HookProcessor

try:
    from astrbot.api import logger as _logger
except Exception:  # noqa: BLE001
    _logger = None


def _log(level: str, msg: str):
    if _logger is None:
        return
    try:
        getattr(_logger, level)(f"[mingbu.character] {msg}")
    except Exception:  # noqa: BLE001
        pass


async def _direct_notify(core, event, uid, text, source="collection") -> bool:
    """向用户确定性直发一条角色玩法事件消息（解锁/羁绊升级/隐藏角色）。

    - 企微客服：proactive 引擎的 ``send_direct_text`` 走 client.kf_message
      直连（官方 48h 窗口内允许主动下发，绕过 AstrBot send_by_session 护栏）；
    - 其它平台：同一方法落到 context.send_message；
    - 无 proactive 引擎（用户关掉主动关怀）或发送失败：返回 False，
      由调用方决定是否注入 req 兜底。任何异常都吞掉，绝不影响主链路。

    Returns:
        bool: 是否真的把消息发给了用户。
    """
    if not text or not uid:
        return False
    session_id = (getattr(event, "unified_msg_origin", None)
                  or getattr(event, "session_id", None) or "")
    if not session_id:
        return False
    proactive = getattr(core, "proactive", None)
    if proactive is None:
        return False
    try:
        return await proactive.send_direct_text(uid, session_id, text, source=source)
    except Exception as e:  # noqa: BLE001 - 直发失败不能阻断对话
        _log("debug", f"玩法事件直发失败(忽略, 将回退注入): {e}")
        return False


class CharacterHook(HookProcessor):
    """角色收集玩法：隐藏解锁 + 好感度推荐 + 羁绊事件（亲密/受伤/想静静/安慰/道歉）。"""

    async def process(self, core, event, req, **kwargs):
        is_private = kwargs.get("is_private")
        uid = event.get_sender_id()
        msg = kwargs.get("message_text")
        if not (core.character_collection and is_private):
            return True

        await core.character_collection.record_interaction(uid)

        # 当前角色（每用户）→ 用于羁绊读写与推荐阈值
        char_id = None
        try:
            coll = await core.character_collection.get_user_collection(uid)
            char_id = coll.get("current")
        except Exception:
            char_id = None

        if msg:
            # 隐藏角色台词解锁 → 直发隐藏角色卡；直发失败降级注入 req
            res = await core.character_collection.check_hidden_unlock(uid, msg)
            if res and res.get("ok"):
                ch = res.get("character") or {}
                ch_name = ch.get("name", "") if isinstance(ch, dict) else ""
                ch_avatar = ch.get("avatar", "👤") if isinstance(ch, dict) else "👤"
                card = (
                    f"✨ 你解锁了隐藏角色「{ch_avatar}{ch_name}」！\n"
                    f"{res.get('message', '')}"
                )
                ok = await _direct_notify(core, event, uid, card,
                                          source="hidden_unlock")
                if ok:
                    # 隐藏角色被识破是有剧情分量的一刻：即使直发成功也注入一条
                    # 导演提示，让角色顺承「第七人现身」的当下自然回应，
                    # 而不是机械复述卡片文案。
                    inject_text_part(
                        req,
                        "（此刻你心里隐隐知道：那个一直没露面的第七位朋友，"
                        "终于被 TA 说破了。顺着这个时刻自然回应 TA，"
                        "别复述刚发出去的那张卡。）",
                    )
                else:
                    inject_text_part(req, res.get('message', ''))

            # 羁绊事件检测（道歉/想静静/受伤/安慰/亲密）→ 当前角色羁绊 nudge
            if char_id:
                kind = detect_bond_event(msg)
                if kind:
                    bond = await core._get_bond(uid, char_id)
                    before_title = bond.title()[0]
                    bond.nudge(kind)
                    await core._save_bond(bond)
                    after_title = bond.title()[0]
                    # 仅在羁绊称号升级时提示（避免每条消息都刷「羁绊加深」）
                    if after_title != before_title and kind in ("intimacy", "comfort", "apology"):
                        text = (
                            f"💞 你和「{after_title}」的羁绊又深了一层，"
                            f"你们的关系更亲近了。"
                        )
                        ok = await _direct_notify(core, event, uid, text,
                                                  source="bond_title")
                        if not ok:
                            inject_text_part(
                                req,
                                f"你们之间的羁绊升到了「{after_title}」，你心里多了一分亲近。",
                            )

        # 角色推荐解锁：用当前角色的羁绊亲密度（无角色时回退用户整体关系）
        if char_id:
            bond = await core._get_bond(uid, char_id)
            score = bond.score
        else:
            rel = await core._get_relationship(uid)
            score = rel.score
        res = await core.character_collection.check_recommend(uid, score)
        if res and res.get("ok"):
            card = (
                f"🎉 你解锁了新角色「{res.get('character_avatar', '')}"
                f"{res.get('character_name', '')}」！\n"
                f"想看看还差谁，直接问我「图鉴」「都有谁」就行；"
                f"想换 TA 来陪你，说「切换」也可以。"
            )
            ok = await _direct_notify(core, event, uid, card,
                                      source="recommend_unlock")
            if not ok:
                inject_text_part(
                    req,
                    f"你注意到有一个新面孔「{res.get('character_avatar', '')} {res.get('character_name', '')}」正慢慢走近你们的生活。",
                )
            # ---- 第 6 位集齐 → 彩蛋线索（B 通道，只发一次）----
            # 集齐全部可见角色后，直发第七人的彩蛋线索，引导用户用自然语言
            # 问出触发句；是否发过记录在 KV（hidden_clue_sent），钩子保持无状态。
            if res.get("unlocked_count", 0) == len(VISIBLE_PRESETS):
                try:
                    coll2 = await core.character_collection.get_user_collection(uid)
                except Exception:
                    coll2 = {}
                if not coll2.get("hidden_unlocked") and not coll2.get("hidden_clue_sent"):
                    clue = (
                        "……六个人，都到齐了。可你发现了吗——总觉得还缺一个位子。\n"
                        "传说里那第七位，一直藏着没露面。\n"
                        "你若真想见 TA，就开口问我一句："
                        "『你是不是还瞒着我什么？』"
                        "——说不定，还真能问出点什么。"
                    )
                    await _direct_notify(core, event, uid, clue,
                                         source="hidden_clue")
                    # 无论直发成败都只记一次，避免每轮重试刷屏；
                    # 失败时用户仍可经 A 通道（自然语料打听第七人）解锁。
                    try:
                        await core.character_collection.mark_hidden_clue_sent(uid)
                    except Exception:
                        pass
        return True
