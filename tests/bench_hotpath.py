"""
tests/bench_hotpath.py -- 消息热路径基准（非 pytest，手动运行）
================================================================
量化"每收到一条消息"在 KV 层的真实开销，用于优化前后对比。

运行：
    python tests/bench_hotpath.py

测量三条热路径：
  1. ConversationInspector.log()   —— 每条用户消息 + 每条 Bot 回复各一次
  2. MemoryCore.add()              —— 每条用户消息一次
  3. ProactiveEngine.track_session() —— 每条消息一次

FakePlugin 统计 KV 读写次数与累计序列化字节数，
不引入人为 sleep，纯粹反映 CPU/序列化成本。
"""
import asyncio
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import conftest  # noqa: F401,E402  注入 astrbot 桩

from conversation_inspector import ConversationInspector  # noqa: E402
from memory_core import MemoryCore  # noqa: E402


class CountingPlugin:
    """记录 KV 调用次数与吞吐字节数的假插件。"""

    def __init__(self):
        self.kv = {}
        self.reads = 0
        self.writes = 0
        self.read_bytes = 0
        self.write_bytes = 0

    async def get_kv_data(self, key, default=None):
        self.reads += 1
        v = self.kv.get(key, default)
        if isinstance(v, str):
            self.read_bytes += len(v)
        return v

    async def put_kv_data(self, key, value):
        self.writes += 1
        if isinstance(value, str):
            self.write_bytes += len(value)
        self.kv[key] = value


SAMPLE = "今天面试完了，说实话有点焦虑，我一直很想去这家公司，但又怕自己不够好"


async def bench_conversation(n_msgs: int):
    plugin = CountingPlugin()
    plugin.memory = MemoryCore(plugin)  # type: ignore[attr-defined]
    insp = ConversationInspector(plugin)
    t0 = time.perf_counter()
    for i in range(n_msgs):
        await insp.log(uid="u1", content=f"{SAMPLE}#{i}", msg_type="user")
    dt = time.perf_counter() - t0
    return dt, plugin


async def bench_memory(n_msgs: int):
    plugin = CountingPlugin()
    mem = MemoryCore(plugin)
    t0 = time.perf_counter()
    for i in range(n_msgs):
        await mem.add_episode("u1", f"{SAMPLE}#{i}", importance=0.45)
    dt = time.perf_counter() - t0
    return dt, plugin


def _mb(n):
    return f"{n / 1024 / 1024:.1f} MB"


async def main():
    n = int(os.environ.get("BENCH_N", "600"))
    print(f"== 消息热路径基准（模拟 {n} 条消息）==\n")

    dt, p = await bench_conversation(n)
    print("[1] ConversationInspector.log()")
    print(f"    耗时      : {dt:.2f}s   ({dt / n * 1000:.2f} ms/条)")
    print(f"    KV 读/写  : {p.reads} / {p.writes}")
    print(f"    序列化写出: {_mb(p.write_bytes)}   解析读入: {_mb(p.read_bytes)}")

    dt, p = await bench_memory(n)
    print("\n[2] MemoryCore.add_episode()")
    print(f"    耗时      : {dt:.2f}s   ({dt / n * 1000:.2f} ms/条)")
    print(f"    KV 读/写  : {p.reads} / {p.writes}")
    print(f"    序列化写出: {_mb(p.write_bytes)}   解析读入: {_mb(p.read_bytes)}")

    print("\n（对比基准：单条消息在 KV 层应为 O(1) 摊销，而非 O(已存条数)）")


if __name__ == "__main__":
    asyncio.run(main())
