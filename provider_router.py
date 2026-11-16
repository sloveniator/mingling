"""
provider_router.py -- 多 Provider 编排
=====================================
根据任务类型和配置选择不同的 AstrBot Provider。
v4.8.141 起所有 LLM/STT/TTS/Embedding/Rerank 请求完全交由 AstrBot Provider
路由，插件不再持有直连凭据（移除原「自定义直连」双模式）。

仍保留两类外部请求辅助（非「重复造轮子」，与 Provider 体系互补）：
- 拉取 AstrBot Dashboard 已配置的服务商/模型列表（fetch_model_catalog、
  list_llm_providers），供面板下拉选择；
- 命理引擎 mingbu 的凭据透传（mingbu_interpret_*，仅转发给 mingbu 后端，
  不经插件直连 OpenAI）。

面板的「连通性测试」「TTS 音色试听」属配置校验/预览工具，与运行时对话路径
解耦，仍用请求体临时凭据调用，不读插件直连配置键。
"""
from __future__ import annotations

import base64
import inspect
from typing import Any, Optional

import aiohttp
from astrbot.api import logger

from .http_client import ServiceHttpError, _CircuitBreaker, request_bytes, request_json

TASK_NAMES = {
    "main_reply": "主回复",
    "dream": "梦境/预感",
    "growth": "成长反思",
    "proactive": "主动关怀",
    "news": "新闻整理",
    "search": "主动搜索",
    "image_vision": "图片转述",
    "merged_forward": "合并消息转述",
    "general": "通用系统任务",
}


# Dashboard API 拉取使用独立熔断器：避免内网 Dashboard 短时不可用污染
# 全局熔断器，导致 mingbu 后端等外部请求也被误熔断。
_DASHBOARD_BREAKER = _CircuitBreaker(failure_threshold=3, cooldown_sec=10)


def _extract_provider_id(p) -> Optional[str]:
    """从 dict 或 AstrBot Provider 实例中提取稳定的 Provider ID。

    AstrBot 真实 Provider 对象不直接暴露 ``id``/``provider_id`` 属性，而是在
    ``provider_config["id"]`` 或 ``meta().id`` 中。本函数兼容 dict、旧模拟对象、
    以及真实的 AstrBot Provider 结构，避免「有实例但缺 id」导致下拉为空。
    """
    if isinstance(p, dict):
        return str(p.get("id") or p.get("provider_id") or p.get("name") or "") or None
    for attr in ("id", "provider_id"):
        v = getattr(p, attr, None)
        if v:
            return str(v)
    cfg = getattr(p, "provider_config", None)
    if isinstance(cfg, dict):
        v = cfg.get("id") or cfg.get("provider_id") or cfg.get("name")
        if v:
            return str(v)
    meta_fn = getattr(p, "meta", None)
    if callable(meta_fn):
        try:
            meta = meta_fn()
            for attr in ("id", "provider_id"):
                v = getattr(meta, attr, None)
                if v:
                    return str(v)
        except Exception:
            pass
    name = getattr(p, "name", None)
    if name:
        return str(name)
    return None


def _provider_id_candidates(provider_id: str) -> list[str]:
    """展开 Provider ID 的候选匹配形态。

    AstrBot Dashboard API 返回的 provider id 是复合格式（``服务商/模型``，
    如 ``google_gemini/models/gemini-3.1-flash-lite-preview``），管理面板
    「能力服务商」下拉直接取它；而 ``provider_manager.inst_map`` 的键是纯
    服务商 ID（如 ``google_gemini``）。历史缺陷：配置存了复合 ID 后
    ``get_provider_by_id`` 精确匹配永远落空，任务（图片转述/梦境/成长等）
    全部走默认 provider 或失败。

    返回候选列表：先原样（精确优先），再剥离 ``/模型`` 后缀的纯服务商 ID。
    """
    if not provider_id:
        return []
    candidates = [provider_id]
    base = provider_id.split("/", 1)[0].strip()
    if base and base != provider_id:
        candidates.append(base)
    return candidates


# AstrBot 对「每条消息 content 分片」的按模态净化（sanitize_contexts_by_modalities）
# 只在该 provider 配置了 modalities 且不含 audio 时才把 audio_url/input_audio 分片
# 替换成 [Audio] 文本；未配置（None/空）时原样透传 → 非全模态主模型 400。
# 这里把「本次请求会用的 chat provider」的 modalities 修正为不含 audio 的最小集，
# 让 AstrBot 每轮请求自动净化历史语音分片（含 runner 已快照的 run_context.messages）。
_PROVIDER_MODALITIES_NO_AUDIO = ["text", "image", "tool_use"]


def _normalize_provider_modalities(cfg: dict) -> bool:
    """修正 provider_config 的 modalities 为「不含 audio」集合；返回是否发生修改。

    规则（幂等，可反复调用）：
    - modalities 缺失 / 非 list / 空列表 → 设为 ``["text", "image", "tool_use"]``。
    - modalities 为 list 且含 ``audio``/``audio_url``/``input_audio`` 元素 → 剔除该元素；
      剔除后若为空则回退默认集。
    - 已是不含 audio 的 list → 不改动，返回 False。
    """
    if not isinstance(cfg, dict):
        return False
    raw = cfg.get("modalities")
    if raw is None or (isinstance(raw, list) and not raw):
        cfg["modalities"] = list(_PROVIDER_MODALITIES_NO_AUDIO)
        return True
    if not isinstance(raw, list):
        return False
    audio_tokens = {"audio", "audio_url", "input_audio"}
    kept = [m for m in raw if str(m).strip().lower() not in audio_tokens]
    if len(kept) == len(raw):
        return False
    cfg["modalities"] = kept or list(_PROVIDER_MODALITIES_NO_AUDIO)
    return True


async def ensure_chat_provider_no_audio(ctx, umo: str | None = None) -> bool:
    """确保当前会话使用的 chat provider 的 modalities 不含 audio。

    背景（AstrBot 4.27 实测）：``ProviderRequest`` 没有 ``messages`` 属性，插件在
    ``on_llm_request`` 钩子里剥 ``req.messages`` 永远拿 None；且 runner 构建时已把
    ``req.contexts`` 快照进 ``run_context.messages``，改 ``req.contexts`` 也不进最终
    payload。真正对快照生效的净化点在 ``_iter_llm_responses`` 的
    ``_sanitize_contexts_for_provider`` —— 它读 ``provider.provider_config["modalities"]``，
    只有配置了且不含 audio 才剥离 audio_url 分片。本函数把该配置补上，让 AstrBot
    每轮自动把历史语音分片替换成 ``[Audio]`` 文本，根治非全模态主模型（Qwen3-VL 等）
    因 audio 分片 400 落 fallback。

    Args:
        ctx: AstrBot Context 实例。
        umo: unified_message_origin；不传时按 ``get_default_chat_provider`` 的兜底序解析。

    Returns:
        是否成功修正了某个 chat provider 的 modalities。
    """
    if ctx is None:
        return False
    candidates = []
    for name in ("get_using_provider_async", "get_using_provider"):
        fn = getattr(ctx, name, None)
        if not callable(fn):
            continue
        try:
            p = await _resolve(fn(umo) if umo else fn())
        except Exception as e:
            logger.debug(f"[mingbu] ensure_chat_provider_no_audio: {name}() 失败: {e}")
            continue
        if p is not None:
            candidates.append(p)
            break
    if not candidates:
        fn = getattr(ctx, "get_all_providers", None)
        if callable(fn):
            try:
                candidates = await _resolve(fn()) or []
            except Exception as e:
                logger.debug(f"[mingbu] ensure_chat_provider_no_audio: get_all_providers() 失败: {e}")
                candidates = []
    fixed = False
    for p in candidates or []:
        cfg = getattr(p, "provider_config", None)
        if not isinstance(cfg, dict):
            continue
        if _normalize_provider_modalities(cfg):
            pid = _extract_provider_id(p) or "<unknown>"
            logger.info(
                "[mingbu] 已将 chat provider %s 的 modalities 修正为不含 audio（AstrBot 将自动净化历史语音分片）",
                pid,
            )
            fixed = True
    return fixed


