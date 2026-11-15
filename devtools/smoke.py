"""
devtools/smoke.py -- 对本地 devserver 的全路由冒烟探测
=====================================================
逐个请求 31 个 Web API，打印状态与响应摘要，快速定位 500 / 空数据 / 字段缺失。

    python devtools/smoke.py [--port 8848]
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.parse
import urllib.request

BASE = "http://127.0.0.1:8848"


def call(method: str, endpoint: str, payload=None):
    url = f"{BASE}/api/{endpoint}"
    data = None
    headers = {"Content-Type": "application/json"}
    if method == "GET":
        if payload:
            url += "?" + urllib.parse.urlencode(payload)
    else:
        data = json.dumps(payload or {}).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8"))
        except Exception:
            return e.code, {"_raw": "<unparsable>"}
    except Exception as e:
        return -1, {"_err": repr(e)}


def brief(obj, width=110):
    s = json.dumps(obj, ensure_ascii=False)
    return s if len(s) <= width else s[:width] + "…"


CASES = [
    # (method, endpoint, payload, 说明)
    ("GET", "stats", None, "总览统计"),
    ("GET", "users", None, "用户列表"),
    ("GET", "user", {"uid": "wxid_a1b2c3d4"}, "用户详情"),
    ("GET", "user", {"uid": ""}, "用户详情·空 uid（应 400）"),
    ("GET", "graph", None, "关系网"),
    ("GET", "recent_activity", None, "近期互动"),
    ("GET", "token_stats", None, "Token 统计"),
    ("GET", "config", None, "配置读取"),
    ("GET", "collection/progress", {"uid": "wxid_a1b2c3d4"}, "收集进度"),
    ("GET", "collection/progress", None, "收集进度·空 uid（应 400）"),
    ("GET", "collection/all", None, "收集全览"),
    ("GET", "worldview/presets", None, "世界观预设"),
    ("GET", "worldview/custom", None, "自定义世界观读"),
    ("GET", "slang/groups", None, "黑话·群列表"),
    ("GET", "slang/list", {"gid": "group_708"}, "黑话·列表"),
    ("GET", "kv", {"key": "mingbu.users"}, "KV 读取"),
    ("GET", "conversation/list", None, "对话列表"),
    ("GET", "conversation/list", {"uid": "wxid_a1b2c3d4"}, "对话列表·按用户"),
    ("GET", "conversation/list", {"search": "梦"}, "对话列表·搜索"),
    ("GET", "conversation/list", {"msg_type": "bot"}, "对话列表·按类型"),
    ("GET", "conversation/list", {"learned_only": "true"}, "对话列表·仅已学"),
    ("GET", "conversation/list", {"limit": "3", "offset": "2"}, "对话列表·分页"),
    ("GET", "conversation/stats", None, "对话统计"),
    ("GET", "conversation/knowledge", None, "已学知识"),
    ("GET", "conversation/weighted", {"uid": "wxid_a1b2c3d4"}, "高权重对话"),
    ("GET", "conversation/weighted", None, "高权重·空 uid（应 400）"),
    ("POST", "conversation/reweight", {"uid": "wxid_a1b2c3d4"}, "重算权重"),
    ("POST", "worldview/preview", {"preset": "xianxia"}, "世界观预览"),
    ("POST", "trigger", {"action": "dream", "uid": "wxid_a1b2c3d4"}, "手动触发·梦境"),
    ("POST", "collection/assign", {"uid": "qq_223344"}, "分配初始角色"),
    ("POST", "kv_set", {"key": "mingbu.devsmoke", "value": "1"}, "KV 写入"),
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8848)
    args = ap.parse_args()
    global BASE
    BASE = f"http://127.0.0.1:{args.port}"

    print(f"── 冒烟探测 {BASE} ──\n")
    ok, bad = 0, []

    for method, endpoint, payload, desc in CASES:
        status, body = call(method, endpoint, payload)
        expect_400 = "应 400" in desc
        good = (status == 400) if expect_400 else (200 <= status < 300)
        mark = "OK " if good else "!! "
        if good:
            ok += 1
        else:
            bad.append((endpoint, desc, status, brief(body, 200)))
        print(f"{mark}[{status:>3}] {desc:<24} {method:<4} {endpoint}")
        print(f"        {brief(body)}")

    # 依赖前一步结果的用例：先取一条真实 log_id 再做写操作
    print("\n── 依赖真实 ID 的写操作 ──")
    st, lst = call("GET", "conversation/list", {"limit": "1"})
    items = (lst or {}).get("items") or []
    if not items:
        print("!! 无法取得 log_id，跳过写操作验证")
        bad.append(("conversation/list", "取 log_id", st, brief(lst, 200)))
    else:
        log_id = items[0].get("id") or items[0].get("log_id")
        print(f"   使用 log_id = {log_id}")
        for method, endpoint, payload, desc in [
            ("POST", "conversation/learn", {"log_id": log_id, "note": "冒烟标注"}, "学习标注"),
            ("POST", "conversation/unlearn", {"log_id": log_id}, "取消学习"),
            ("POST", "conversation/extract",
             {"uid": "wxid_a1b2c3d4", "top_n": 10}, "批量入记忆"),
            ("POST", "conversation/delete", {"log_id": log_id}, "删除对话"),
        ]:
            status, body = call(method, endpoint, payload)
            good = 200 <= status < 300
            mark = "OK " if good else "!! "
            if good:
                ok += 1
            else:
                bad.append((endpoint, desc, status, brief(body, 200)))
            print(f"{mark}[{status:>3}] {desc:<24} {method:<4} {endpoint}")
            print(f"        {brief(body)}")

    print(f"\n── 结果：{ok} 通过，{len(bad)} 失败 ──")
    for endpoint, desc, status, body in bad:
        print(f"  !! {endpoint} ({desc}) status={status}")
        print(f"     {body}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
