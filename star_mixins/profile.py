"""
star_mixins/profile.py -- 用户档案与出生信息：读写、参数归一、方法推断、合法性校验
==============================================================
用户档案与出生信息：读写、参数归一、方法推断、合法性校验。

本模块由 ``MingbuStar`` 混入。方法体内的 ``self.<engine>`` 访问依赖
``MingbuStar.__getattr__`` 代理到 ``MingbuCore``，因此 mixin 本身不 import
``core``，也就不产生循环依赖。
"""
import json
import uuid
from datetime import date, timedelta
from typing import Any, Optional

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent

from ..bj_time import now_bj
from ..constants import BUGUA_METHODS, KV_PROFILE, MINGPAN_METHODS
from ..formatters import _clock_to_shichen
from ..util import spawn
from ._host import MixinHost


def _normalize_birth_hour(hour: Any) -> Any:
    """将 profile.birth.hour 归一到命理服务端期望的 0–11 地支索引（0=子…11=亥）。

    - 0–11：已是地支索引（插件内部口径，与 ``_clock_to_shichen`` / ``_shichen_name``
      完全一致），原样返回（幂等，不改动正常档案）；
    - 12–23：视为 24 小时制时钟小时，经 ``_clock_to_shichen`` 折算为地支索引。
      命理服务端以 ``2*hour`` 作内部索引并约束 ``hour <= 11``，故 24h 钟点（≥12）
      原样直传会被拒（wrong hour 2×hour），必须折算；
    - 其余异常值：原样返回，交由服务端校验，保持既有失败语义。
    """
    try:
        h = int(hour)
    except (TypeError, ValueError):
        return hour
    if 0 <= h <= 11:
        return h
    if 12 <= h <= 23:
        return _clock_to_shichen(h)
    return hour


def _apply_late_zi_swap(year, month, day, birth_hour):
    """晚子时（23 点，即 23:00–23:59）换日。

    传统命理主流流派（徐乐吾《子平真诠评注》等，及问真八字、元亨利贞等
    主流排盘软件）认为 23 点后出生应**以次日推日柱、时柱**（夜子时/晚子时
    换日）。此处把公历日期 +1、时辰归为子时（0），后端据此排盘即得次日
    日柱、并以次日日干起子时（五鼠遁），完整符合「晚子时换日」规则。

    返回 ``(year, month, day, hour, swapped)``：``swapped=True`` 表示发生了换日。
    若 ``birth_hour`` 不是 23（含早子时 0 点），原样返回、``swapped=False``。
    """
    try:
        h = int(birth_hour)
    except (TypeError, ValueError):
        return year, month, day, birth_hour, False
    if h == 23:
        d = date(int(year), int(month), int(day)) + timedelta(days=1)
        return d.year, d.month, d.day, 0, True
    return year, month, day, birth_hour, False


