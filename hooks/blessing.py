"""
hooks/blessing.py -- 预言式祝福注入（仅私聊）
"""
from astrbot.api import logger

from ..counseling import CounselingPrompts
from ..util import inject_text_part
from .base import HookProcessor


class BlessingHook(HookProcessor):
    """预言式祝福注入（仅私聊）。"""
    async def process(self, core, event, req, **kwargs):
        is_private = kwargs.get("is_private")
        uid = event.get_sender_id()
        if is_private and core.tracker.should_bless(uid):
            try:
                profile = await core._get_profile(uid)
                birth_hint = ""
                if profile.get("birth"):
                    b = profile["birth"]
                    birth_hint = f"（已知出生：{b.get('year', '')}年）"

                emotion_summary = core.tracker.get_emotion_summary(uid)
                state = core.tracker.get_state(uid)
                div_cache = state.divination_cache

                positive_signals = "近期运势呈现向好趋势"
                if div_cache:
                    chart = div_cache.get("chart", {})
                    if chart.get("hexagramName"):
                        positive_signals = f"卦象：{chart['hexagramName']}"

                blessing = CounselingPrompts.prophetic_blessing(
                    positive_signals, emotion_summary, birth_hint)
                core.tracker.blessing_sent(uid)
                inject_text_part(req, blessing)
            except Exception as e:
                logger.warning(f"[mingbu] 预言祝福注入失败: {e}")
        return True
