"""系统诊断与外部服务连通性 API。"""
from __future__ import annotations

import time

from astrbot.api import logger

from ...constants import MINGBU_API_URL
from ...http_client import ServiceHttpError
from ...http_client import request_json as _request_json
from ...provider_router import (
    TASK_NAMES,
    _direct_dashscope_rerank,
    _direct_openai_chat,
    _direct_openai_embed,
    _direct_openai_rerank,
    _direct_openai_speech,
    _extract_provider_id,
    _is_dashscope_target,
    _provider_id_candidates,
    _resolve,
)
from ._base import api_endpoint, error_response, json_response, request_json, request_method


def _mask_secret(value: str) -> str:
    value = str(value or "")
    if not value:
        return ""
    if len(value) <= 8:
        return "*" * len(value)
    return value[:4] + "..." + value[-4:]


# 1x1 透明 PNG，供 image_vision 连通性测试走多模态路径（与运行时一致）。
_TINY_PNG_B64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="


def _is_image_content_error(exc: Exception) -> bool:
    """判断是否为「图像内容被模型拒绝」类错误（请求已抵达且鉴权通过）。

    典型：SiliconFlow 视觉模型对占位/过小测试图返回 HTTP 400 code 20015
    （Failed to apply ...Processor），或消息含 image/processor/OCR 字样。
    这类错误与「密钥无效(401)/服务不可达(5xx)」不同——端点本身连通、密钥有效，
    仅测试图不合适，应按「软成功」上报，避免 OCR 类模型误判为失败。
    """
    msg = str(exc)
    status = getattr(exc, "status", None)
    low = msg.lower()
    if status == 400 and ("20015" in msg or "image" in low or "processor" in low or "ocr" in low):
        return True
    return False


def _result(service: str, started: float, *, ok: bool, error: str = "", **extra) -> dict:
    data = {
        "service": service,
        "ok": bool(ok),
        "latency_ms": round((time.perf_counter() - started) * 1000, 1),
    }
    if error:
        data["error"] = str(error)[:240]
    data.update(extra)
    return data


