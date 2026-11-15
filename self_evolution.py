import asyncio
import json
import re
from typing import List, Optional

from astrbot.api import logger

from .bj_time import CRON_TZ
from .memory_core import SEMANTIC
from .util import cleanup_stale_cron_jobs, spawn

# 并发上限：同时最多提炼 2 个用户，避免超量触发 Rate Limit。
#
# 惰性创建而非模块级构造：Python 3.9 的 asyncio.Semaphore.__init__ 会调用
# get_event_loop()，模块级构造等于把「导入本模块」和「当前线程存在事件循环」
# 绑死——在没有当前事件循环的同步上下文导入即抛 RuntimeError，会让插件加载
# 直接失败。改为首次在协程内使用时创建，3.9 与 3.10+ 行为一致。
_MAX_CONCURRENT_REFLECTIONS = 2
_SEMAPHORE: Optional[asyncio.Semaphore] = None


def _get_semaphore() -> asyncio.Semaphore:
    """返回（按需创建）并发闸门。仅在协程内调用，创建过程无 await 点。"""
    global _SEMAPHORE
    if _SEMAPHORE is None:
        _SEMAPHORE = asyncio.Semaphore(_MAX_CONCURRENT_REFLECTIONS)
    return _SEMAPHORE

PROMPT_TEMPLATE = """你是一个陪伴型 AI 的「自我进化」整理器。下面是一段与用户的近期对话片段。
请只提取用户**明确陈述**的、可被长期记住的「显性事实」，包括但不限于：
- 事实/近况（"我最近在备考"）
- 偏好（"我不喜欢被催"）
- 承诺/计划（"我周末要去爬山"）
- 重要关系/人名（"我姐叫小敏"）

规则：
1. 只提取用户自己说出来的，不要推断、不要脑补；
2. 每条是一句简洁的中文陈述，不要带"用户说"等前缀；
3. 若没有明显事实，返回空列表 []；
4. 最多 8 条。

输出严格为 JSON 数组（字符串列表），不要任何解释或 markdown：
["事实1", "事实2"]

对话片段：
{context}

JSON 数组："""


def build_reflection_prompt(context: str) -> str:
    """构造反思提炼 prompt。确保从最近的换行符截断，避免截断语义。"""
    # 截取最后 3500 字符，并从最近的换行符开始
    s = context[-3500:]
    idx = s.find('\n')
    if idx != -1:
        s = s[idx+1:]
    return PROMPT_TEMPLATE.format(context=s)


def parse_facts(raw: str) -> List[str]:
    """从 LLM 输出中稳健解析。"""
    if not raw:
        return []
    # 清理 markdown 和可能的额外文本
    text = re.sub(r"^```(?:json)?\s*", "", raw.strip(), flags=re.IGNORECASE)
    text = re.sub(r"\s*```$", "", text)

    # 尝试直接解析，如果失败则正则提取
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("[")
        end = text.rfind("]")
        if start == -1 or end == -1 or end <= start:
            return []
        try:
            data = json.loads(text[start : end + 1])
        except Exception:
            return []

    if not isinstance(data, list):
        return []
    return [str(x).strip() for x in data if isinstance(x, (str, int, float)) and str(x).strip()]


class SelfEvolution:
    """每晚把近期对话提炼为显性事实，回写持久记忆。"""

    def __init__(self, plugin, memory):
        self.plugin = plugin
        self.memory = memory

    async def register_jobs(self):
        cfg = getattr(self.plugin, "_config", {}) or {}
        # 注意 (k) or default 而非 get(k, default)：生产配置曾出现「键存在但为空串」，
        # get 的默认值对空值无效 → cron_expression="" → AstrBot 报
        # "recurring job missing cron_expression"（v4.9.0 线上事故）。
        cron_expr = (cfg.get("self_evolution_cron") or "").strip() or "0 4 * * *"
        cron = getattr(self.plugin.context, "cron_manager", None)
        if cron is not None:
            try:
                job = await cron.add_basic_job(
                    name="mingbu_self_evolution",
                    cron_expression=cron_expr,
                    handler=lambda: spawn(self.run_nightly(), name="self_evolution_nightly"),
                    description="命灵·自我进化（每晚事实提炼）",
                    persistent=True,
                    timezone=CRON_TZ,  # 显式北京时区（否则回退进程本地=UTC）
                )
                await cleanup_stale_cron_jobs(cron, "mingbu_self_evolution", job.job_id)
                logger.info("[mingbu.evo] 已注册自我进化任务")
            except Exception as e:
                logger.warning(f"[mingbu.evo] cron 注册失败: {e}")

    async def run_nightly(self):
        """对每位有记忆的用户提炼事实并回写。使用信号量控制并发。"""
        try:
            raw = await self.plugin.get_kv_data("mingbu.sessions", None)
            sessions = json.loads(raw) if raw else {}
        except Exception:
            sessions = {}

        tasks = [self._process_single_user(sid, meta) for sid, meta in sessions.items()]
        if tasks:
            await asyncio.gather(*tasks)

    async def _process_single_user(self, sid, meta):
        uid = meta.get("uid", "")
        if not uid or not self.memory:
            return

        async with _get_semaphore():
            try:
                recent = await self.memory.recall_recent(uid, n=20)
                if not recent:
                    return
                context = "\n".join(f"- {m.content[:120]}" for m in recent)
                prompt = build_reflection_prompt(context)

                # 指数退避重试逻辑
                for attempt in range(2):
                    try:
                        out = await self.plugin._llm_text(prompt, task_type="self_evolution")
                        facts = parse_facts(out)
                        for f in facts:
                            # fix(2026-11-05)：原调用 self.memory.add_memory(...)，
                            # 但 MemoryCore 只暴露 add()（grep 全仓：定义 0 处、
                            # 此调用 1 处）。该功能默认关闭且 run_nightly 无测试
                            # 覆盖，故长期未暴露；一旦开启，每晚提炼必抛
                            # AttributeError，导致「显性事实」断言从未真正落库。
                            await self.memory.add(
                                uid, SEMANTIC, f, source="self_evolution",
                                importance=0.7, emotional_weight=0.0,
                            )
                        if facts:
                            logger.info(f"[mingbu.evo] {uid} 成功提炼 {len(facts)} 条事实")
                        break
                    except Exception as e:
                        if attempt == 0:
                            await asyncio.sleep(2)
                        else:
                            logger.debug(f"[mingbu.evo] {uid} 提炼失败: {e}")
            except Exception as e:
                logger.debug(f"[mingbu.evo] {uid} 处理异常: {e}")