def _extract_provider_name(p) -> Optional[str]:
    """提取 Provider 可读名称；没有时回退到 id。"""
    if isinstance(p, dict):
        return str(p.get("name") or p.get("provider") or p.get("id") or p.get("provider_id") or "") or None
    name = getattr(p, "name", None)
    if name:
        return str(name)
    model = getattr(p, "model_name", None)
    if model:
        return str(model)
    return _extract_provider_id(p)


def _unwrap_sync(obj):
    """同步路径上拿到 awaitable 时返回 None，并关闭 coroutine 避免 never-awaited 警告。

    真实 AstrBot（4.2x）的 ``provider_manager.get_provider_by_id`` 是 ``async def``，
    在同步的 :meth:`ProviderRouter.get_provider` 里直接调用只会拿到 coroutine。
    历史缺陷：该 coroutine 被当成 Provider 缓存下来，`hasattr(p, "text_chat")`
    恒为 False，于是「明明配了 provider_xxx 却始终解析不到」，还刷
    RuntimeWarning: coroutine was never awaited。
    """
    if inspect.iscoroutine(obj):
        try:
            obj.close()
        except Exception:
            pass
        return None
    if inspect.isawaitable(obj):
        return None
    return obj


async def _call_text_chat(provider, req, model: str = ""):
    """调用 provider.text_chat，并对不同 AstrBot 版本的签名差异做降级重试。

    注：``temperature`` / ``max_tokens`` / ``session_id`` 在官方 OpenAI 源里会被
    ``_prepare_chat_payload`` 的 ``**kwargs`` 吞掉、不进请求体，传 None 无副作用，
    因此保持与历史一致的入参形状。
    """
    kwargs = dict(
        prompt=getattr(req, "prompt", "") or "",
        session_id=getattr(req, "session_id", None),
        image_urls=getattr(req, "image_urls", None),
        system_prompt=getattr(req, "system_prompt", None),
        temperature=getattr(req, "temperature", None),
        max_tokens=getattr(req, "max_tokens", None),
    )
    if model:
        kwargs["model"] = model
    try:
        return await provider.text_chat(**kwargs)
    except TypeError:
        # 旧版 text_chat 不支持 model 关键字：去掉后重试，保证不崩
        kwargs.pop("model", None)
        return await provider.text_chat(**kwargs)


class ProviderRouter:
    def __init__(self, plugin):
        self.plugin = plugin
        self._provider_cache = {}

    def _resolve_provider_id(self, task_type: str) -> str:
        """从配置中解析任务对应的 Provider ID；未配置返回空串。

        兼容扁平 key（``provider_main_reply``）与旧嵌套（``provider_overrides.main_reply``），
        子任务未单独指定时回退主回复的服务商。
        """
        cfg = getattr(self.plugin, "_config", {}) if hasattr(self.plugin, "_config") else {}
        provider_id = cfg.get(f"provider_{task_type}", "")
        if not provider_id and task_type != "general":
            provider_id = cfg.get("provider_main_reply", "")
        if not provider_id:
            overrides = cfg.get("provider_overrides", {})
            if isinstance(overrides, dict):
                provider_id = overrides.get(task_type, "")
                if not provider_id and task_type != "general":
                    provider_id = overrides.get("main_reply", "")
        return provider_id or ""

    def get_provider(self, task_type: str = "general"):
        """同步解析任务对应 Provider。返回 None 表示使用默认 Provider。

        真实 AstrBot 的 ``provider_manager.get_provider_by_id`` 为异步，本方法会
        跳过它（见 :func:`_unwrap_sync`）并用同步的 ``ctx.get_provider_by_id`` /
        实例名匹配兜底。运行时请求路径请用 :meth:`get_provider_async`。
        """
        if task_type in self._provider_cache:
            return self._provider_cache[task_type]

        provider_id = self._resolve_provider_id(task_type)
        if not provider_id:
            return None

        try:
            ctx = getattr(self.plugin, "context", None)
            if not ctx:
                return None
            provider = None
            for candidate in _provider_id_candidates(provider_id):
                if provider is not None:
                    break
                # AstrBot provider manager 通常提供 get_provider_by_id
                pm = getattr(ctx, "provider_manager", None)
                if pm and hasattr(pm, "get_provider_by_id"):
                    try:
                        provider = _unwrap_sync(pm.get_provider_by_id(candidate))
                    except Exception:
                        provider = None
                # 兜底：context 直接提供（真实 AstrBot 的 Context.get_provider_by_id 是同步的）
                if not provider and hasattr(ctx, "get_provider_by_id"):
                    try:
                        provider = _unwrap_sync(ctx.get_provider_by_id(candidate))
                    except Exception:
                        provider = None
                # 兜底：按实例 name 匹配（配置里可能填的是可读名称而非 id，
                # 或者 get_provider_by_id 在部分 AstrBot 版本下行为不同）
                if not provider:
                    provider = _match_provider_by_name(ctx, candidate)

            if provider:
                self._provider_cache[task_type] = provider
                return provider
        except Exception as e:
            logger.debug(f"[mingbu] 获取 Provider {provider_id} 失败: {e}")
        return None

    async def get_provider_async(self, task_type: str = "general"):
        """异步解析任务对应 Provider（运行时请求路径专用）。

        与 :meth:`get_provider` 的差别：正确 ``await`` 异步的
        ``provider_manager.get_provider_by_id`` —— 真实 AstrBot 4.2x 就是异步版本，
        同步调用只会得到 coroutine 从而永远解析失败。

        ``provider_id`` 兼容两种形态：
        - 纯服务商 ID（如 ``google_gemini``）——精确匹配；
        - Dashboard API 返回的复合 ID（如 ``google_gemini/models/gemini-3.1-...``，
          面板「能力服务商」下拉直接取它）——先精确，失败后剥离 ``/模型`` 后缀
          再按纯服务商 ID 匹配，避免「配置了下拉项却永远解析不到」。
        """
        if task_type in self._provider_cache:
            return self._provider_cache[task_type]

        provider_id = self._resolve_provider_id(task_type)
        if not provider_id:
            return None

        ctx = getattr(self.plugin, "context", None)
        if not ctx:
            return None

        # 候选 ID：原样 + 剥离 /模型 后缀（复合 ID 兼容）
        candidates = _provider_id_candidates(provider_id)

        provider = None
        for candidate in candidates:
            if provider is not None:
                break
            for holder in (getattr(ctx, "provider_manager", None), ctx):
                if provider is not None or holder is None:
                    continue
                fn = getattr(holder, "get_provider_by_id", None)
                if not callable(fn):
                    continue
                try:
                    provider = await _resolve(fn(candidate))
                except Exception as e:
                    logger.debug(f"[mingbu] 解析 Provider {candidate} 失败: {e}")
                    provider = None
            if provider is None:
                provider = _match_provider_by_name(ctx, candidate)
        if provider is not None:
            self._provider_cache[task_type] = provider
            if len(candidates) > 1 and provider_id != candidates[1]:
                logger.info(
                    "[mingbu] 复合 Provider ID %s 已解析为 %s（剥离 /模型 后缀）",
                    provider_id,
                    _extract_provider_id(provider),
                )
        return provider

    def invalidate_cache(self):
        """清空 Provider 解析缓存。

        面板修改外部服务配置（provider_* 等）后调用，使 get_provider 在下次
        请求时重新读取最新配置，无需重启插件（符合「面板免重启生效」设计意图）。
        见代码审查报告 P2：原缓存命中后改配置需重启才生效。
        """
        self._provider_cache.clear()

    async def llm_request(self, task_type: str, req, model: Optional[str] = None):
        """使用指定 Provider 发起 LLM 请求。

        ``model`` 为调用方显式指定的模型名（来自配置中的 ``{task}_model``，
        即管理面板「已拉取的模型」下拉选中的模型）。传入后：

        - 即使 ``provider_{task}`` 留空、走 AstrBot 默认服务商，也会用**选中的模型**
          而非默认服务商的默认模型——对应「服务商留空用默认服务商，但选了模型就以该
          模型独立调用」的区分逻辑；
        - 若指定了 provider，则把模型透传给该 provider 的 ``text_chat``。

        透传对 AstrBot 不同版本做兼容：部分旧版 ``text_chat`` 不接受 ``model`` 关键字，
        捕获 ``TypeError`` 后回退为不带 ``model`` 的调用，避免整体崩溃。
        """
        # 调用方指定的模型优先于 req 自带 model（req.model 一般为空）
        selected = model or getattr(req, "model", None) or ""
        if selected:
            try:
                req.model = selected
            except Exception:
                pass

        # v4.8.141 起：LLM 请求完全交由 AstrBot Provider 路由，插件不再持有
        # 直连凭据（移除 v4.8.111 的「自定义直连优先」分支）。选中的模型经
        # provider.text_chat 透传，AstrBot 默认服务商负责承载。
        provider = await self.get_provider_async(task_type)
        if provider is not None and hasattr(provider, "text_chat"):
            return await _call_text_chat(provider, req, selected)

        # 回退默认 Provider（req.model 已设为选中模型，AstrBot 默认服务商会用它）
        ctx = getattr(self.plugin, "context", None)
        if ctx is not None:
            # ① 旧版 AstrBot / 测试桩：Context.llm_request(req)
            legacy = getattr(ctx, "llm_request", None)
            if callable(legacy):
                return await _resolve(legacy(req))
            # ② 真实 AstrBot（4.2x）没有 Context.llm_request —— 此前该分支恒不命中，
            #    导致所有未单独配服务商的子任务（图片转述/梦境/成长/主动关怀/新闻/
            #    搜索/合并转发）一律返回 None 并打「没有可用 Provider」。改为显式
            #    取当前默认对话 Provider 并直接调用。
            default = await get_default_chat_provider(ctx)
            if default is not None and hasattr(default, "text_chat"):
                return await _call_text_chat(default, req, selected)
        logger.warning(
            f"[mingbu] 没有可用 Provider，任务 {TASK_NAMES.get(task_type, task_type)} 失败"
            "（请在 AstrBot「模型服务」里配置默认对话服务商，"
            "或在命灵面板为该任务指定服务商）"
        )
        return None