class DiagnosticsApiMixin:
    """管理面板的依赖状态和最小连通性检查。

    认证由 AstrBot Plugin Pages 宿主负责；本插件不伪造一套可能与宿主冲突的
    token 体系。接口只返回脱敏信息，绝不回传 API Key。
    """

    @api_endpoint("系统诊断")
    async def api_diagnostics(self):
        status = getattr(self.core, "capability_status", {}) or {}
        capabilities = []
        for name, item in status.items():
            row = dict(item)
            row["error"] = str(row.get("error", ""))[:200]
            capabilities.append(row)
        cfg = getattr(self, "_config", {}) or {}
        provider = getattr(self.core, "provider_router", None)
        return json_response({
            "ok": True,
            "plugin": "astrbot_plugin_mingbu",
            "initialized": bool(getattr(self.core, "_ensured", False)),
            "capabilities": capabilities,
            "dependencies": {
                "mingbu_api_url": str(cfg.get("mingbu_api_url", "")),
                "provider_router": bool(provider),
                "memory": bool(getattr(self.core, "memory", None)),
                "persona": bool(getattr(self.core, "persona", None)),
            },
        })

    @api_endpoint("服务连通性")
    async def api_connectivity(self):
        if request_method() != "POST":
            return error_response("连通性测试必须使用 POST")
        body = await request_json()
        service = str(body.get("service", "")).strip().lower()
        if service not in {"provider", "tts", "stt", "embedding", "rerank", "vision", "merged", "interpret"}:
            return error_response("service 必须是 provider/tts/stt/embedding/rerank/vision/merged/interpret")
        started = time.perf_counter()
        if service == "provider":
            return await self._test_provider(started, body)
        return await self._test_direct_service(started, service, body)

    @api_endpoint("命理后端连通性测试")
    async def api_mingbu_test(self):
        if request_method() != "POST":
            return error_response("命理后端测试必须使用 POST")
        started = time.perf_counter()
        cfg = getattr(self, "_config", {}) or {}
        # 优先面板已保存的配置地址；未配置则取运行中客户端的 base_url，最终回退环境变量默认。
        url = str(cfg.get("mingbu_api_url", "") or getattr(self.api, "base_url", MINGBU_API_URL)).rstrip("/")
        test_url = url + "/api/health"
        try:
            data = await _request_json(None, "GET", test_url, timeout=10)
            ok = isinstance(data, dict) or data is True
            extra = {"url": url}
            if isinstance(data, dict):
                # 透传后端 health 的关键字段，供前端展示 provider 是否就绪。
                extra["providerConfigured"] = data.get("providerConfigured")
            return json_response(_result("mingbu", started, ok=bool(ok), **extra))
        except Exception as exc:
            logger.warning("[mingbu] 命理后端连通性测试失败: %s", exc)
            return json_response(_result("mingbu", started, ok=False, error=str(exc), url=url))

    async def _test_provider(self, started: float, body: dict):
        task = str(body.get("task_type", "general"))
        model = str(body.get("model", "")).strip() or None
        provider_id = str(body.get("provider_id", "")).strip() or None
        # v4.8.112：能力服务商「自定义模式」直连测试——填了 Key+Base 直接测，
        # 不经 AstrBot Provider 解析（独立直连语义与外部服务一致）。
        d_key = str(body.get("api_key", "")).strip() or None
        d_base = str(body.get("api_base", "")).strip() or None
        if d_key and d_base:
            try:
                text = await _direct_openai_chat(
                    str(body.get("text") or "命灵连通性测试"),
                    d_key, d_base, model or "gpt-4o",
                    raise_error=True,
                )
                return json_response(_result(
                    "provider", started, ok=True, task_type=task,
                    latency_ms=round((time.perf_counter() - started) * 1000),
                    response_preview=(text or "")[:60],
                    using_direct=True,
                ))
            except Exception as exc:
                return json_response(_result(
                    "provider", started, ok=False, task_type=task,
                    error=f"直连失败: {exc}", using_direct=True,
                ))
        router = getattr(self.core, "provider_router", None)
        if not router:
            return json_response(_result("provider", started, ok=False, error="ProviderRouter 未初始化"))
        try:
            provider = None
            # 优先按显式 provider_id 解析（用于「拉取模式」外部服务卡片直接测某个 ID）
            if provider_id:
                ctx = getattr(self, "context", None)
                # 真实 AstrBot 的 provider_manager.get_provider_by_id 是 async def，
                # 必须 await；同步调用只会拿到 coroutine，导致测试结果误报
                # 「Provider 不支持 text_chat」。_resolve 对同步/异步两种实现都兼容。
                for holder in (getattr(ctx, "provider_manager", None) if ctx else None, ctx):
                    if provider is not None or holder is None:
                        continue
                    fn = getattr(holder, "get_provider_by_id", None)
                    if not callable(fn):
                        continue
                    for candidate in _provider_id_candidates(provider_id):
                        try:
                            provider = await _resolve(fn(candidate))
                        except Exception:
                            provider = None
                        if provider is not None:
                            break
            # 否则按 task_type 走配置解析（用于「能力服务商」角色）
            if not provider:
                provider = await router.get_provider_async(task)
            if not provider:
                if provider_id:
                    # 显式指定了 provider_id 却解析不到 —— 真实失败（沿用原行为）
                    return json_response(_result(
                        "provider", started, ok=False,
                        error=f"未找到 Provider ID: {provider_id}",
                        task_type=task, provider_id=provider_id,
                    ))
                # 走到这里说明「完全没有指定 provider_id」，且按 task_type 也解析不到
                # 专属服务商。分两种情形：
                # 1) 只填了模型却没填服务商 → 模型没有承载方，属配置不完整，需给出
                #    明确指引（让用户去「服务商」框填对应的 Provider ID），而不是一句
                #    含义不明的「未找到任务类型 X 对应的 Provider」；
                # 2) 同时没填模型 → 这是「留空则沿用默认服务商」的合法状态（运行时
                #    回退 AstrBot 默认服务商），应提示性通过，而非笼统报红错误导用户。
                if model:
                    err = (
                        f"已指定模型「{model}」但未配置「{TASK_NAMES.get(task, task)}」服务商。"
                        f"模型必须由某个 AstrBot 服务商承载，请在上方「服务商」填写其 Provider ID"
                        f"（留空服务商时该模型会落到 AstrBot 默认服务商，可能无此模型）。"
                    )
                    return json_response(_result("provider", started, ok=False, error=err, task_type=task))
                return json_response(_result(
                    "provider", started, ok=True, task_type=task, using_default=True,
                    message=(
                        f"未配置「{TASK_NAMES.get(task, task)}」专属服务商，"
                        f"运行时将使用 AstrBot 默认服务商（主回复）。"
                        f"如需为该任务独立指定模型，请在上方填写服务商与模型。"
                    ),
                ))
            resolved_id = _extract_provider_id(provider) or provider_id or "已解析"
            # 只要求解析成功就算通过；text_chat 能力不是所有 Provider 都有（如 TTS Provider）
            if not hasattr(provider, "text_chat"):
                return json_response(_result(
                    "provider", started, ok=True,
                    error="Provider 不支持 text_chat，仅确认 ID 可解析",
                    task_type=task, provider_id=resolved_id,
                    resolved_only=True,
                ))
            kwargs = {
                "prompt": str(body.get("text") or "命灵连通性测试"),
                "session_id": "mingbu_diagnostics",
                "image_urls": None, "system_prompt": None,
                "temperature": 0, "max_tokens": 16,
            }
            if model:
                kwargs["model"] = model
            try:
                result = await provider.text_chat(**kwargs)
            except TypeError:
                # 兼容旧版 AstrBot Provider 不接受 model 关键字。
                kwargs.pop("model", None)
                result = await provider.text_chat(**kwargs)
            return json_response(_result(
                "provider", started, ok=True, task_type=task,
                provider_id=resolved_id,
                model=model or "使用 Provider 默认模型",
                response_preview=str(result)[:120],
            ))
        except Exception as exc:
            logger.warning("[mingbu] Provider 诊断失败: %s", exc)
            return json_response(_result("provider", started, ok=False, error=str(exc), task_type=task, provider_id=provider_id))

    async def _test_direct_service(self, started: float, service: str, body: dict):
        cfg = getattr(self, "_config", {}) or {}
        # service -> 真实配置键前缀（与 _conf_schema.json 对齐）。
        # 注意：面板 data-svc 用短名（vision/merged/interpret），但配置键带前缀
        #（image_vision_*/merged_forward_*/mingbu_interpret_*），且命理 AI 解读的
        # 接口地址字段叫 base_url 而非 api_base。
        KEY_PREFIX = {
            "tts": "tts", "stt": "stt", "embedding": "embedding", "rerank": "rerank",
            "vision": "image_vision", "merged": "merged_forward", "interpret": "mingbu_interpret",
        }
        BASE_FIELD = {
            "interpret": "base_url",  # mingbu_interpret_base_url
        }
        prefix = KEY_PREFIX.get(service, service)
        base_field = BASE_FIELD.get(service, "api_base")
        key = str(body.get("api_key") or cfg.get(f"{prefix}_api_key", "")).strip()
        base = str(body.get("api_base") or cfg.get(f"{prefix}_{base_field}", "")).strip()
        model = str(body.get("model") or cfg.get(f"{prefix}_model", "")).strip()
        missing = [name for name, value in (("api_key", key), ("api_base", base), ("model", model)) if not value]
        if missing:
            return json_response(_result(
                service, started, ok=False,
                error="缺少配置: " + ", ".join(missing),
                api_base=base, api_key=_mask_secret(key), model=model,
                mode="configuration_check",
            ))
        try:
            if service == "tts":
                # DashScope Qwen TTS 模型使用 MultiModalConversation API，
                # 不走 OpenAI 兼容的 /audio/speech 端点（会返回 404）。
                # 模型名包含 qwen-tts 或 qwen3-tts 时走 DashScope 直连路径。
                model_lower = str(model or "").lower()
                is_dashscope = "qwen-tts" in model_lower or "qwen3-tts" in model_lower
                if is_dashscope:
                    # 注意：provider_router 在插件根目录，必须用三点半相对导入
                    # （与 diagnostics.py:12 的模块级导入一致；单点会去找
                    #  stars/apis/provider_router 导致 ModuleNotFoundError）。
                    from ...provider_router import _direct_dashscope_tts
                    from .tts_voices import _DASHSCOPE_VOICE_NAMES

                    diag_voice = cfg.get("tts_voice") or "Cherry"
                    if diag_voice not in _DASHSCOPE_VOICE_NAMES:
                        # v4.8.132：无效音色（如虚构音色）会 400 导致误报连通失败
                        logger.warning(
                            f"[mingbu.diag] 音色 {diag_voice} 不在 DashScope 官方列表，测试改用 Cherry"
                        )
                        diag_voice = "Cherry"
                    audio = await _direct_dashscope_tts(
                        str(body.get("text") or "命灵连通性测试"),
                        key, model, voice=diag_voice,
                    )
                else:
                    audio = await _direct_openai_speech(
                        str(body.get("text") or "命灵连通性测试"), key, base, model,
                        voice=cfg.get("tts_voice") or None, raise_error=True,
                    )
                extra = {"bytes": len(audio)}
            elif service == "stt":
                # STT 完全走 AstrBot STT provider，插件不再持有直连凭据。
                # 连通性测试改为验证「AstrBot 里是否已配置可用的 STT provider」：
                # 有 provider 即视为 STT 服务就绪（真实转写由 AstrBot 运行时完成）。
                from ...provider_router import (
                    get_stt_provider,  # 模块级引用（get_stt_provider 所在）
                )
                provider = await get_stt_provider(
                    getattr(self, "context", None),
                    cfg.get("provider_stt") or "",
                )
                if not provider:
                    raise ServiceHttpError(
                        "未配置 AstrBot STT provider：请在 AstrBot 主设置里添加 STT 服务商后重试"
                    )
                extra = {
                    "provider": _extract_provider_id(provider) or "default",
                    "note": "STT 由 AstrBot provider 提供，插件不再直连",
                }
            elif service == "embedding":
                vectors = await _direct_openai_embed([str(body.get("text") or "命灵连通性测试")], key, base, model, raise_error=True)
                extra = {"dimensions": len(vectors[0]) if vectors else 0}
            elif service in {"vision", "merged", "interpret"}:
                # 这三类自定义凭据均为 OpenAI 兼容 /chat/completions：
                # 图片视觉 / 合并转发 运行时即走 _direct_openai_chat；
                # 命理 AI 解读的自定义凭据也是指向 LLM 的 chat 端点（如 SiliconFlow）。
                # vision 走多模态路径（带一张 1x1 图），与运行时 image_urls 调用一致。
                image_urls = None
                if service == "vision":
                    image_urls = ["data:image/png;base64," + _TINY_PNG_B64]
                try:
                    text = await _direct_openai_chat(
                        str(body.get("text") or "命灵连通性测试"), key, base, model,
                        image_urls=image_urls, raise_error=True,
                    )
                except ServiceHttpError as e:
                    # 多模态/视觉模型（尤其 OCR 类如 PaddleOCR-VL）会拒绝占位或过小
                    # 的测试图，返回 4xx（如 SiliconFlow code 20015 图像处理失败）。
                    # 此类错误说明请求已抵达模型且鉴权通过（否则是 401），
                    # 端点与密钥本身有效；仅测试图不合适，按「软成功」上报并提示。
                    if service == "vision" and _is_image_content_error(e):
                        return json_response(_result(
                            service, started, ok=True,
                            api_base=base, api_key=_mask_secret(key), model=model,
                            mode="live_request",
                            image_rejected=True,
                            response_preview="端点已连通·密钥有效（测试图被视觉模型拒收，不影响真实图片转述）",
                            note="端点与密钥有效，但占位测试图被视觉模型拒绝（多模态模型对测试图敏感，不影响真实图片转述）",
                        ))
                    raise
                extra = {"response_preview": text[:120]}
            else:
                docs = [str(body.get("text") or "命灵连通性测试")]
                if _is_dashscope_target(base, model):
                    # DashScope 无 OpenAI 兼容 /rerank（404）——走原生 gte-rerank 端点
                    indices = await _direct_dashscope_rerank(docs[0], docs, key, model, 1, raise_error=True)
                else:
                    indices = await _direct_openai_rerank(docs[0], docs, key, base, model, 1, raise_error=True)
                extra = {"indices": indices}
            return json_response(_result(
                service, started, ok=True,
                api_base=base, api_key=_mask_secret(key), model=model,
                mode="live_request", **extra,
            ))
        except Exception as exc:
            logger.warning("[mingbu] %s 连通性测试失败: %s", service, exc)
            return json_response(_result(
                service, started, ok=False, error=str(exc),
                api_base=base, api_key=_mask_secret(key), model=model,
                mode="live_request",
            ))
