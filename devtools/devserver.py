"""
devtools/devserver.py -- 管理面板本地联调服务器
================================================
把 `stars/admin.py` 注册的全部 Web API 挂到一个真实的本地 HTTP 服务上，
并托管 `pages/admin` 静态资源，使管理面板可以在浏览器里直接点。

为什么需要它：
  AstrBot 的 Plugin Pages 面板在真实运行时里通过宿主注入的
  `window.AstrBotPluginPage` 桥接对象访问后端。脱离 AstrBot 就没有这个
  对象，前端连一个字节都拿不到。这里补上等价的桥接层（`/__bridge.js`），
  前端源码**一行都不用改**即可联调。

用法：
    python devtools/devserver.py               # 起服务 + 播种演示数据
    python devtools/devserver.py --port 8848
    python devtools/devserver.py --fresh       # 清空本地数据后重新播种
    python devtools/devserver.py --no-seed     # 空库启动（验证空态）

数据落在 devtools/.devdata.json，不会污染真实部署。
"""
from __future__ import annotations

import argparse
import asyncio
import importlib
import json
import mimetypes
import os
import random
import sys
import threading
import time
import traceback
import types
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict
from urllib.parse import parse_qs, unquote, urlparse

_PLUGIN_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_PKG = "astrbot_plugin_mingbu"

sys.path.insert(0, _PLUGIN_DIR)

# 与 tests/conftest.py 同款引导：先登记包，再以包内全名加载桩。
# 写成 `from devtools.astrbot_stub import ...` 会让 devtools 先以扁平名
# 加载一份，bootstrap 时再以包名加载第二份，_CURRENT_REQUEST 随之分裂，
# dev server 里 handler 会读不到刚设置的请求。
if _PKG not in sys.modules:
    _pkg = types.ModuleType(_PKG)
    _pkg.__path__ = [_PLUGIN_DIR]
    _pkg.__package__ = _PKG
    sys.modules[_PKG] = _pkg

_stub = importlib.import_module(f"{_PKG}.devtools.astrbot_stub")

PLUGIN_DIR = _stub.PLUGIN_DIR
StubRequest = _stub.StubRequest
StubResponse = _stub.StubResponse
reset_current_request = _stub.reset_current_request
set_current_request = _stub.set_current_request

_stub.install_astrbot_stub()
_stub.bootstrap_plugin_modules()

PAGES_DIR = os.path.join(PLUGIN_DIR, "pages", "admin")
DATA_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".devdata.json")


# ══════════════════════════════════════════════════════════════
# 持久化 KV：内存 dict + JSON 落盘
# ══════════════════════════════════════════════════════════════

class DevKV:
    """把 KV 落到单个 JSON 文件，重启后数据仍在。"""

    def __init__(self, path: str):
        self.path = path
        self.data: Dict[str, Any] = {}
        self._dirty = False
        self.reads = 0
        self.writes = 0
        self.load()

    def load(self):
        if os.path.exists(self.path):
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    self.data = json.load(f)
            except Exception as e:
                print(f"  [kv] 读取 {self.path} 失败，按空库启动: {e}")
                self.data = {}

    def save(self):
        if not self._dirty:
            return
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.data, f, ensure_ascii=False, indent=1)
        os.replace(tmp, self.path)
        self._dirty = False

    def get(self, key, default=None):
        self.reads += 1
        return self.data.get(key, default)

    def put(self, key, value):
        self.writes += 1
        self.data[key] = value
        self._dirty = True

    def clear(self):
        self.data = {}
        self._dirty = True
        self.save()


KV = DevKV(DATA_FILE)


# ══════════════════════════════════════════════════════════════
# Context 桩：收集 register_web_api 注册的路由
# ══════════════════════════════════════════════════════════════

class DevContext:
    """最小 Context：只实现管理面板真正会用到的部分。"""

    def __init__(self):
        self.routes: Dict[str, Dict[str, Any]] = {}
        self._providers = []

    def register_web_api(self, route, handler, methods, desc=""):
        self.routes[route] = {
            "handler": handler,
            "methods": [m.upper() for m in methods],
            "desc": desc,
        }

    # 管理面板不会触发 LLM，给个安全兜底即可
    def get_using_provider(self, *a, **k):
        return None

    def get_all_providers(self):
        return []

    def get_registered_star(self, *a, **k):
        return None

    def get_runtime_id(self):
        return "devserver"


# ══════════════════════════════════════════════════════════════
# Star 实例：接上 DevKV
# ══════════════════════════════════════════════════════════════