# ============================================================
# 跨版本 Provider 获取（STT / TTS / Rerank）
# AstrBot 不同版本方法名不一致（get_using_stt_provider /
# get_all_stt_providers / get_provider_by_id / provider_manager.*
# 实例列表）。这里做兼容兜底，取不到就返回 None（调用方优雅降级）。
# ============================================================
async def _resolve(maybe):
    if inspect.isawaitable(maybe):
        return await maybe
    return maybe


async def _pick(ctx, provider_id, *, using, all_method, legacy, cap):
    """通用：按 id → 当前使用 → 列表首个 的顺序挑出带 cap 能力的 provider。

    兼容 AstrBot Dashboard 复合 Provider ID（``服务商/模型``）：先精确匹配，
    失败后剥离 ``/模型`` 后缀再按纯服务商 ID 匹配（见 _provider_id_candidates）。
    """
    if provider_id and hasattr(ctx, "get_provider_by_id"):
        for candidate in _provider_id_candidates(provider_id):
            p = await _resolve(ctx.get_provider_by_id(candidate))
            if p and hasattr(p, cap):
                return p
    for name in (using, legacy):
        fn = getattr(ctx, name, None)
        if fn:
            try:
                p = await _resolve(fn())
            except Exception:
                p = None
            if p and hasattr(p, cap):
                return p
    fn = getattr(ctx, all_method, None)
    if fn:
        try:
            lst = await _resolve(fn())
        except Exception:
            lst = None
        if lst:
            for p in lst:
                if hasattr(p, cap):
                    return p
    return None


async def get_default_chat_provider(ctx):
    """取 AstrBot「当前使用的对话服务商」（Chat Completion）。取不到返回 None。

    背景：真实 AstrBot 的 ``Context`` **没有** ``llm_request()``（只有
    ``llm_generate(chat_provider_id=...)``，强制要求显式服务商 ID）。插件此前用
    ``hasattr(ctx, "llm_request")`` 作为「回退默认服务商」的判据，在真机上恒为
    False，于是所有子任务在未单独指定服务商时全部失败。本函数按官方 API 逐级兜底：

    1. ``get_using_provider_async()``（现行推荐接口，非 deprecated）；
    2. ``get_using_provider()``（旧同步接口，已标 deprecated 但仍可用）；
    3. ``get_all_providers()`` / ``provider_manager.provider_insts`` 首个可对话实例。

    不传 ``umo``：命灵子任务用的是内部 session_id（如 ``mingbu_vision_x``），
    并非真实的 unified_message_origin，传进去只会让会话级偏好解析失败。
    """
    if ctx is None:
        return None
    for name in ("get_using_provider_async", "get_using_provider"):
        fn = getattr(ctx, name, None)
        if not callable(fn):
            continue
        try:
            p = await _resolve(fn())
        except Exception as e:
            logger.debug(f"[mingbu] {name}() 取默认服务商失败: {e}")
            continue
        if p is not None and hasattr(p, "text_chat"):
            return p
    # 列表兜底：会话未设默认服务商时取第一个可对话实例
    candidates = []
    fn = getattr(ctx, "get_all_providers", None)
    if callable(fn):
        try:
            candidates = await _resolve(fn()) or []
        except Exception as e:
            logger.debug(f"[mingbu] get_all_providers() 失败: {e}")
            candidates = []
    if not candidates:
        pm = getattr(ctx, "provider_manager", None)
        for attr in ("provider_insts", "llm_provider_insts", "llm_providers"):
            lst = getattr(pm, attr, None) if pm else None
            if isinstance(lst, (list, tuple)) and lst:
                candidates = list(lst)
                break
    for p in candidates or []:
        if hasattr(p, "text_chat"):
            return p
    return None


async def default_llm_request(ctx, req, model: str = ""):
    """不经 ProviderRouter 时，用 AstrBot 默认对话服务商完成一次请求。

    供 ``star_mixins/llm.py`` 在 ``provider_router`` 缺失时使用，语义与
    ``ProviderRouter.llm_request`` 的兜底段一致（旧版 ``llm_request`` 优先，
    否则走默认服务商 ``text_chat``）。
    """
    if ctx is None:
        return None
    legacy = getattr(ctx, "llm_request", None)
    if callable(legacy):
        return await _resolve(legacy(req))
    provider = await get_default_chat_provider(ctx)
    if provider is None:
        return None
    return await _call_text_chat(provider, req, model or getattr(req, "model", "") or "")


async def get_stt_provider(ctx, provider_id: str = "") -> object:
    """获取 STT（语音转文字）provider。无则返回 None。"""
    return await _pick(
        ctx, provider_id,
        using="get_using_stt_provider",
        all_method="get_all_stt_providers",
        legacy="get_stt_provider",
        cap="get_text",
    )


async def get_tts_provider(ctx, provider_id: str = "") -> object:
    """获取 TTS（语音合成）provider。无则返回 None。"""
    return await _pick(
        ctx, provider_id,
        using="get_using_tts_provider",
        all_method="get_all_tts_providers",
        legacy="get_tt_provider",
        cap="get_audio",
    )


async def get_rerank_provider(ctx, provider_id: str = "") -> object:
    """获取 Rerank（重排序）provider。优先指定 id，否则取管理器首个 rerank 实例。

    兼容 AstrBot Dashboard 复合 Provider ID（``服务商/模型``）。
    """
    if provider_id and hasattr(ctx, "get_provider_by_id"):
        for candidate in _provider_id_candidates(provider_id):
            p = await _resolve(ctx.get_provider_by_id(candidate))
            if p and hasattr(p, "rerank"):
                return p
    pm = getattr(ctx, "provider_manager", None)
    if pm and hasattr(pm, "rerank_provider_insts"):
        for p in pm.rerank_provider_insts:
            if hasattr(p, "rerank"):
                return p
    return None


