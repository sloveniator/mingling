"""stars/apis/system.py -- KV 查看/写入 + 运行时配置读写
====================================================
对应路由：``/kv`` ``/kv_set`` ``/config``

配置校验不再手写类型/边界/enum 分支，统一走
:mod:`._schema` 消费 ``_conf_schema.json``（建议④）。
"""

from __future__ import annotations

import json

from astrbot.api import logger

from ...bj_time import now_bj
from ._base import (
    api_endpoint,
    error_response,
    json_response,
    kv_read_guard,
    kv_write_guard,
    query_str,
    request_json,
    request_method,
    with_json,
)
from ._schema import coerce_by_schema

KV_RUNTIME_CONFIG = "mingbu.runtime_config"
# 模型统一配置：拉取的模型目录 + 端点凭据。与运行时配置分开存，
# 因为它是「拉取状态」而非用户配置项，不应走 coerce_by_schema，也不随配置保存被合并/冲刷。
KV_MODEL_CATALOG = "mingbu.model_catalog"


def _mask_secret(value: str) -> str:
    """脱敏（仅用于模型目录里保存的掩码副本，非配置表单展示）。

    配置表单的 API Key 自 v4.8.51 起明文展示、所见即所得，不再走掩码/留空保留原值。
    """
    value = str(value or "")
    if not value:
        return ""
    if len(value) <= 8:
        return "*" * len(value)
    return value[:4] + "..." + value[-4:]


# 配置热更新钩子：{配置键: (内核引擎属性, 引擎上的字段名)}
# 拆分前这段是 api_config 里三个手写 if 分支，新增热更项要照抄一遍样板。
_HOT_RELOAD_MAP = {
    "darkchannel_cache_ttl": ("diviner", "_cache_ttl"),
    "proactive_interval_min": ("proactive", "_interval_min"),
    "weight_threshold": ("conversation_inspector", "weight_threshold"),
}


