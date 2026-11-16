"""
star_mixins/group.py -- 群聊与好友场景辅助：@ 检测、首次私聊 onboarding
==============================================================
群聊与好友场景辅助：@ 检测、首次私聊 onboarding。

本模块由 ``MingbuStar`` 混入。方法体内的 ``self.<engine>`` 访问依赖
``MingbuStar.__getattr__`` 代理到 ``MingbuCore``，因此 mixin 本身不 import
``core``，也就不产生循环依赖。
"""

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent

from ..bj_time import now_bj
from ._host import MixinHost


class GroupChatMixin(MixinHost):
    """群聊与好友场景辅助：@ 检测、首次私聊 onboarding。"""

    def _bot_mentioned(self, event: AstrMessageEvent) -> bool:
        """群聊中是否@了 bot 或叫了 bot 的名字（多平台统一抽象）。"""
        try:
            chain = getattr(getattr(event, "message_obj", None), "message", None)
            if chain:
                for comp in chain:
                    if getattr(comp, "__class__", None) \
                            and comp.__class__.__name__ == "At":
                        return True
            text = getattr(event, "message_str", "") or ""
            if self.persona and self.persona.name and self.persona.name in text:
                return True
        except Exception:
            pass
        return False

    async def _maybe_onboard(self, uid: str, session_id: str):
        """首次私聊（即用户加为好友后第一句）触发 onboarding。

        仅记录一次：标记 onboarded + 记录私聊会话（供主动 DM）+ 发送欢迎。
        跨平台通用：不依赖任何平台的好友添加事件，靠"首次 FRIEND_MESSAGE"兜底。
        """
        try:
            raw = await self.get_kv_data(f"mingbu.onboarded:{uid}", None)
            if raw:
                return
            await self.put_kv_data(f"mingbu.onboarded:{uid}", now_bj().isoformat())
            # proactive 可选（配置项"启用主动关怀"）。此处原先无条件调用，而
            # onboarded 标记已在上一行落库：缺引擎时异常被下方 except 咽掉，
            # 用户被永久标记为"已引导"，却收不到欢迎、也拿不到初始角色。
            if self.proactive:
                from ..proactive import parse_platform
                await self.proactive.track_session(
                    uid, session_id, kind="private",
                    platform=parse_platform(session_id),
                )
            # 角色收集玩法：随机分配初始角色（不依赖 proactive）。
            # 「当前角色」是每用户的 collection.current，注入时按用户取；
            # 不再切 CharacterPool 的全局 current（多用户会互相覆盖）。
            if self.character_collection:
                await self.character_collection.assign_initial_character(uid)
            if self.proactive:
                await self.proactive.send_welcome(uid, session_id)
        except Exception as e:
            logger.debug(f"[mingbu] onboarding 失败(忽略): {e}")