async def call_rerank(provider, query: str, documents: list, top_n: int):
    """调用 rerank，返回重排后的索引列表（list[int]）或 None。

    兼容两种调用签名：rerank(query=, documents=, top_n=) 与 rerank(query, documents, top_n)。
    """
    if not provider or not hasattr(provider, "rerank"):
        return None
    try:
        res = provider.rerank(query=query, documents=documents, top_n=top_n)
    except TypeError:
        try:
            res = provider.rerank(query, documents, top_n)
        except Exception as e:
            logger.debug(f"[mingbu] rerank 签名不兼容(忽略): {e}")
            return None
    except Exception as e:
        logger.debug(f"[mingbu] rerank 调用失败(忽略): {e}")
        return None
    res = await _resolve(res)
    return parse_rerank_indices(res)


async def list_llm_providers(ctx) -> list:
    """枚举 AstrBot 已配置的 LLM 服务商，返回 ``[{"id","name"}]``。

    本函数保持向后兼容；内部转调带诊断的版本，但只把服务商列表返回给调用方。
    """
    return (await list_llm_providers_with_diagnostics(ctx))["providers"]


async def list_llm_providers_with_diagnostics(ctx, plugin_cfg: Optional[dict] = None) -> dict:
    """枚举 AstrBot 已配置的 LLM 服务商，并返回可定位失败根因的诊断信息。

    返回结构::

        {
            "providers": [{"id": str, "name": str}, ...],
            "diagnostics": {
                "context_available": bool,
                "provider_manager_available": bool,
                "strategies": [{"name": str, "found": int, "error": str|None}, ...],
                "provider_sample": {"attributes": [str], "type": str}|None,
                "root_cause": "none|context_missing|manager_missing|"
                              "enumeration_failed|id_attribute_missing|no_providers_configured",
                "message": str,
            },
        }

    用途：管理面板「能力服务商（拉取模式）」下拉框取不到数据时，前端可以给出
    具体原因（AstrBot 未就绪 / 接口结构变更 / 确实没配服务商等），而不是一句
    模糊的「未配置任何服务商」。
    """
    diagnostics: dict[str, Any] = {
        "context_available": bool(ctx),
        "provider_manager_available": False,
        "strategies": [],
        "provider_sample": None,
        "root_cause": "none",
        "message": "",
    }
    insts = []
    pm = getattr(ctx, "provider_manager", None) if ctx else None
    diagnostics["provider_manager_available"] = bool(pm)

    # ── 最优先策略：AstrBot HTTP API（结构稳定、响应对象明确带 id）────
    # /api/v1/providers 有 OpenAPI 文档、返回 dict 且每个元素带稳定 id 字段
    # （如 "Sense/deepseek-v4-flash"），不依赖 provider_manager 进程内实例的
    # 属性猜测（旧路径常因「实例缺 id/provider_id 属性」而枚举为空）。
    if plugin_cfg is not None:
        api_base, api_key, api_ssl = _resolve_astrbot_dashboard(plugin_cfg)
        if api_base:
            try:
                api_providers = await _fetch_llm_providers_via_api(api_base, api_key, api_ssl)
                if api_providers:
                    diagnostics["strategies"].append({"name": "astrbot_http_api", "found": len(api_providers)})
                    diagnostics["root_cause"] = "none"
                    diagnostics["message"] = f"通过 AstrBot HTTP API 成功枚举 {len(api_providers)} 个服务商"
                    diagnostics["provider_sample"] = {
                        "type": "dict(api/v1/providers)",
                        "attributes": ["id", "provider", "provider_type", "model"],
                    }
                    return {"providers": api_providers, "diagnostics": diagnostics}
                diagnostics["strategies"].append({
                    "name": "astrbot_http_api", "found": 0,
                    "error": "AstrBot HTTP API 返回空服务商列表",
                })
            except Exception as e:
                diagnostics["strategies"].append({
                    "name": "astrbot_http_api", "found": 0,
                    "error": f"{type(e).__name__}: {e}"[:200],
                })
                diagnostics["http_api_error"] = f"{type(e).__name__}: {e}"[:200]
                logger.debug(f"[mingbu] HTTP API 拉取 providers 失败，回退实例枚举: {e}")

    def _record_strategy(name: str, found: int, error: Exception | None = None):
        diagnostics["strategies"].append({
            "name": name,
            "found": found,
            "error": f"{type(error).__name__}: {error}"[:200] if error else None,
        })

    if pm:
        # 1) 直接的 LLM 实例列表（多版本兼容：llm_provider_insts /
        #    llm_providers / provider_insts 任一存在即可枚举）
        for attr in ("llm_provider_insts", "llm_providers", "provider_insts"):
            lst = getattr(pm, attr, None)
            if isinstance(lst, (list, tuple)) and lst:
                if attr == "provider_insts":
                    # 通用实例列表，按是否支持 text_chat 过滤出 LLM
                    filtered = [p for p in lst if hasattr(p, "text_chat")]
                    if filtered:
                        insts.extend(filtered)
                        _record_strategy(
                            "provider_manager.provider_insts(text_chat)",
                            len(filtered),
                        )
                else:
                    insts.extend(lst)
                    _record_strategy(f"provider_manager.{attr}", len(lst))
                break
        else:
            _record_strategy("provider_manager.llm_provider_insts", 0)

        # 3) 方法式枚举
        if not insts:
            for meth in ("get_all_llm_providers", "get_llm_providers", "get_providers"):
                fn = getattr(pm, meth, None)
                if not callable(fn):
                    continue
                try:
                    r = fn()
                    r = await _resolve(r) if inspect.isawaitable(r) else r
                    if isinstance(r, (list, tuple)) and r:
                        insts.extend(r)
                        _record_strategy(f"provider_manager.{meth}()", len(r))
                        break
                    _record_strategy(f"provider_manager.{meth}()", 0)
                except Exception as e:
                    _record_strategy(f"provider_manager.{meth}()", 0, e)

    # 4) context 直出（兜底）
    if not insts and ctx:
        for meth in ("get_all_llm_providers", "get_providers"):
            fn = getattr(ctx, meth, None)
            if not callable(fn):
                continue
            try:
                r = fn()
                r = await _resolve(r) if inspect.isawaitable(r) else r
                if isinstance(r, (list, tuple)) and r:
                    insts.extend(r)
                    _record_strategy(f"context.{meth}()", len(r))
                    break
                _record_strategy(f"context.{meth}()", 0)
            except Exception as e:
                _record_strategy(f"context.{meth}()", 0, e)

    # 采样第一个实例的属性，用于排查「有实例但读不到 id/name」的结构不匹配
    if insts:
        sample = insts[0]
        try:
            attrs = [a for a in dir(sample) if not a.startswith("_")][:32]
        except Exception:
            attrs = []
        diagnostics["provider_sample"] = {
            "type": type(sample).__name__,
            "attributes": attrs,
        }

    out = []
    seen = set()
    for p in insts:
        pid = _extract_provider_id(p)
        if not pid:
            continue
        if pid in seen:
            continue
        seen.add(pid)
        name = _extract_provider_name(p) or pid
        # 若对象带 provider_config/model_name，尽量拼出「服务商 / 模型」可读名
        if not isinstance(p, dict):
            cfg = getattr(p, "provider_config", None) or {}
            provider_name = cfg.get("type") or cfg.get("provider") or ""
            model_name = getattr(p, "model_name", None) or cfg.get("model") or ""
            if provider_name or model_name:
                name = f"{provider_name} / {model_name}" if provider_name and model_name else (provider_name or model_name)
        out.append({"id": pid, "name": name})

    # 根因判定
    if out:
        diagnostics["root_cause"] = "none"
        diagnostics["message"] = f"成功枚举 {len(out)} 个服务商"
    elif not ctx:
        diagnostics["root_cause"] = "context_missing"
        diagnostics["message"] = "AstrBot 上下文未注入，插件可能尚未完成初始化，请刷新面板或重启 AstrBot"
    elif not pm:
        diagnostics["root_cause"] = "manager_missing"
        diagnostics["message"] = "AstrBot provider_manager 未就绪，请检查 AstrBot 版本或等待初始化完成"
    elif insts and not out:
        diagnostics["root_cause"] = "id_attribute_missing"
        diagnostics["message"] = "已拿到 Provider 实例，但无法从中解析出 id/provider_id，可能是 AstrBot 数据结构变更"
    elif diagnostics["strategies"] and all(s["error"] for s in diagnostics["strategies"]) and not any(s["found"] for s in diagnostics["strategies"]):
        diagnostics["root_cause"] = "enumeration_failed"
        diagnostics["message"] = "枚举服务商接口全部失败，详见 strategies.error"
    else:
        diagnostics["root_cause"] = "no_providers_configured"
        diagnostics["message"] = "AstrBot 未配置任何 LLM 服务商，请到「设置 → 模型服务」添加"

    # Dashboard HTTP API 失败时：若实例枚举成功，HTTP 错误只保留在 diagnostics
    # 中供排查，不污染主 message；只有实例枚举也失败（下拉为空）时，才把它拼到
    # message 末尾，让用户看到真实原因（地址错、端口错、未授权、连接拒绝等）。
    http_err = diagnostics.get("http_api_error")
    if http_err and not out and diagnostics["root_cause"] != "none":
        diagnostics["message"] += f"（Dashboard 拉取失败：{http_err}）"

    return {"providers": out, "diagnostics": diagnostics}