class SystemApiMixin:
    """系统域 Web API。"""

    # ── /kv ───────────────────────────────────────────────────
    @api_endpoint("KV 查看")
    async def api_kv_get(self):
        key = query_str("key")
        err = kv_read_guard(key)
        if err:
            return error_response(err)
        raw = await self.get_kv_data(key, None)
        return json_response({"key": key, "value": raw})

    # ── /kv_set ───────────────────────────────────────────────
    @api_endpoint("KV 写入")
    @with_json
    async def api_kv_set(self, body):
        key = body.get("key", "")
        value = body.get("value", "")
        err = kv_write_guard(key)
        if err:
            return error_response(err)
        try:
            await self.put_kv_data(key, value)
            return json_response({"ok": True, "key": key})
        except Exception as e:
            return error_response(f"写入失败: {str(e)[:120]}")

    # ── /config ───────────────────────────────────────────────
    @api_endpoint("配置读写")
    async def api_config(self):
        if request_method() == "GET":
            # API Key 明文返回，方便面板直接展示与编辑（所见即所得）
            return json_response(await self._merged_config())
        return await self._save_config(await request_json())

    async def _merged_config(self) -> dict:
        """AstrBot 静态配置 + 运行时 KV 覆盖层。"""
        merged = dict(self._config) if self._config else {}
        try:
            raw = await self.get_kv_data(KV_RUNTIME_CONFIG, None)
            if raw:
                runtime = json.loads(raw) if isinstance(raw, str) else raw
                if isinstance(runtime, dict):
                    merged.update(runtime)
        except Exception as e:
            logger.debug(f"[mingbu] 读取运行时配置失败: {e}")
        return merged

    async def _merge_runtime_config(self, data: dict) -> None:
        """把若干键合并进运行时 KV 覆盖层（保留既有键，只覆盖本次提交）。"""
        try:
            existing: dict = {}
            raw = await self.get_kv_data(KV_RUNTIME_CONFIG, None)
            if raw:
                parsed = json.loads(raw) if isinstance(raw, str) else raw
                if isinstance(parsed, dict):
                    existing = parsed
            existing.update(data)
            await self.put_kv_data(
                KV_RUNTIME_CONFIG, json.dumps(existing, ensure_ascii=False)
            )
            # 同步内存态与权威镜像，保证热重载后（configure 重新合并 overlay）不丢
            self._config.update(data)
            if self.core:
                self.core._runtime_overlay = dict(existing)
        except Exception as e:
            logger.debug(f"[mingbu] 运行时配置合并失败(忽略): {e}")

    async def _save_config(self, payload: dict):
        data, errors = coerce_by_schema(payload)
        if errors:
            return error_response("; ".join(errors))

        # 明文展示、所见即所得：提交什么存什么（留空=清空该字段），不再做掩码/留空保留原值。
        # 落 KV（运行时覆盖层）：与静态配置分开存，升级不丢面板改动。
        # 合并既有覆盖层、只覆盖本次提交的键——部分保存不会冲掉其它已存键
        # （审查报告 [中]：旧实现整条 put_kv 覆盖，会静默丢失其它运行时配置）。
        try:
            merged = dict(data)
            try:
                raw = await self.get_kv_data(KV_RUNTIME_CONFIG, None)
                if raw:
                    existing = json.loads(raw) if isinstance(raw, str) else raw
                    if isinstance(existing, dict):
                        existing.update(data)
                        merged = existing
            except (ValueError, TypeError):
                pass  # 既有覆盖层损坏则忽略，以本次提交为准

            # 调试日志：记录本次保存的键数与 KV 覆盖层状态
            old_kv_keys = set()
            try:
                if raw and isinstance(raw, dict):
                    old_kv_keys = set(raw.keys())
            except Exception:
                pass
            logger.info(
                f"[mingbu] 保存配置: 提交 {len(data)} 键, "
                f"KV 旧键 {len(old_kv_keys)}, 合并后 {len(merged)} 键, "
                f"新增 {len(set(data.keys()) - old_kv_keys)}, "
                f"更新 {len(set(data.keys()) & old_kv_keys)}"
            )

            await self.put_kv_data(
                KV_RUNTIME_CONFIG, json.dumps(merged, ensure_ascii=False)
            )
        except Exception as e:
            return error_response(f"保存配置失败: {e}")

        self._config.update(data)
        if self.core:
            # 先同步 KV 覆盖层权威镜像（含本次提交的 data），再 configure——
            # configure 会用该镜像重新合并，避免刚保存的键被旧镜像覆盖掉。
            self.core._runtime_overlay = dict(merged)
            self.core.configure(self._config)
            self._apply_hot_reload(data)
            # 外部服务配置变更立即失效 Provider 解析缓存，免重启生效（审查报告 P2）
            if self.core.provider_router:
                self.core.provider_router.invalidate_cache()
        # 把最终生效值同步回 AstrBot 静态 config（self.config 的 save_config 落盘到
        # data/config/<plugin>_config.json），让 AstrBot 原生「插件配置」页与命灵后台
        # 配置页展示同一份值——消除「后台改了原生页却显示旧值」的双入口不同步。
        # 命灵后台页(KV 覆盖层)为最终真源，回写静态层是为了 UI 一致与升级不丢失。
        self._sync_astrbot_static_config(data)
        return json_response({"ok": True, "message": "配置已保存并生效"})

    def _sync_astrbot_static_config(self, data: dict) -> None:
        """把本次保存的键同步进 AstrBot 静态 config（self.config，AstrBotConfig）并持久化。

        仅当 self.config 存在且是 dict、且具备官方 save_config() 时才回写；否则静默跳过，
        不阻断保存（后台 KV 覆盖层仍是最终真源，功能不受影响）。防御式：AstrBot 版本差异
        或缺 config 对象时安全降级。
        """
        try:
            cfg = getattr(self, "config", None)
            if not isinstance(cfg, dict):
                return
            for k, v in data.items():
                try:
                    cfg[k] = v
                except Exception:
                    continue
            saver = getattr(cfg, "save_config", None)
            if callable(saver):
                saver()
                logger.info(f"[mingbu] 配置已回写 AstrBot 静态 config({len(data)}项)")
        except Exception as e:  # noqa: BLE001 - 回写失败不影响后台保存
            logger.debug(f"[mingbu] 回写 AstrBot 静态 config 失败(已忽略): {e}")

    def _apply_hot_reload(self, data: dict) -> None:
        """把已保存的配置推到活着的引擎实例上，免重启生效。"""
        for cfg_key, (engine_attr, field) in _HOT_RELOAD_MAP.items():
            if cfg_key not in data:
                continue
            engine = getattr(self.core, engine_attr, None)
            if engine is None:
                continue
            try:
                setattr(engine, field, data[cfg_key])
            except Exception as e:
                logger.debug(f"[mingbu] 热更新 {cfg_key} 失败: {e}")
        # integration_mode 不走 _HOT_RELOAD_MAP（它不是单引擎字段，而是重建
        # core.flags + gateway.flags 门控快照）。端到端实测复现：flags 在
        # ensure() 守卫块内只建一次，面板切换 observe 后不重建则不重启不生效。
        if "integration_mode" in data and hasattr(self.core, "reload_flags"):
            try:
                self.core.reload_flags()
            except Exception as e:
                logger.debug(f"[mingbu] 热更新 integration_mode 失败(忽略): {e}")

    # ── /model_catalog ───────────────────────────────────────
    @api_endpoint("拉取模型目录")
    @with_json
    async def api_model_catalog(self, body):
        """按 OpenAI 兼容 /models 拉取模型列表并按能力分类。

        请求体：``{"base_url": str, "api_key": str, "extra_headers": str|dict}``。
        extra_headers 可选，字符串按 JSON 解析。
        """
        from ...provider_router import fetch_model_catalog

        base_url = (body.get("base_url") or "").strip()
        api_key = (body.get("api_key") or "").strip()
        merged = await self._merged_config()
        if not api_key:
            # 前端留空时允许回退到运行时配置中已保存的 mc_api_key，即使本框为空
            # 也能直接使用已保存的 Key 拉取模型。
            api_key = merged.get("mc_api_key") or ""
        if not base_url:
            base_url = merged.get("mc_base_url") or ""
        extra_raw = body.get("extra_headers") or ""
        extra_headers = {}
        if isinstance(extra_raw, dict):
            extra_headers = extra_raw
        elif isinstance(extra_raw, str) and extra_raw.strip():
            try:
                extra_headers = json.loads(extra_raw)
            except Exception:
                return error_response("额外请求头不是合法 JSON")
        if not isinstance(extra_headers, dict):
            extra_headers = {}

        result = await fetch_model_catalog(base_url, api_key, extra_headers)
        if not result.get("ok"):
            return error_response(result.get("error", "拉取失败"))
        return json_response({"ok": True, "models": result["models"]})

    # ── /model_catalog_save ────────────────────────────────
    @api_endpoint("保存模型目录")
    @with_json
    async def api_model_catalog_save(self, body):
        """把拉取的模型目录 + 端点凭据持久化到 KV，确保再次进入页面时不丢失。

        拉取模型列表后必须落盘才能跨刷新保留——此前 ``MC_MODELS`` 只存在于
        前端内存，刷新即清空，导致「拉取保存后再次进入全部消失」。

        请求体：``{"base_url": str, "api_key": str, "extra_headers": str|dict, "models": list}``。
        仅存储、不校验模型可用性；写 KV 失败返回 error_response。
        """
        api_key = (body.get("api_key") or "").strip()
        catalog = {
            "base_url": (body.get("base_url") or "").strip(),
            # API Key 不再落入模型目录 KV；运行时能力配置由 config 接口单独保存。
            "api_key": "" if api_key else "",
            "api_key_masked": _mask_secret(api_key),
            "extra_headers": body.get("extra_headers") or "",
            "models": body.get("models") or [],
            "saved_at": now_bj().isoformat(),
        }
        try:
            await self.put_kv_data(
                KV_MODEL_CATALOG, json.dumps(catalog, ensure_ascii=False)
            )
        except Exception as e:
            return error_response(f"保存模型目录失败: {e}")

        # 关键：把「拉取模型列表」所用的端点凭据一并持久化到运行时配置，
        # 否则「拉取」只落盘模型目录、不落盘 Key/Base/Headers，刷新后凭据消失。
        # mc_api_key 仅在本次非空时写入，避免「重新拉取时留空」把已存 Key 清掉。
        extra_headers = body.get("extra_headers") or ""
        if not isinstance(extra_headers, str):
            extra_headers = json.dumps(extra_headers, ensure_ascii=False)
        cfg_updates = {
            "mc_base_url": (body.get("base_url") or "").strip(),
            "mc_extra_headers": extra_headers,
        }
        if api_key:
            cfg_updates["mc_api_key"] = api_key
        await self._merge_runtime_config(cfg_updates)

        return json_response(
            {"ok": True, "message": "模型目录已保存", "count": len(catalog["models"])}
        )

    # ── /model_catalog_get ─────────────────────────────────
    @api_endpoint("读取模型目录")
    async def api_model_catalog_get(self):
        """返回此前持久化的模型目录（若有），供页面加载时恢复。

        无数据返回 ``catalog: null``，前端据此保持空态提示，不报错。
        """
        try:
            raw = await self.get_kv_data(KV_MODEL_CATALOG, None)
        except Exception:
            raw = None
        if not raw:
            return json_response({"ok": True, "catalog": None})
        try:
            catalog = json.loads(raw) if isinstance(raw, str) else raw
        except Exception:
            return json_response({"ok": True, "catalog": None})
        if not isinstance(catalog, dict):
            return json_response({"ok": True, "catalog": None})
        # 兼容历史明文 KV，但永不通过 API 原样返回；前端只显示掩码。
        catalog = dict(catalog)
        legacy_key = catalog.get("api_key") or ""
        existing_masked = catalog.get("api_key_masked") or ""
        catalog["api_key"] = ""
        catalog["api_key_masked"] = existing_masked or _mask_secret(legacy_key)
        return json_response({"ok": True, "catalog": catalog})

    # ── /list_providers ────────────────────────────────────
    @api_endpoint("列出服务商")
    async def api_list_providers(self):
        """返回 AstrBot 已配置的 LLM 服务商，供面板 Provider ID 下拉选用。

        同时返回 ``diagnostics``，包含枚举过程中每一步的尝试结果与错误信息，
        让前端在拉取失败时能给出具体根因（未初始化 / 数据结构变更 / 确实没配等）。
        """
        from ...provider_router import list_llm_providers_with_diagnostics

        ctx = getattr(self, "context", None)
        try:
            result = await list_llm_providers_with_diagnostics(ctx, getattr(self, "_config", {}))
        except Exception as e:
            logger.warning("[mingbu] 枚举服务商接口异常: %s", e)
            return json_response({
                "ok": True,
                "providers": [],
                "diagnostics": {
                    "context_available": bool(ctx),
                    "provider_manager_available": bool(getattr(ctx, "provider_manager", None)),
                    "strategies": [],
                    "provider_sample": None,
                    "root_cause": "enumeration_failed",
                    "message": f"枚举服务商时发生内部异常: {e}",
                },
            })
        return json_response({"ok": True, **result})

    # 兼容旧调用点/测试：拆分前的校验入口
    def _validate_config(self, payload: dict) -> dict:
        data, errors = coerce_by_schema(payload)
        if errors:
            return {"ok": False, "error": "; ".join(errors)}
        return {"ok": True, "data": data}
