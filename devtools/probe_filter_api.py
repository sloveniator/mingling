"""针对 AstrBot 各版本的 filter API 探测（验证插件装饰器是否兼容）。
只检查 API 形态，不依赖插件包结构；需在有真实 astrbot 的环境运行。
"""

def check(name, fn):
    try:
        ok = fn()
        print(f"[OK]   {name}: {ok}")
    except Exception as e:
        print(f"[FAIL] {name}: {type(e).__name__}: {e}")

# 1. 导入 filter
def import_filter():
    import importlib

    importlib.import_module("astrbot.api.event.filter")
    return "imported"

# 2. on_message 存在且可作为装饰器使用（无参调用 + 应用到 async 函数）
def on_message_decorator():
    from astrbot.api.event import filter
    if not hasattr(filter, "on_message"):
        return "MISSING on_message"
    deco = filter.on_message()

    async def handler(self, event):
        return None

    wrapped = deco(handler)
    # 同时测试堆叠 event_message_type
    if hasattr(filter, "event_message_type"):
        deco2 = filter.event_message_type(getattr(filter.EventMessageType, "ALL", None) if hasattr(filter, "EventMessageType") else None)
        wrapped2 = deco2(wrapped)
        return f"on_message() ok; stacked event_message_type ok -> {wrapped2.__name__}"
    return f"on_message() ok -> {wrapped.__name__}"

# 3. EventMessageType 是否存在
def event_message_type_enum():
    from astrbot.api.event import filter
    if not hasattr(filter, "EventMessageType"):
        return "MISSING EventMessageType"
    return "EventMessageType present: " + ", ".join(
        [n for n in dir(filter.EventMessageType) if not n.startswith("_")]
    )

# 4. 其它常见装饰器
def other_decorators():
    from astrbot.api.event import filter
    names = ["command", "regex", "on_llm_request", "on_llm_response", "permission_type", "platform_adapter_type"]
    present = [n for n in names if hasattr(filter, n)]
    return "present: " + ", ".join(present)

if __name__ == "__main__":
    print("=== AstrBot filter API 探测 ===")
    check("import filter", import_filter)
    check("on_message 装饰器可用", on_message_decorator)
    check("EventMessageType 枚举", event_message_type_enum)
    check("其它装饰器", other_decorators)
    print("=== 完成 ===")