def parse_rerank_indices(result) -> list:
    """把 rerank 返回值解析为索引列表，支持多种返回形态；解析不出返回 []。"""
    if not isinstance(result, list):
        return []
    idxs = []
    for item in result:
        if isinstance(item, dict):
            if "index" in item:
                idxs.append(int(item["index"]))
            # 仅含 document / relevance_score 无 index 时无法映射回原序，跳过
            elif "document" in item or "relevance_score" in item:
                continue
        elif isinstance(item, (list, tuple)) and len(item) >= 1:
            idxs.append(int(item[0]))
        elif isinstance(item, int):
            idxs.append(item)
    return idxs


async def rerank_memory_list(provider, query: str, mems: list, plugin_cfg: Optional[dict] = None) -> list:
    """用 rerank provider 对记忆列表按 query 重排，返回重排后的新列表。

    v4.8.141 起完全走 AstrBot rerank provider，插件不再持有直连凭据。
    ``plugin_cfg`` 参数保留以兼容既有调用签名，不再消费。
    """
    if not mems:
        return mems

    if not provider:
        return mems
    docs = [getattr(m, "content", "") or "" for m in mems]
    idxs = await call_rerank(provider, query, docs, len(mems))
    if not idxs:
        return mems
    return [mems[i] for i in idxs if 0 <= i < len(mems)]


# ============================================================
# 直连 OpenAI 兼容 API（插件自管凭据）
# ============================================================
async def _direct_openai_embed(texts: list, api_key: str, api_base: str, model: str, *, raise_error: bool = False) -> list:
    """通过 OpenAI 兼容的 /embeddings 接口直接获取向量。

    Args:
        raise_error: 为 True 时请求失败或返回空向量则抛出异常（用于连通性测试），
                     否则保持向后兼容的静默失败（返回 []）。
    """
    url = api_base.rstrip("/") + "/embeddings"
    payload = {"input": texts, "model": model}
    try:
        data = await request_json(None, "POST", url, payload=payload, api_key=api_key, timeout=30)
    except ServiceHttpError as e:
        logger.debug(f"[mingbu] 直连 Embedding API 失败: {e}")
        if raise_error:
            raise
        return []
    vectors = [item.get("embedding") for item in data.get("data", []) if item.get("embedding")]
    if not vectors and raise_error:
        raise ServiceHttpError(f"Embedding 接口返回空向量: {data.keys() if isinstance(data, dict) else type(data)}")
    return vectors


async def _direct_openai_rerank(query: str, documents: list, api_key: str, api_base: str, model: str, top_n: int, *, raise_error: bool = False) -> list:
    """通过兼容 /rerank 接口 (SiliconFlow/Jina/Cohere) 直接重排序。

    统一调用 /rerank；解析策略覆盖两种返回形态：
      - SiliconFlow / Jina：{"results":[{"index":.., "relevance_score":..}]}
      - Cohere 等：直接返回顶层数组 [{"index":..}]
    原实现里「先主格式、非 200 时再试 alt」的分支因外层已 return 而永远走不到，
    此处删除该不可达死代码，改为单一正确路径 + 顶层数组兜底。

    Args:
        raise_error: 为 True 时请求失败或结果为空则抛出异常（用于连通性测试）。
    """
    url = api_base.rstrip("/") + "/rerank"
    payload = {"query": query, "documents": documents, "model": model, "top_n": top_n}
    try:
        data = await request_json(None, "POST", url, payload=payload, api_key=api_key, timeout=30)
    except ServiceHttpError as e:
        logger.debug(f"[mingbu] 直连 Rerank API 失败: {e}")
        if raise_error:
            raise
        return []
    results = data.get("results", []) if isinstance(data, dict) else []
    if results:
        idxs = [r.get("index", i) for i, r in enumerate(results)]
        if not idxs and raise_error:
            raise ServiceHttpError(f"Rerank 接口返回 results 但无有效 index: {results}")
        return idxs
    # Cohere 等接口可能直接返回顶层数组
    if isinstance(data, list):
        idxs = [r.get("index", i) for i, r in enumerate(data)]
        if idxs or not raise_error:
            return idxs
    if raise_error:
        raise ServiceHttpError(f"Rerank 接口返回空结果: {data.keys() if isinstance(data, dict) else type(data)}")
    return []


def _resolve_tts_voice(voice: Optional[str], api_base: str, model: str) -> str:
    """把用户/默认音色名解析为接口实际接受的形式。

    - 已含 ":"（如 "模型:音色"）原样返回；
    - SiliconFlow 类（api_base 含 "siliconflow"）且 model 非空：裸音色补成 "模型:音色"，
      缺省音色用 "alex"（CosyVoice2 / SenseVoice 通用预设）；
    - 其余（OpenAI 原生等）：裸音色原样，缺省 "alloy"。
    """
    if voice and ":" in voice:
        return voice
    is_siliconflow = "siliconflow" in (api_base or "").lower()
    if is_siliconflow:
        if not model:
            return voice or "alex"
        return f"{model}:{voice or 'alex'}"
    return voice or "alloy"


# ── DashScope（阿里云百炼）原生路由 ──────────────────────────────
# 原生域不提供 OpenAI 兼容的 /rerank、/audio/transcriptions，也没有 /models——
# 按 OpenAI 形状拼 URL 会 404（与 TTS 的 /audio/speech 404 同根因），
# 必须走原生端点（与 _direct_dashscope_tts 同理）。
_DASHSCOPE_NATIVE_BASE = "https://dashscope.aliyuncs.com"
_DASHSCOPE_COMPAT_BASE = "https://dashscope.aliyuncs.com/compatible-mode/v1"


def _is_dashscope_target(api_base: str, model: str = "") -> bool:
    """判断 api_base 是否指向阿里云 DashScope（百炼）。

    以 base 域名为准、不看模型名：本地/三方部署的同名模型
    （自建 SenseVoice、FunASR 等）仍应走 OpenAI 兼容路径。
    """
    return "dashscope.aliyuncs.com" in str(api_base or "").lower()