class ProfileMixin(MixinHost):
    """用户档案与出生信息：读写、参数归一、方法推断、合法性校验。"""

    async def _get_profile(self, uid: str) -> dict:
        try:
            raw = await self.get_kv_data(f"{KV_PROFILE}:{uid}", None)
            return json.loads(raw) if raw else {}
        except Exception as e:
            logger.debug(f"[mingbu] 读取档案失败(已忽略): {e}")
            return {}

    async def _save_profile(self, uid: str, profile: dict):
        profile["_updated"] = now_bj().isoformat()
        await self.put_kv_data(f"{KV_PROFILE}:{uid}", json.dumps(profile, ensure_ascii=False))

    def _birth_from(self, profile: dict) -> Optional[dict]:
        b = profile.get("birth", {})
        required = ["year", "month", "day", "hour", "gender"]
        if all(k in b for k in required):
            return {
                "year": b["year"],
                "month": b["month"],
                "day": b["day"],
                "hour": _normalize_birth_hour(b["hour"]),
                "gender": b["gender"],
                "name": profile.get("name", ""),
            }
        return None

    def _birth_params(self, year, month, day, birth_hour, gender, name=""):
        # 晚子时（23 点）换日：日柱/时柱按次日推，时辰归子时。
        year, month, day, birth_hour, _ = _apply_late_zi_swap(
            year, month, day, birth_hour,
        )
        return {
            "year": year,
            "month": month,
            "day": day,
            "hour": _clock_to_shichen(birth_hour),
            "gender": gender,
            "name": name,
        }

    def _method_for(self, uid: str, profile: dict) -> str:
        """推断某用户的命理方法，用于每月后台测算分流。

        - profile["method"] 显式设置了合法方法（ziwei/bazi/liuyao/qimen/meihua）
          时直接采用；
        - 否则按 background_watch_method_default 配置（默认 "auto"）推断：
            * auto + 有出生信息 → 命盘类（combined：紫微+八字 同算）
            * auto + 无出生信息 → 卜卦类（默认 liuyao 六爻）
        """
        cfg = getattr(self, "_config", {}) or {}
        default = cfg.get("background_watch_method_default", "auto")
        m = (profile or {}).get("method") or default or "auto"
        if m in MINGPAN_METHODS or m in BUGUA_METHODS:
            return m
        # auto 推断
        if self._birth_from(profile or {}):
            return "combined"
        return "liuyao"

    async def _save_birth(self, event: AstrMessageEvent,
                          year, month, day, birth_hour, gender):
        uid = event.get_sender_id()
        profile = await self._get_profile(uid)
        profile["name"] = profile.get("name") or (event.get_sender_name() or "")
        profile["birth"] = self._birth_params(year, month, day, birth_hour, gender)
        await self._save_profile(uid, profile)

        # 排盘后自动建档到命理后端客户库（紫占客户管理/学习校准用）。
        # 非关键、非阻塞：后台异步执行，失败仅 debug 记录，不影响排盘主流程。
        cfg = getattr(self, "_config", {}) or {}
        if cfg.get("client_archive_enabled", True):
            spawn(
                self._archive_client(
                    uid, year, month, day, birth_hour, gender, profile.get("name", ""),
                ),
                name="archive_client",
            )

    async def _archive_client(self, uid, year, month, day, birth_hour, gender, name):
        """把出生信息归档到后端 /api/clients/ensure（后台、非关键、失败静默）。

        映射策略：每个 AstrBot 用户首次建档时生成一个稳定的**匿名 UUID**
        （``uuid.uuid4()``）持久化到该用户 profile 的 ``client_uuid`` 字段，之后复用。
        这与后端「浏览器 localStorage 生成的匿名 UUID」语义一致——匿名、稳定、按用户
        隔离，且不从 sender_id 可逆推导，避免泄露原始用户标识。

        注意：后端 /api/clients/ensure 的 ``birth.hour`` 是 **0–23 时钟小时**（与
        /api/bazi 的 0–11 地支不同），故这里用原始 ``birth_hour``，而非
        ``_birth_params`` 折算后的地支索引。
        """
        try:
            _g = str(gender or "").strip().lower()
            if _g in ("male", "男"):
                gender_norm = "male"
            elif _g in ("female", "女"):
                gender_norm = "female"
            else:
                logger.debug(f"[mingbu] 客户建档跳过：性别取值不合法 {gender!r}")
                return

            profile = await self._get_profile(uid)
            client_uuid = profile.get("client_uuid")
            if not client_uuid:
                client_uuid = str(uuid.uuid4())
                profile["client_uuid"] = client_uuid
                await self._save_profile(uid, profile)

            payload = {
                "birth": {
                    "year": int(year),
                    "month": int(month),
                    "day": int(day),
                    "hour": int(birth_hour),
                    "gender": gender_norm,
                },
                "name": name or "",
            }
            await self.api.ensure_client(payload, user_id=client_uuid)
        except Exception as e:
            logger.debug(f"[mingbu] 客户建档失败(忽略): {e}")

    def _validate_birth(self, year, month, day, birth_hour, gender) -> tuple:
        """校验出生参数。返回 (ok: bool, error_msg: str)。"""
        import calendar
        try:
            y = int(year)
            m = int(month)
            d = int(day)
            h = int(birth_hour)
        except (TypeError, ValueError):
            return (False, "出生信息里有不是数字的字段，请确认年份/月份/日期/钟点都是数字。")
        if not (1900 <= y <= 2100):
            return (False, f"出生年份 {y} 超出可排盘范围（1900-2100）。")
        if not (1 <= m <= 12):
            return (False, f"月份 {m} 不合法（应为 1-12）。")
        try:
            max_d = calendar.monthrange(y, m)[1]
        except Exception:
            max_d = 31
        if not (1 <= d <= max_d):
            return (False, f"{y}年{m}月只有 {max_d} 天，日期 {d} 不合法。")
        if not (0 <= h <= 23):
            return (False, f"出生钟点 {h} 不合法（应为 0-23）。")
        g = (gender or "").lower()
        if g not in ("male", "female", "男", "女"):
            return (False, "性别请填 male（男）或 female（女）。")
        return (True, "")