def _build_star(context: DevContext):
    from astrbot_plugin_mingbu.stars.admin import StarAdmin

    class DevStarAdmin(StarAdmin):
        async def get_kv_data(self, key, default=None):
            return KV.get(key, default)

        async def put_kv_data(self, key, value):
            KV.put(key, value)

    # 关掉一切会发外部请求 / 起定时任务的模块，本地联调只关心面板
    cfg = {
        "background_watch_enabled": False,
        "self_evolution_enabled": False,
        "proactive_enabled": False,
        "news_enabled": False,
        "dream_engine_enabled": True,
        "growth_engine_enabled": True,
        "social_graph_enabled": True,
        "character_collection_enabled": True,
        "token_monitor_enabled": True,
        "group_slang_enabled": True,
        "conversation_inspect_enabled": True,
    }
    return DevStarAdmin(context, cfg)


# ══════════════════════════════════════════════════════════════
# 演示数据播种
# ══════════════════════════════════════════════════════════════

SEED_USERS = [
    # (uid, 昵称, 关系分, 互动数, 模式)
    ("wxid_a1b2c3d4", "林月见", 78.5, 214, "attached"),
    ("wxid_zhang_wei", "张伟", 52.0, 96, "warming"),
    ("tg_10086", "Nova", 41.2, 63, "warming"),
    ("qq_874512", "老陈", 12.4, 18, "normal"),
    ("wxid_shen_ling", "沈凌", -8.0, 34, "hurt"),
    ("qq_223344", "小满", 5.0, 7, "normal"),
]

SEED_DIALOG = [
    ("wxid_a1b2c3d4", "user", "我最近老是梦到自己在爬一座爬不完的楼梯，是不是压力太大了", 1),
    ("wxid_a1b2c3d4", "bot", "重复出现的攀爬梦，多半和「持续消耗但看不到终点」的状态有关。你最近工作节奏是不是一直没停过？", 0),
    ("wxid_a1b2c3d4", "user", "对，连着加了三周班了，周末也在改方案", 1),
    ("wxid_a1b2c3d4", "user", "我生日是1996年3月17号，早上七点多出生的", 1),
    ("wxid_a1b2c3d4", "bot", "记下了。丙子年辛卯月，卯时。日后起盘我直接用这组。", 0),
    ("wxid_zhang_wei", "user", "帮我看看今天适不适合签合同", 1),
    ("wxid_zhang_wei", "bot", "今日冲鼠煞北，若对方属鼠可另择时。上午巳时相对稳妥。", 0),
    ("wxid_zhang_wei", "user", "行，那我约十点半", 0),
    ("tg_10086", "user", "塔罗抽一张，问下个季度的项目走向", 1),
    ("tg_10086", "bot", "抽到「宝剑八」逆位——束缚正在松动，你其实比自己以为的更有余地。", 0),
    ("tg_10086", "user", "有点准，我确实一直觉得动不了", 1),
    ("qq_874512", "user", "在吗", 0),
    ("qq_874512", "bot", "在的。", 0),
    ("wxid_shen_ling", "user", "你上次说的那个根本没用，别再给我发这些了", 1),
    ("wxid_shen_ling", "bot", "抱歉，那次的建议确实不贴合你的情况。我先不打扰了。", 0),
    ("qq_223344", "user", "我妈总说我这个八字克夫，是真的吗", 1),
]

SEED_GROUP_DIALOG = [
    ("wxid_a1b2c3d4", "group_708", "今晚团建谁去", "林月见"),
    ("wxid_zhang_wei", "group_708", "我加班，去不了", "张伟"),
    ("qq_223344", "group_708", "+1 想去", "小满"),
    ("wxid_a1b2c3d4", "group_912", "这个需求又改了第五版", "林月见"),
]


async def seed(star, force: bool = False):
    """写入演示数据。已有数据时默认跳过。"""
    from astrbot_plugin_mingbu.core import Relationship

    existing = await star._active_uids()
    if existing and not force:
        print(f"  [seed] 已有 {len(existing)} 个用户，跳过播种（--fresh 可重来）")
        return

    print("  [seed] 播种演示数据…")
    now = time.time()

    for uid, name, score, count, mode in SEED_USERS:
        await star._remember_user(uid)
        rel = Relationship(uid)
        rel.score = score
        rel.interaction_count = count
        rel.mode = mode
        # 最近一次互动散布在过去 10 天内，让「近期互动」有层次
        last = now - random.uniform(0, 10 * 86400)
        for attr in ("last_interaction", "last_seen", "last_time"):
            if hasattr(rel, attr):
                try:
                    setattr(rel, attr, last)
                except Exception:
                    pass
        await star._save_relationship(rel)

        profile = await star._get_profile(uid)
        profile["name"] = name          # 平台昵称
        profile.setdefault("birth", {})["name"] = name
        await star._save_profile(uid, profile)

    insp = star.core.conversation_inspector
    if insp:
        for uid, msg_type, content, _weighted in SEED_DIALOG:
            await insp.log(uid=uid, content=content, msg_type=msg_type,
                           platform="dev", session_id=f"s_{uid}")
        for uid, gid, content, sender in SEED_GROUP_DIALOG:
            await insp.log(uid=uid, content=content, msg_type="user",
                           gid=gid, is_group=True, platform="dev",
                           sender_name=sender, session_id=f"g_{gid}")
        await insp.flush_all()

    social = star.core.social
    if social:
        for gid, title in (("group_708", "产品三组"), ("group_912", "需求评审")):
            try:
                await social.ensure_group(gid, title)
            except Exception:
                pass

    KV.save()
    print(f"  [seed] 完成：{len(SEED_USERS)} 用户 / "
          f"{len(SEED_DIALOG) + len(SEED_GROUP_DIALOG)} 条对话")