async def _direct_dashscope_rerank(query: str, documents: list, api_key: str, model: str, top_n: int, *, raise_error: bool = False) -> list:
    """DashScope 原生 gte-rerank 重排。

    端点 /api/v1/services/rerank/text-rerank/text-rerank（同步），
    返回按相关性排序的文档索引列表（与 _direct_openai_rerank 同形）。

    Args:
        raise_error: 为 True 时请求失败或结果为空则抛出异常（连通性测试用）。
    """
    url = _DASHSCOPE_NATIVE_BASE + "/api/v1/services/rerank/text-rerank/text-rerank"
    payload = {
        "model": model or "gte-rerank-v2",
        "input": {"query": query, "documents": list(documents)},
        "parameters": {"return_documents": False, "top_n": max(1, int(top_n or 1))},
    }
    try:
        data = await request_json(None, "POST", url, payload=payload, api_key=api_key, timeout=30)
    except ServiceHttpError as e:
        logger.debug(f"[mingbu] DashScope 原生 Rerank 失败: {e}")
        if raise_error:
            raise
        return []
    results: list = []
    if isinstance(data, dict):
        results = ((data.get("output") or {}).get("results")) or []
    idxs = [int(r.get("index", i)) for i, r in enumerate(results) if isinstance(r, dict)]
    if not idxs and raise_error:
        raise ServiceHttpError(f"DashScope rerank 返回空结果: {str(data)[:200]}")
    return idxs


async def _direct_dashscope_tts(text: str, api_key: str, model: str, voice: str = "Cherry",
                                instructions: str = "", optimize_instructions: bool = True) -> bytes:
    """通过 DashScope SDK 的 MultiModalConversation API 合成语音。

    适用于 qwen3-tts-instruct-flash 等 Qwen TTS 模型。
    DashScope 不使用 OpenAI 兼容的 /audio/speech 端点，而是使用
    dashscope SDK 的 MultiModalConversation.call() 方法。

    Args:
        text: 要合成的文本。
        api_key: DashScope API Key。
        model: 模型名称（如 qwen3-tts-instruct-flash-2026-01-26）。
        voice: 音色名称（如 Cherry）。默认 Cherry。
        instructions: 语气指令（自然语言，≤1600 token，中英文）。仅
            qwen3-tts-instruct-flash 系列支持，普通模型不识别；空串不传。
        optimize_instructions: 对 instructions 做官方语义增强以提升自然度，
            仅 instruct 系列生效且依赖 instructions 非空。默认开启。

    Returns:
        音频字节数据（WAV 格式）。

    Raises:
        RuntimeError: 合成失败或返回空音频。
    """
    try:
        import dashscope
        from dashscope.aigc.multimodal_conversation import MultiModalConversation
    except ImportError:
        raise RuntimeError(
            "DashScope SDK 未安装或版本过低。"
            "请安装：pip install dashscope>=1.20.0"
        )

    try:
        # 设置 API key
        dashscope.api_key = api_key

        # 调用 MultiModalConversation（值混合 str/None/bool，统一标注为 Any）
        kwargs: dict[str, Any] = {
            "model": model,
            "messages": None,
            "api_key": api_key,
            "voice": voice or "Cherry",
            "text": text,
        }
        # 语气指令（v4.8.129）：仅 qwen3-tts-instruct-flash 系列识别该参数，
        # 空串不传，普通模型保持原请求形状。
        if instructions:
            kwargs["instructions"] = instructions
            if optimize_instructions:
                kwargs["optimize_instructions"] = True
        response = MultiModalConversation.call(**kwargs)

        # 提取音频数据
        output = getattr(response, "output", None)
        audio_obj = getattr(output, "audio", None) if output is not None else None

        if not audio_obj:
            raise RuntimeError(f"DashScope TTS 返回空音频：{response}")

        data_b64 = getattr(audio_obj, "data", None)
        if data_b64:
            return base64.b64decode(data_b64)

        url = getattr(audio_obj, "url", None)
        if url:
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as resp:
                    return await resp.read()

        raise RuntimeError("DashScope TTS 返回格式未知")

    except Exception as e:
        logger.error(f"[mingbu.tts] DashScope TTS 调用失败：{e}")
        raise


async def _direct_openai_speech(text: str, api_key: str, api_base: str, model: str, *, voice: Optional[str] = None, response_format: str = "mp3", raise_error: bool = False) -> bytes:
    """通过 OpenAI 兼容的 /audio/speech 接口直接合成语音。

    voice 兼容两种取值：
      - OpenAI 原生：裸音色名（如 "alloy" / "echo"），缺省即为 "alloy"；
      - SiliconFlow 等：要求 "模型名:音色"（如 "FunAudioLLM/CosyVoice2-0.5B:alex"）。
    当 voice 未显式传入时按接口地址自动推断：siliconflow 默认用 "模型:alex"，
    否则用 "alloy"。显式传入的裸音色名在 siliconflow 下会自动补成 "模型:音色"。

    Args:
        voice: 音色名（见上）。缺省按服务类型自适应。
        response_format: 输出音频格式（mp3/wav/pcm...）。默认 mp3；
            连通性测试等需要把音频再喂给 STT 时传 "wav"（SenseVoiceSmall 拒收 mp3）。
        raise_error: 为 True 时请求失败或返回空音频则抛出异常（用于连通性测试）。
    """
    url = api_base.rstrip("/") + "/audio/speech"
    effective_voice = _resolve_tts_voice(voice, api_base, model)
    payload = {"input": text, "model": model, "voice": effective_voice, "response_format": response_format}
    try:
        audio = await request_bytes(None, url, payload=payload, api_key=api_key, timeout=60)
    except ServiceHttpError as e:
        logger.debug(f"[mingbu] 直连 TTS 异常(忽略): {e}")
        if raise_error:
            raise
        return b""
    if not audio and raise_error:
        raise ServiceHttpError("TTS 接口返回空音频流")
    return audio


# ============================================================
# 直连对话（多模态）：插件自管凭据的 /chat/completions
# 用于图片转述与合并转发转述——当 image_vision_*/merged_forward_* 的 Key+Base 均填时，
# enhancements.py 会直接调用本函数（覆盖 provider_* / 主回复凭据）；否则走 Provider 路由。
# （历史注释称其为「死配置」已过时：enhancements.py 已接入。）
# ============================================================
async def _direct_openai_chat(
    prompt: str,
    api_key: str,
    api_base: str,
    model: str,
    image_urls: Optional[list] = None,
    system: Optional[str] = None,
    raise_error: bool = False,
) -> str:
    """通过 OpenAI 兼容的 /chat/completions 直接对话，支持多模态图片。

    raise_error: 为 True 时请求失败或返回空内容则抛出异常（用于连通性测试）；
                 为 False（默认）时静默返回空串，由调用方决定是否降级。
    """
    url = api_base.rstrip("/") + "/chat/completions"
    content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
    for u in (image_urls or []):
        content.append({"type": "image_url", "image_url": {"url": u}})
    messages: list[dict[str, Any]] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": content})
    payload = {"model": model, "messages": messages, "max_tokens": 512}
    try:
        data = await request_json(None, "POST", url, payload=payload, api_key=api_key, timeout=40)
        return (data["choices"][0]["message"]["content"] or "").strip()
    except (ServiceHttpError, KeyError, IndexError, TypeError) as e:
        if raise_error:
            raise
        logger.debug(f"[mingbu] 直连对话失败(忽略): {e}")
        return ""


