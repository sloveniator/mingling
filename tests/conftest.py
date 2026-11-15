"""
tests/conftest.py -- 测试环境引导
=================================
astrbot 在 CI/本地测试环境未必安装，因此需要在测试收集前注入一个最小桩，
并通过自定义 import finder 让源码里的相对导入（from .astrology import ...）
也能正常解析。

这套桩原先内联在本文件里，导致两个问题：
  1. `astrbot.api.web` 从未被打桩，`stars/admin.py` 的 31 个 Web API
     因此长期零测试覆盖；
  2. devtools/devserver.py 想复用同一套桩时只能整段抄。

现已统一下沉到 devtools/astrbot_stub.py，测试与本地开发服务器共用同一份。
真实部署仍在完整 AstrBot 运行时下进行，本桩不影响运行行为。
"""
import importlib
import os
import sys
import types

_PLUGIN_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_PKG = "astrbot_plugin_mingbu"

sys.path.insert(0, _PLUGIN_DIR)

# 先把插件目录登记成包，再以包内全名加载桩模块。
#
# 这里不能写 `from devtools.astrbot_stub import ...`：那样 devtools 会作为
# 扁平顶层包先加载一份，随后 bootstrap_plugin_modules() 又以
# astrbot_plugin_mingbu.devtools 加载第二份。同一份源码执行两遍，
# _CURRENT_REQUEST 这类模块级 ContextVar 就会分裂成两个——测试设置的请求
# 与 Web handler 读到的请求分属不同副本，管理面板 API 测试会莫名其妙拿到
# LookupError。tests/test_astrbot_stub_identity.py 守的正是这条不变量。
if _PKG not in sys.modules:
    _pkg = types.ModuleType(_PKG)
    _pkg.__path__ = [_PLUGIN_DIR]
    _pkg.__package__ = _PKG
    sys.modules[_PKG] = _pkg

_stub = importlib.import_module(f"{_PKG}.devtools.astrbot_stub")

StubRequest = _stub.StubRequest
StubResponse = _stub.StubResponse
install_astrbot_stub = _stub.install_astrbot_stub
bootstrap_plugin_modules = _stub.bootstrap_plugin_modules
reset_current_request = _stub.reset_current_request
set_current_request = _stub.set_current_request

install_astrbot_stub()
bootstrap_plugin_modules()

# 供 Web API 测试直接使用（tests/test_admin_api.py）
__all__ = [
    "StubRequest",
    "StubResponse",
    "set_current_request",
    "reset_current_request",
]
