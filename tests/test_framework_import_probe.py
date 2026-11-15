"""全模块导入探针：框架回归护栏
=================================

在 conftest 桩引导下逐个 import 顶层模块与框架子包，守住两条不变量：
1. 无循环导入（import 顺序敏感的模块会在此暴露）；
2. 无导入期错误（模块级代码在桩环境下必须能干净执行）。

devtools 下是一次性迁移脚本（split_counseling.py 会 SystemExit(0)），
不属于运行时框架，跳过。
"""
import importlib
import pkgutil

_SKIP_PREFIX = ("devtools.devserver", "devtools.split_counseling", "devtools.migrate")

TOP_LEVEL = [
    "astrology", "background_watch", "bj_time", "bond", "character_collection",
    "character_pool", "client", "constants", "conversation_inspector", "core",
    "disguise_guard", "dream", "emotion_detect", "features", "formatters",
    "group_slang", "growth", "health", "http_client", "kv_cache", "kv_store",
    "main", "memory_core", "metrics", "news_search", "persona", "proactive",
    "provider_router", "self_evolution", "silent_divination", "social", "stt",
    "tarot", "technique_kb", "token_monitor", "tts_config", "util",
    "worldview_adapter", "ziwei_kb",
]

FRAMEWORK_PKGS = ("stars", "star_mixins", "hooks", "counseling", "stars.apis")


def test_import_all_top_level_modules():
    import conftest  # noqa: F401 - 桩引导副作用

    fails = []
    for m in TOP_LEVEL:
        try:
            importlib.import_module(m)
        except Exception as e:  # noqa: BLE001
            fails.append(f"{m}: {type(e).__name__}: {e}")
    assert not fails, "顶层模块导入失败:\n" + "\n".join(fails)


def test_import_all_framework_subpackages():
    import conftest  # noqa: F401

    for pkg in FRAMEWORK_PKGS:
        mod = importlib.import_module(pkg)
        for info in pkgutil.iter_modules(mod.__path__, mod.__name__ + "."):
            if info.name.startswith(_SKIP_PREFIX):
                continue
            importlib.import_module(info.name)