# ============================================================
# 模型目录拉取：一个地址 + 一个 Key，一键列出全部可用模型
# ============================================================
# 常见模型价格速查表（USD / 1M tokens，输入/输出）。
# 用途：在 /models 端点通常不返回价格时，按模型 id 关键字回退展示「参考价」。
# 注意这是近似值、会随厂商调价变动；拉取接口若能从端点拿到精确价（OpenRouter
# 风格 pricing 字段）则优先用端点价，并标 source=provider。本表仅作 source=builtin
# 的兜底。关键字用「包含匹配」，更具体的型号写在更前面以免被泛化型号抢匹配。
_BUILTIN_MODEL_PRICES = [
    # OpenAI
    ("gpt-4.5", 75, 150),
    ("gpt-4o-mini", 0.15, 0.6),
    ("gpt-4o", 2.5, 10),
    ("gpt-4.1-mini", 0.4, 1.6),
    ("gpt-4.1", 2, 8),
    ("gpt-4-turbo", 10, 30),
    ("gpt-4", 30, 60),
    ("gpt-3.5-turbo", 0.5, 1.5),
    ("o4-mini", 1.1, 4.4),
    ("o3-mini", 1.1, 4.4),
    ("o3", 10, 40),
    ("o1-mini", 1.1, 4.4),
    ("o1", 15, 60),
    # Anthropic Claude
    ("claude-opus-4", 15, 75),
    ("claude-sonnet-4", 3, 15),
    ("claude-3-5-sonnet", 3, 15),
    ("claude-3-5-haiku", 0.8, 4),
    ("claude-3-opus", 15, 75),
    ("claude-3-sonnet", 3, 15),
    ("claude-3-haiku", 0.25, 1.25),
    # Google Gemini
    ("gemini-2.5-pro", 1.25, 10),
    ("gemini-2.5-flash-lite", 0.1, 0.4),
    ("gemini-2.5-flash", 0.3, 2.5),
    ("gemini-2.0-flash", 0.1, 0.4),
    ("gemini-1.5-pro", 1.25, 5),
    ("gemini-1.5-flash", 0.075, 0.3),
    # DeepSeek
    ("deepseek-reasoner", 0.55, 2.19),
    ("deepseek-r1", 0.55, 2.19),
    ("deepseek-v3", 0.27, 1.10),
    ("deepseek-chat", 0.27, 1.10),
    # 阿里 Qwen（百炼美元近似价）
    ("qwen-max", 2.4, 9.6),
    ("qwen-plus", 0.8, 2.0),
    ("qwen-turbo", 0.1, 0.3),
    ("qwen-long", 0.5, 2.0),
    ("qwen2.5-72b", 0.35, 0.4),
    ("qwen2.5-32b", 0.13, 0.15),
    ("qwen2.5-14b", 0.07, 0.07),
    ("qwen2.5-7b", 0.04, 0.04),
    ("qwen3", 0.2, 0.6),
    # 智谱 GLM
    ("glm-4-plus", 1, 2),
    ("glm-4-long", 1, 1),
    ("glm-4-airs", 0.1, 0.1),
    ("glm-4-flash", 0, 0),
    # Moonshot / Kimi
    ("moonshot-v1-128k", 4, 4),
    ("moonshot-v1-32k", 2, 2),
    ("moonshot-v1-8k", 1, 1),
    ("kimi-latest", 1, 1),
    # Mistral
    ("mistral-large", 2, 6),
    ("mistral-small", 0.2, 0.6),
    ("mistral-nemo", 0.15, 0.15),
    ("mixtral", 0.6, 0.6),
    # Meta Llama（各云平台参考价）
    ("llama-3.3-70b", 0.6, 0.6),
    ("llama-3.1-405b", 2.7, 2.7),
    ("llama-3.1-70b", 0.35, 0.4),
    ("llama-3.1-8b", 0.05, 0.05),
    # MiniMax / 百川 / 零一
    ("abab7", 1, 1),
    ("abab6.5", 1, 1),
    ("baichuan4", 1, 1),
    ("yi-large", 0.5, 0.5),
    ("step-2", 3, 12),
    ("step-1v", 3, 12),
    ("step-1", 1, 4),
]


def _to_per_1m(x):
    """把价格归一化为「每 1M tokens」：每 token 级（<1）乘 1e6。"""
    if x is None:
        return None
    return round(x * 1_000_000, 4) if x < 1 else round(x, 4)


def _extract_provider_price(raw: dict):
    """从 /models 单个模型对象提取价格。

    兼容 OpenRouter 风格 ``pricing.{prompt,completion}``（每 token 字符串），
    也兼容直接的 ``price`` 数字或 ``{input,output}``。提取不到返回 None。
    """
    if not isinstance(raw, dict):
        return None
    pricing = raw.get("pricing")
    if isinstance(pricing, dict):

        def _num(*ks):
            for k in ks:
                v = pricing.get(k)
                if v is None:
                    continue
                try:
                    return float(v)
                except (TypeError, ValueError):
                    continue
            return None

        pin = _num("prompt", "input", "in")
        pout = _num("completion", "output", "out")
        if pin is None and pout is None:
            return None
        return (_to_per_1m(pin), _to_per_1m(pout))
    single = raw.get("price")
    if isinstance(single, (int, float)):
        return (_to_per_1m(float(single)), _to_per_1m(float(single)))
    if isinstance(single, dict):
        pin = single.get("input") or single.get("in")
        pout = single.get("output") or single.get("out")
        try:
            return (
                _to_per_1m(float(pin)) if pin is not None else None,
                _to_per_1m(float(pout)) if pout is not None else None,
            )
        except (TypeError, ValueError):
            return None
    return None


def _builtin_price_for(mid: str):
    """按模型 id 关键字回退查内置价表，命中返回 (in,out) 每 1M tokens。"""
    s = (mid or "").lower()
    for key, pin, pout in _BUILTIN_MODEL_PRICES:
        if key in s:
            return (pin, pout)
    return None


def _resolve_model_price(raw: dict, mid: str):
    """组合端点价与内置价：端点精确价优先，否则回退内置参考价。

    返回 ``{"in","out","unit","source"}`` 或 None（本地/开源模型无价）。
    """
    prov = _extract_provider_price(raw)
    if prov is not None and (prov[0] is not None or prov[1] is not None):
        return {"in": prov[0], "out": prov[1], "unit": "USD/1M", "source": "provider"}
    b = _builtin_price_for(mid)
    if b is not None:
        return {"in": b[0], "out": b[1], "unit": "USD/1M", "source": "builtin"}
    return None


# 按模型 id 关键字粗分类能力。优先级：embedding/rerank/tts/stt 为专用能力，
# 命中即不再视为通用对话；否则按 vision 关键字判断，兜底为 chat。
_MODEL_CAT_RULES = [
    ("embedding", ("embed", "bge-m3", "bge-large", "bge-base", "bge-small", "e5-", "text-embedding", "m3e", "jina-embed", "nomic")),
    ("rerank", ("rerank", "re-rank", "bge-reranker", "jina-rerank", "cohere-rerank")),
    ("tts", ("tts", "/tts", "speech", "cosyvoice", "fish-speech", "vits", "bark",
             "openvoice", "f5-tts", "chattts", "melo")),
    ("stt", ("whisper", "transcrib", "asr", "paraformer", "sensevoice", "funasr")),
    ("vision", ("vision", "-vl", "vl-", "gpt-4o", "gpt-4-vision", "gpt-4-turbo", "gemini", "claude",
                "qwen-vl", "qwen2-vl", "qwen2.5-vl", "llava", "internvl",
                "minicpm-v", "glm-4v", "step-1v", "emollm")),
]


def _classify_model(mid: str) -> list:
    """按模型 id 推断能力标签。返回 chat/vision/embedding/rerank/tts/stt 的子集。"""
    s = (mid or "").lower()
    cats = []
    for cat, keys in _MODEL_CAT_RULES:
        if any(k in s for k in keys):
            cats.append(cat)
    if not cats:
        return ["chat"]
    # 专用能力（embed/rerank/tts/stt）之外，能聊天的再补一个 chat 标签
    if "embedding" not in cats and "rerank" not in cats and "tts" not in cats and "stt" not in cats:
        cats.append("chat")
    return cats


async def fetch_model_catalog(base_url: str, api_key: str = "", extra_headers: Optional[dict] = None) -> dict:
    """拉取 OpenAI 兼容 /models 列表，按能力粗分类，并尽力附价格。

    每个模型附带 ``price`` 字段：``{"in","out","unit":"USD/1M","source"}``，
    source 为 ``provider``（端点返回精确价）/ ``builtin``（内置参考价）/
    ``None``（本地或开源模型，无价）。返回
    ``{"ok": bool, "models": [{"id","owned_by","cats","price"}], "error": str}``。
    """
    if not base_url:
        return {"ok": False, "error": "缺少接口地址 (Base URL)"}
    catalog_base = base_url.rstrip("/")
    # DashScope 原生域没有 OpenAI 兼容 /models（404）：拉取模型目录改走 compatible-mode
    if _is_dashscope_target(catalog_base) and "compatible-mode" not in catalog_base.lower():
        catalog_base = _DASHSCOPE_COMPAT_BASE
    url = catalog_base + "/models"
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    if extra_headers:
        headers.update(extra_headers)
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    return {"ok": False, "error": f"HTTP {resp.status}: {text[:200]}"}
                data = await resp.json()
    except Exception as e:  # 连接失败 / 超时 / JSON 解析失败
        return {"ok": False, "error": str(e)[:200]}

    raw = data.get("data", []) if isinstance(data, dict) else data
    models = []
    for m in raw:
        if isinstance(m, dict):
            mid = m.get("id") or m.get("name") or ""
            owned = m.get("owned_by", "") or ""
        else:
            mid, owned = str(m), ""
        if not mid:
            continue
        price = _resolve_model_price(m, mid)
        models.append(
            {"id": mid, "owned_by": owned, "cats": _classify_model(mid), "price": price}
        )
    models.sort(key=lambda x: x["id"])
    return {"ok": True, "models": models}


