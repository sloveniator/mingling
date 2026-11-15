"""
star_mixins/health.py -- 健康档案存取
=======================================
健康档案存取。

本模块由 ``MingbuStar`` 混入。方法体内的 ``self.<engine>`` 访问依赖
``MingbuStar.__getattr__`` 代理到 ``MingbuCore``，因此 mixin 本身不 import
``core``，也就不产生循环依赖。
"""
import json

from astrbot.api import logger

from ..health import HealthProfile


class HealthProfileMixin:
    """健康档案存取。"""

    async def _get_health_profile(self, uid: str) -> HealthProfile:
        try:
            raw = await self.get_kv_data(f"mingbu.health:{uid}", None)
            return HealthProfile.from_dict(json.loads(raw) if raw else {})
        except Exception as e:
            logger.debug(f"[mingbu] 读取健康档案失败(已忽略): {e}")
            return HealthProfile()

    async def _save_health_profile(self, uid: str, profile: HealthProfile):
        try:
            await self.put_kv_data(
                f"mingbu.health:{uid}",
                json.dumps(profile.to_dict(), ensure_ascii=False),
            )
        except Exception as e:
            logger.warning(f"[mingbu] 健康档案保存失败(已忽略): {e}")
