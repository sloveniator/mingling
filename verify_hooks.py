"""
mingling 钩子验证脚本
==================================
将本文件放到 AstrBot 的 data/plugins/mingling/ 目录下，
在 AstrBot Python 环境中运行以验证钩子注入是否正常工作。

用法（在 AstrBot 容器内）：
    cd /AstrBot/data/plugins/mingling
    python verify_hooks.py

或在宿主机：
    docker exec astrbot python /AstrBot/data/plugins/mingling/verify_hooks.py
"""

import importlib
import sys


def section(title: str):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}")

def ok(msg: str):
    print(f"  [PASS] {msg}")

def warn(msg: str):
    print(f"  [WARN] {msg}")

def fail(msg: str):
    print(f"  [FAIL] {msg}")


# ── 1. 模块导入检查 ──

section("1. 模块导入检查")

try:
    from astrbot.api.event import AstrMessageEvent, MessageEventResult, filter
    ok("astrbot.api.event 导入成功")
except ImportError as e:
    fail(f"astrbot.api.event 导入失败: {e}")
    sys.exit(1)

try:
    from astrbot.api.star import Context, Star
    ok("astrbot.api.star 导入成功")
except ImportError as e:
    fail(f"astrbot.api.star 导入失败: {e}")

try:
    from astrbot.api.provider import ProviderRequest
    ok("astrbot.api.provider.ProviderRequest 导入成功")
except ImportError as e:
    fail(f"astrbot.api.provider.ProviderRequest 导入失败: {e}")
    fail("这意味着 on_llm_request 钩子无法正确工作！")

try:
    from astrbot.api import logger
    ok("astrbot.api.logger 导入成功")
except ImportError as e:
    warn(f"astrbot.api.logger 导入失败: {e} (非致命)")

try:
    import aiohttp
    ok("aiohttp 导入成功")
except ImportError:
    fail("aiohttp 未安装！请运行: pip install aiohttp")

# ── 2. 插件主模块结构检查 ──

section("2. 插件主模块结构检查")

try:
    from main import MinglingPlugin
    ok("MinglingPlugin 类存在")
except ImportError as e:
    fail(f"无法导入 MinglingPlugin: {e}")
    sys.exit(1)

from main import MinglingPlugin

# 检查 on_llm_request 钩子
found_hook = False
for name in dir(MinglingPlugin):
    if name == 'inject_state_layer' or name.startswith('_on_llm') or name == '_on_llm_request':
        found_hook = True
        ok(f"发现 on_llm_request 钩子: {name}")
        method = getattr(MinglingPlugin, name)
        # 检查签名
        import inspect
        sig = inspect.signature(method)
        params = list(sig.parameters.keys())
        expected = ['self', 'event', 'req']
        if params[:3] == expected:
            ok(f"  签名正确: {params[:3]} == {expected}")
        else:
            fail(f"  签名错误: {params[:3]} != {expected}（应为 self, event, req）")
        break

if not found_hook:
    fail("未找到 on_llm_request 钩子方法！")

# 检查是否还存在 on_decorating_result 钩子（应该已被删除）
found_old = False
for name in dir(MinglingPlugin):
    if 'prophetic' in name.lower() or 'decorating' in name.lower():
        found_old = True
        fail(f"发现已废弃的钩子: {name}（应已删除）")

if not found_old:
    ok("未发现已废弃的 on_decorating_result 钩子（正确）")

# ── 3. ProviderRequest 接口检查 ──

section("3. ProviderRequest 接口检查")

try:
    req_attrs = dir(ProviderRequest)
    checks = {
        'system_prompt': 'system_prompt' in req_attrs,
        'extra_user_content_parts': 'extra_user_content_parts' in req_attrs,
    }
    for attr, exists in checks.items():
        if exists:
            ok(f"ProviderRequest.{attr} 存在")
        else:
            fail(f"ProviderRequest.{attr} 不存在！（注入会失败）")
except Exception as e:
    fail(f"ProviderRequest 接口检查失败: {e}")

# ── 4. 咨询引擎模块检查 ──

section("4. 咨询引擎模块检查")

try:
    from counseling import EMOTION_KEYWORDS, CounselingPrompts, EmotionalTracker
    ok("counseling 模块导入成功")
except ImportError as e:
    fail(f"counseling 模块导入失败: {e}")

try:
    tracker = EmotionalTracker()
    ok("EmotionalTracker 实例化成功")
except Exception as e:
    fail(f"EmotionalTracker 实例化失败: {e}")

try:
    signals = EmotionalTracker.detect_emotions("最近压力好大，不知道怎么办")
    if signals:
        ok(f"情绪检测正常: {signals}")
    else:
        warn("情绪检测返回空列表（检测文本可能不够典型）")
except Exception as e:
    fail(f"情绪检测失败: {e}")

# ── 5. 装饰器语法检查 ──

section("5. 装饰器语法检查")

import inspect
import textwrap

source = inspect.getsource(MinglingPlugin._on_llm_request)
if '@filter.on_llm_request()' in source:
    ok("装饰器语法: @filter.on_llm_request() （正确）")
elif '@filter.on_llm_request' in source:
    warn("装饰器语法: @filter.on_llm_request（缺少括号，可能不生效）")
else:
    fail("未找到 @filter.on_llm_request 装饰器")

# ── 6. 注入逻辑检查 ──

section("6. 注入逻辑检查")

if 'extra_user_content_parts' in source:
    ok("注入目标: req.extra_user_content_parts （正确，避免破坏缓存）")
elif 'system_prompt' in source:
    warn("注入目标: req.system_prompt（每轮变化会破坏 LLM 缓存，增加费用）")
else:
    fail("未找到注入目标")

# ── 总结 ──

section("验证总结")
print()
print("  如果所有 [PASS] 项通过，钩子注入机制已正确配置。")
print("  部署后可通过以下日志确认钩子正在工作：")
print()
print("    docker logs astrbot 2>&1 | grep '\\[mingbu\\] 已注入'")
print()
print("  预期输出（心理咨询钩子）：")
print('    [mingbu] 已注入心理咨询钩子 -> <user_id> (xxx 字)')
print()
print("  预期输出（预言祝福）：")
print('    [mingbu] 已注入预言祝福 -> <user_id> (xxx 字)')
print()
print("  如果看到 '[mingbu] ... 注入失败'，请检查上面的 [FAIL] 项。")