# ══════════════════════════════════════════════════════════════
# 后台 asyncio 事件循环
# ══════════════════════════════════════════════════════════════

class AsyncRuntime:
    """在独立线程里跑一个常驻事件循环，供 HTTP 线程投递协程。"""

    def __init__(self):
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _run(self):
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

    def run(self, coro, timeout: float = 30.0):
        fut = asyncio.run_coroutine_threadsafe(coro, self.loop)
        return fut.result(timeout=timeout)

    def stop(self):
        self.loop.call_soon_threadsafe(self.loop.stop)


async def _invoke(handler, req: "StubRequest"):  # type: ignore[valid-type]
    """在协程上下文里绑定当前请求后调用 handler。

    用 ContextVar 而非全局变量：run_coroutine_threadsafe 会为每个协程
    复制一份 context，因此并发请求之间不会互相覆盖参数。
    """
    token = set_current_request(req)
    try:
        return await handler()
    finally:
        reset_current_request(token)


# ══════════════════════════════════════════════════════════════
# 桥接层：等价于 AstrBot 宿主注入的 window.AstrBotPluginPage
# ══════════════════════════════════════════════════════════════

BRIDGE_JS = """/* devserver 注入：等价于 AstrBot 宿主的 window.AstrBotPluginPage */
(function () {
  const BASE = "/api/";
  async function call(method, endpoint, payload) {
    let url = BASE + endpoint;
    const opt = { method, headers: { "Content-Type": "application/json" } };
    if (method === "GET") {
      const qs = new URLSearchParams();
      Object.entries(payload || {}).forEach(([k, v]) => {
        if (v !== undefined && v !== null && v !== "") qs.append(k, v);
      });
      const s = qs.toString();
      if (s) url += "?" + s;
    } else {
      opt.body = JSON.stringify(payload || {});
    }
    const t0 = performance.now();
    let res;
    try {
      res = await fetch(url, opt);
    } catch (e) {
      console.error("[bridge] 网络失败", endpoint, e);
      return { status: "error", message: "网络请求失败：" + e.message };
    }
    const ms = (performance.now() - t0).toFixed(0);
    let data;
    try {
      data = await res.json();
    } catch (e) {
      console.error("[bridge] 响应非 JSON", endpoint, e);
      return { status: "error", message: "响应解析失败" };
    }
    console.debug(`[bridge] ${method} ${endpoint} -> ${res.status} (${ms}ms)`, data);
    return data;
  }
  window.AstrBotPluginPage = {
    apiGet: (endpoint, params) => call("GET", endpoint, params),
    apiPost: (endpoint, body) => call("POST", endpoint, body),
  };
  console.info("[bridge] AstrBotPluginPage 已就绪（devserver）");
})();
"""


# ══════════════════════════════════════════════════════════════
# HTTP 服务
# ══════════════════════════════════════════════════════════════

