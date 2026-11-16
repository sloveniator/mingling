import importlib
import sys
import types

pkg = "astrbot_plugin_mingbu"
BASE = r"E:\Programs\命灵\astrbot_mingbu"
if pkg not in sys.modules:
    m = types.ModuleType(pkg)
    m.__path__ = [BASE]
    sys.modules[pkg] = m

# 安装 astrbot 桩（与 tests/conftest.py 同源）
stub = importlib.import_module(f"{pkg}.devtools.astrbot_stub")
stub.install_astrbot_stub()

# 关键验证：core.feature_flags 是否被 core.py 抢占
try:
    ff = importlib.import_module(f"{pkg}.core.feature_flags")
    print("OK core.feature_flags imported, mode =",
          ff.FeatureFlags.from_config({"enabled": True}).mode)
except Exception as e:
    print("FAIL core.feature_flags:", type(e).__name__, e)

# 再看 core 本身解析到哪个
core = importlib.import_module(f"{pkg}.core")
print("core resolved to:", getattr(core, "__file__", None))
print("core has MingbuCore:", hasattr(core, "MingbuCore"))