# ============================================================
# Embedding（向量化 / 语义召回）
# AstrBot 原生 EmbeddingProvider 接口：get_embedding(text)->vec、
# get_embeddings(list)->list[vec]、get_dim()。也兼容社区
# astrbot_plugin_embedding_adapter（get_embedding_async 等）。
# 取不到 provider 时调用方优雅降级（记忆按原序/权重）。
# ============================================================
_EMBED_CAPS = ("get_embeddings", "get_embedding",
               "get_embeddings_async", "get_embedding_async")


def cosine_similarity(a, b) -> float:
    """余弦相似度；维度不一致或零向量返回 0.0。"""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def rank_indices_by_vector(query_vec: list, vecs: list, top_n: int) -> list:
    """按与 query_vec 的余弦相似度降序，返回与 vecs 对齐的索引列表。"""
    scored = []
    for i, v in enumerate(vecs):
        if v:
            scored.append((cosine_similarity(v, query_vec), i))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [i for _, i in scored[:top_n]]


async def get_embedding_provider(ctx, provider_id: str = "") -> object:
    """获取 Embedding provider。

    优先级：指定 id → 社区 astrbot_plugin_embedding_adapter →
    AstrBot 原生 embedding provider 实例。无则返回 None。
    """
    # 1) 指定 id（兼容 AstrBot Dashboard 复合 Provider ID：服务商/模型）
    if provider_id and hasattr(ctx, "get_provider_by_id"):
        for candidate in _provider_id_candidates(provider_id):
            p = await _resolve(ctx.get_provider_by_id(candidate))
            if p and any(hasattr(p, c) for c in _EMBED_CAPS):
                return p
    # 2) 社区通用适配器
    if hasattr(ctx, "get_registered_star"):
        try:
            adapter = await _resolve(ctx.get_registered_star("astrbot_plugin_embedding_adapter"))
            if adapter and any(hasattr(adapter, c) for c in _EMBED_CAPS):
                if not provider_id or getattr(adapter, "provider_name", "") == provider_id:
                    return adapter
        except Exception:
            pass
    # 3) AstrBot 原生 embedding provider 实例
    pm = getattr(ctx, "provider_manager", None)
    if pm:
        for attr in ("embedding_provider_insts", "embed_provider_insts"):
            insts = getattr(pm, attr, None)
            if insts:
                for p in insts:
                    if any(hasattr(p, c) for c in _EMBED_CAPS):
                        return p
    return None


async def embed_texts(provider, texts: list, plugin_cfg: Optional[dict] = None) -> list:
    """把文本列表向量化，返回 List[List[float]]。失败/缺 provider 返回 []。

    v4.8.141 起完全走 AstrBot embedding provider，插件不再持有直连凭据。
    ``plugin_cfg`` 参数保留以兼容既有调用签名，不再消费。
    """
    if not provider or not texts:
        return []
    if hasattr(provider, "get_embeddings"):
        try:
            res = await _resolve(provider.get_embeddings(texts))
            if res:
                return res
        except Exception as e:
            logger.debug(f"[mingbu] embed(批量)失败，转单条: {e}")
    if hasattr(provider, "get_embeddings_async"):
        try:
            res = await _resolve(provider.get_embeddings_async(texts))
            if res:
                return res
        except Exception:
            pass
    single = getattr(provider, "get_embedding", None) or getattr(provider, "get_embedding_async", None)
    if single:
        out = []
        for t in texts:
            try:
                v = await _resolve(single(t))
                out.append(v or [])
            except Exception:
                out.append([])
        return out
    return []


def _match_provider_by_name(ctx, provider_id: str):
    """按实例 id 或 name 在 provider_manager 实例列表里匹配 Provider。

    用于 ``get_provider_by_id`` 取不到（配置填的是可读名称、或版本差异）时
    的兜底。仅遍历属性式实例列表（同步、无副作用），不调用可能阻塞/抛错的
    方法式枚举，避免影响主请求路径。
    """
    if not provider_id:
        return None
    pm = getattr(ctx, "provider_manager", None) if ctx else None
    if not pm:
        return None
    for attr in ("llm_provider_insts", "llm_providers", "provider_insts"):
        lst = getattr(pm, attr, None)
        if not isinstance(lst, (list, tuple)):
            continue
        for p in lst:
            pid = _extract_provider_id(p) or ""
            pname = _extract_provider_name(p) or ""
            if pid == provider_id or pname == provider_id:
                return p
    return None


def _resolve_astrbot_dashboard(plugin_cfg: dict):
    """解析 AstrBot Dashboard 地址与 Key，用于通过 HTTP API 拉取服务商列表。

    优先级：① 命灵配置 ``astrbot_dashboard_url``（用户显式填写，通常为自签/公网
    地址，跳过证书验证；裸 ``IP:端口`` / ``域名:端口`` 会自动补 ``http://``）；
    ② 回退本机默认 ``http://127.0.0.1:6185``（AstrBot Dashboard 默认端口，
    http 无需证书）。返回 ``(base, key, ssl_skip)``。
    """
    cfg = plugin_cfg or {}
    url = (cfg.get("astrbot_dashboard_url") or "").strip()
    key = (cfg.get("astrbot_api_key") or "").strip()
    if url:
        # 用户常填「192.168.x.x:6185」这类裸地址，缺协议前缀会让 aiohttp 抛
        # Invalid URL (No scheme supplied)、静默拉取为空。这里自动补 http://；
        # 需要 https 的地址请显式填写（含 https:// 前缀）。
        if "://" not in url:
            url = "http://" + url
        return url.rstrip("/"), key, True
    return "http://127.0.0.1:6185", "", False


async def _fetch_llm_providers_via_api(base: str, key: str, ssl_skip: bool) -> list:
    """通过 AstrBot HTTP API ``/api/v1/providers`` 拉取 LLM 服务商。

    结构稳定、响应对象明确带 ``id`` 字段，不依赖 ``provider_manager`` 进程内实例
    的属性猜测。只保留文本生成类（``provider_type == "chat_completion"`` 或未声明
    ``provider_type`` 的兜底），排除 embedding/rerank/tts/stt。返回
    ``[{"id","name","provider","model"}]``。请求失败抛异常由调用方捕获并回退。
    """
    from .http_client import request_json

    url = base.rstrip("/") + "/api/v1/providers"
    ssl = False if ssl_skip else None
    data = await request_json(None, "GET", url, api_key=key, timeout=20, ssl=ssl, breaker=_DASHBOARD_BREAKER)
    if not isinstance(data, dict):
        return []
    provs = (data.get("data") or {}).get("providers") or data.get("providers") or []
    out = []
    seen = set()
    for p in provs:
        if not isinstance(p, dict):
            continue
        ptype = p.get("provider_type")
        # 只列文本生成类；排除专用能力（embedding/rerank/tts/stt）
        if ptype not in (None, "chat_completion"):
            continue
        pid = p.get("id") or p.get("provider_id")
        if not pid:
            continue
        pid = str(pid)
        if pid in seen:
            continue
        seen.add(pid)
        provider = p.get("provider") or ""
        model = p.get("model") or ""
        name = f"{provider} / {model}" if model else (provider or pid)
        out.append({"id": pid, "name": name, "provider": provider, "model": model})
    return out