class Handler(BaseHTTPRequestHandler):
    server_version = "MingbuDevServer/1.0"
    runtime: "AsyncRuntime | None" = None      # 由 main 注入
    routes: Dict[str, Any] = {}
    plugin_name: str = "astrbot_plugin_mingbu"
    access_log: list = []

    def log_message(self, fmt, *args):
        pass  # 自己打，不要默认那套噪音

    # ── 响应工具 ──
    def _send_json(self, payload, status=200):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_bytes(self, body: bytes, ctype: str, status=200):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    # ── 路由分发 ──
    def do_GET(self):
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        if path.startswith("/api/"):
            query = {k: v[0] for k, v in parse_qs(parsed.query).items()}
            self._dispatch(path[len("/api/"):], "GET", query, None)
            return
        if path == "/__bridge.js":
            self._send_bytes(BRIDGE_JS.encode("utf-8"),
                             "application/javascript; charset=utf-8")
            return
        if path == "/__routes":
            self._send_json({
                "count": len(self.routes),
                "routes": sorted(self.routes.keys()),
            })
            return
        self._serve_static(path)

    def do_POST(self):
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        if not path.startswith("/api/"):
            self._send_json({"status": "error", "message": "not found"}, 404)
            return
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        try:
            body = json.loads(raw.decode("utf-8")) if raw else {}
        except Exception:
            body = {}
        query = {k: v[0] for k, v in parse_qs(parsed.query).items()}
        self._dispatch(path[len("/api/"):], "POST", query, body)

    def _dispatch(self, endpoint: str, method: str, query: dict, body):
        full_route = f"/{self.plugin_name}/{endpoint}"
        spec = self.routes.get(full_route)
        t0 = time.perf_counter()
        if spec is None:
            print(f"  [{method}] {endpoint}  -> 404 无此路由")
            self._send_json(
                {"status": "error", "message": f"未注册的路由: {endpoint}"}, 404)
            return
        if method not in spec["methods"]:
            self._send_json(
                {"status": "error",
                 "message": f"{endpoint} 不支持 {method}（支持 {spec['methods']}）"}, 405)
            return

        req = StubRequest(method=method, query=query, body=body)
        try:
            if self.runtime is None:
                raise RuntimeError("AsyncRuntime 尚未由 main 注入")
            result = self.runtime.run(_invoke(spec["handler"], req))
        except Exception as e:
            traceback.print_exc()
            self.access_log.append(
                {"endpoint": endpoint, "method": method, "status": 500, "error": repr(e)})
            print(f"  [{method}] {endpoint}  -> 500 {e!r}")
            self._send_json({"status": "error", "message": f"{type(e).__name__}: {e}"}, 500)
            return

        ms = (time.perf_counter() - t0) * 1000
        if isinstance(result, StubResponse):
            payload, status = result.payload, result.status_code
        else:
            payload, status = result, 200
        flag = "" if status < 400 else "  <-- 失败"
        print(f"  [{method}] {endpoint}  -> {status} ({ms:.0f}ms){flag}")
        self.access_log.append(
            {"endpoint": endpoint, "method": method, "status": status, "ms": round(ms, 1)})
        KV.save()
        self._send_json(payload, status)

    # ── 静态资源 ──
    def _serve_static(self, path: str):
        rel = path.lstrip("/") or "index.html"
        target = os.path.normpath(os.path.join(PAGES_DIR, rel))
        # 目录穿越防护
        if not target.startswith(PAGES_DIR):
            self._send_json({"status": "error", "message": "forbidden"}, 403)
            return
        if os.path.isdir(target):
            target = os.path.join(target, "index.html")
        if not os.path.exists(target):
            self._send_bytes(b"404 Not Found", "text/plain; charset=utf-8", 404)
            return

        ctype = mimetypes.guess_type(target)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype.endswith(("javascript", "json")):
            ctype += "; charset=utf-8"

        with open(target, "rb") as f:
            body = f.read()

        # 关键：桥接脚本必须先于 app.js 执行（app.js 第一行就读该对象）
        if target.endswith("index.html"):
            html = body.decode("utf-8")
            inject = '<script src="/__bridge.js"></script>'
            if "</head>" in html:
                html = html.replace("</head>", f"  {inject}\n</head>", 1)
            else:
                html = inject + html
            body = html.encode("utf-8")

        self._send_bytes(body, ctype)


# ══════════════════════════════════════════════════════════════

def main():
    ap = argparse.ArgumentParser(description="命卜管理面板本地联调服务器")
    ap.add_argument("--port", type=int, default=8848)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--fresh", action="store_true", help="清空本地数据重新播种")
    ap.add_argument("--no-seed", action="store_true", help="不播种，验证空态")
    args = ap.parse_args()

    if args.fresh:
        KV.clear()
        print("  [kv] 已清空本地数据")

    runtime = AsyncRuntime()
    ctx = DevContext()
    star = _build_star(ctx)

    print("── 初始化插件内核 ──")
    runtime.run(star.initialize(), timeout=60)
    print(f"  [core] 已注册 {len(ctx.routes)} 个 Web API 路由")

    if not args.no_seed:
        runtime.run(seed(star, force=args.fresh), timeout=120)
    KV.save()

    Handler.runtime = runtime
    Handler.routes = ctx.routes
    from astrbot_plugin_mingbu.core import PLUGIN_NAME
    Handler.plugin_name = PLUGIN_NAME

    httpd = ThreadingHTTPServer((args.host, args.port), Handler)
    url = f"http://{args.host}:{args.port}/"
    print("── 服务已启动 ──")
    print(f"  管理面板  {url}")
    print(f"  路由清单  {url}__routes")
    print("  Ctrl+C 停止\n")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n── 收尾 ──")
    finally:
        try:
            runtime.run(star.core.shutdown(), timeout=15)
        except Exception:
            pass
        KV.save()
        runtime.stop()
        print("  数据已落盘，再见")


if __name__ == "__main__":
    main()
