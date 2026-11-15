"""
hooks/counseling.py -- 心理咨询钩子 + 静默洞察 + 后台关注注记
"""
from astrbot.api import logger

from ..background_watch import weave_watch_note
from ..counseling import CounselingPrompts
from ..metrics import record_failure
from ..silent_divination import weave_insights_into_prompt
from ..util import inject_text_part
from .base import HookProcessor


class CounselingHook(HookProcessor):
    """心理咨询钩子 + 静默洞察 + 后台关注注记。"""
    async def process(self, core, event, req, **kwargs):
        is_private = kwargs.get("is_private")
        is_group = kwargs.get("is_group")
        mentioned = kwargs.get("mentioned")
        uid = event.get_sender_id()
        user_is_divining = kwargs.get("user_is_divining", False)
        crisis = kwargs.get("crisis")

        if (is_private or (is_group and mentioned)) and not user_is_divining \
                and core.tracker.should_hook(uid):
            try:
                profile = await core._get_profile(uid)
                birth = core._birth_from(profile)
                emotion = core.tracker.get_emotion_summary(uid)

                insights = None
                if core._config.get("darkchannel_enabled", True) \
                        and not (crisis and isinstance(crisis, dict) and crisis.get("level") == "action"):
                    insights = await core.diviner.get_insights(uid, birth, emotion)

                hook_text = CounselingPrompts.hook_injection(uid, core.tracker, profile)
                core.tracker.hook_presented(uid)

                if insights:
                    hook_text += "\n" + weave_insights_into_prompt(insights)

                if core._config.get("background_watch_enabled", True) and core.background_watch:
                    try:
                        watch_note = await core.background_watch.note_for(uid)
                        if watch_note:
                            hook_text += "\n" + weave_watch_note(watch_note)
                    except Exception as e:
                        logger.debug(f"[mingbu] 后台关注注记注入失败(已忽略): {e}", exc_info=True)
                        record_failure("watch_note_inject")

                inject_text_part(req, hook_text)
            except Exception as e:
                logger.warning(f"[mingbu] 心理咨询钩子注入失败: {e}")

        if user_is_divining:
            logger.info(f"[mingbu] 用户已在聊命理，跳过钩子 -> {uid}")
        return True
