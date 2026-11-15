"""
tests/test_py39_compat.py -- Python 3.9 兼容性回归护栏
======================================================
`pyproject.toml` 声明 ``requires-python = ">=3.9"``，CI 矩阵也真实跑 3.9。
本文件把「为什么 3.9 会坏」的规则固化成可执行断言，防止改动悄悄破坏下限。

覆盖三类问题，其中第 1 类是真实踩过的坑：

1. **事件循环绑定的 asyncio 同步原语**
   Python 3.9 的 ``asyncio.Lock() / Event() / Semaphore() / Condition() /
   Queue()`` 在 ``__init__`` 里就会调用 ``get_event_loop()``。因此：

   - 在**模块级**构造 → 「导入该模块」被迫依赖「当前线程有事件循环」。
     ``asyncio.run()`` 结束后会把当前 loop 置空，此后导入即
     ``RuntimeError: There is no current event loop``，插件加载直接失败。
   - 在**同步函数/``__init__``** 里构造 → 同理，只要调用方不在协程上下文就崩。

   3.10 起这些类不再于 ``__init__`` 中取 loop，所以该缺陷在新版本上**完全不可见**，
   只有真跑 3.9 才会暴露——静态语法检查同样抓不到。
   正确写法是惰性创建（首次在协程内使用时构造）。

2. **PEP 604 联合类型注解**（``int | None``）
   在没有 ``from __future__ import annotations`` 时会于**导入期**求值，3.9 抛
   ``TypeError``。同样是纯语法检查抓不到的运行时问题。

3. **3.9 语法下限**
   用 ``ast.parse(feature_version=(3, 9))`` 兜底，拦截 match 语句等新语法。
"""
import ast
import os
import sys

import pytest

PLUGIN_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 这些 asyncio 原语在 3.9 的 __init__ 中会调用 get_event_loop()
LOOP_BOUND_PRIMITIVES = {
    "Lock", "Event", "Condition", "Semaphore", "BoundedSemaphore", "Queue",
    "PriorityQueue", "LifoQueue",
}

SKIP_DIRS = {
    "__pycache__", ".git", ".ruff_cache", ".pytest_cache", ".workbuddy",
    ".github", "node_modules", "venv", ".venv",
}


def _iter_source_files(include_tests: bool = False):
    """遍历插件内的 .py 文件（默认跳过 tests/ 与 devtools/）。"""
    for root, dirs, files in os.walk(PLUGIN_DIR):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        rel_root = os.path.relpath(root, PLUGIN_DIR).replace("\\", "/")
        top = rel_root.split("/")[0]
        if not include_tests and top in ("tests", "devtools"):
            continue
        for f in files:
            if f.endswith(".py"):
                path = os.path.join(root, f)
                yield os.path.relpath(path, PLUGIN_DIR).replace("\\", "/"), path


def _read(path: str) -> str:
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _is_loop_bound_call(node: ast.AST) -> str:
    """若 node 是 asyncio.<原语>() 调用，返回原语名，否则返回 ""。"""
    if not isinstance(node, ast.Call):
        return ""
    func = node.func
    # asyncio.Lock()
    if isinstance(func, ast.Attribute) and func.attr in LOOP_BOUND_PRIMITIVES:
        base = func.value
        if isinstance(base, ast.Name) and base.id == "asyncio":
            return func.attr
    return ""


def _is_none_guard(test: ast.AST) -> bool:
    """判断是否为 ``<expr> is None`` 形式的守卫条件。"""
    return (
        isinstance(test, ast.Compare)
        and len(test.ops) == 1
        and isinstance(test.ops[0], ast.Is)
        and len(test.comparators) == 1
        and isinstance(test.comparators[0], ast.Constant)
        and test.comparators[0].value is None
    )


def _collect_sync_context_constructions(tree: ast.AST):
    """找出所有「不在协程内、且无惰性守卫」构造 loop 绑定原语的位置。

    规则不是「禁止在同步函数里构造」，而是「禁止**无守卫**地构造」：

    - ``async def`` 内构造 —— 放行。此时必然有运行中的事件循环。
    - ``if X is None:`` 分支内构造 —— 放行。这正是推荐的惰性单例写法
      （见 ``proactive.ProactiveEngine._get_save_lock``）：getter 本身是
      同步函数，但只在协程内被调用，且构造过程无 await 点，故无竞态。
    - 其余同步上下文（模块级、``__init__``、普通同步函数）—— 违规。

    嵌套的同步函数即使定义在 ``async def`` 内也重置为同步上下文，
    因为它可能被同步调用，保持保守。
    """
    found = []

    def visit(node, in_async: bool, guarded: bool):
        name = _is_loop_bound_call(node)
        if name and not in_async and not guarded:
            found.append((node.lineno, name))

        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.AsyncFunctionDef):
                visit(child, True, False)
            elif isinstance(child, (ast.FunctionDef, ast.Lambda)):
                visit(child, False, False)
            elif isinstance(child, ast.ClassDef):
                visit(child, in_async, False)
            elif isinstance(child, ast.If) and _is_none_guard(child.test):
                for sub in child.body:
                    visit(sub, in_async, True)
                for sub in child.orelse:
                    visit(sub, in_async, guarded)
            else:
                visit(child, in_async, guarded)

    visit(tree, False, False)
    return found


# ============================================================
# 1. asyncio 原语不得在同步上下文构造
# ============================================================

def test_no_loop_bound_asyncio_primitives_in_sync_context():
    """源码中不得在模块级或同步函数里构造事件循环绑定的 asyncio 原语。

    这是真实踩过的坑：``self_evolution.py`` 曾在模块级写
    ``SEMAPHORE = asyncio.Semaphore(2)``，``proactive.py`` 曾在 ``__init__``
    里写 ``self._save_lock = asyncio.Lock()``。两者在 3.13 完全正常，
    在 3.9 上却让 7 个测试炸掉，且真实 3.9 环境下会导致插件加载失败。
    """
    violations = []
    for rel, path in _iter_source_files():
        try:
            tree = ast.parse(_read(path))
        except SyntaxError:  # 交给 test_py39_syntax 报告
            continue
        for lineno, prim in _collect_sync_context_constructions(tree):
            violations.append(f"{rel}:{lineno}: asyncio.{prim}() 在同步上下文构造")

    assert not violations, (
        "以下位置在模块级/同步函数里构造了事件循环绑定的 asyncio 原语，"
        "Python 3.9 会在此抛 RuntimeError：\n  "
        + "\n  ".join(violations)
        + "\n请改为惰性创建（首次在协程内使用时构造），"
          "参见 proactive.ProactiveEngine._get_save_lock。"
    )


def test_detector_itself_is_not_a_no_op():
    """自检：验证检测器能区分坏写法与推荐写法。

    护栏最大的风险是自身逻辑写错、变成永远通过的摆设。这里用内联样本
    同时验证「必须报」与「不许误报」两个方向。
    """
    bad_samples = {
        "模块级构造": "import asyncio\nSEM = asyncio.Semaphore(2)\n",
        "__init__ 中构造": (
            "import asyncio\n"
            "class A:\n"
            "    def __init__(self):\n"
            "        self.lock = asyncio.Lock()\n"
        ),
        "普通同步函数中构造": (
            "import asyncio\n"
            "def make():\n"
            "    return asyncio.Queue()\n"
        ),
        "async 内嵌套同步函数中构造": (
            "import asyncio\n"
            "async def outer():\n"
            "    def inner():\n"
            "        return asyncio.Event()\n"
            "    return inner\n"
        ),
    }
    for label, src in bad_samples.items():
        hits = _collect_sync_context_constructions(ast.parse(src))
        assert hits, f"检测器漏报了坏写法：{label}"

    good_samples = {
        "async def 内构造": (
            "import asyncio\n"
            "async def f():\n"
            "    sem = asyncio.Semaphore(4)\n"
            "    async with sem:\n"
            "        pass\n"
        ),
        "is None 守卫的惰性 getter": (
            "import asyncio\n"
            "_S = None\n"
            "def get():\n"
            "    global _S\n"
            "    if _S is None:\n"
            "        _S = asyncio.Semaphore(2)\n"
            "    return _S\n"
        ),
        "实例属性惰性创建": (
            "import asyncio\n"
            "class A:\n"
            "    def __init__(self):\n"
            "        self._lock = None\n"
            "    def _get_lock(self):\n"
            "        if self._lock is None:\n"
            "            self._lock = asyncio.Lock()\n"
            "        return self._lock\n"
        ),
        "非 asyncio 的同名类": (
            "import threading\n"
            "L = threading.Lock()\n"
        ),
    }
    for label, src in good_samples.items():
        hits = _collect_sync_context_constructions(ast.parse(src))
        assert not hits, f"检测器误报了推荐写法：{label} -> {hits}"


def test_lazy_primitives_actually_work_without_running_loop():
    """行为验证：无当前事件循环时，相关对象仍能被构造。

    直接复刻 3.9 的失败场景——在 ``asyncio.run()`` 之后（此时当前 loop 已被
    置空）构造对象。若哪天有人把惰性创建改回构造期创建，本用例会立刻失败。
    """
    import asyncio

    from proactive import ProactiveEngine

    async def _noop():
        return None

    asyncio.run(_noop())  # 结束后当前 loop 被置为 None

    # 关键断言：以下构造/导入在 3.9 上均不得抛 RuntimeError
    engine = ProactiveEngine(plugin=None)
    assert engine._save_lock is None, "锁应保持惰性，未在 __init__ 中创建"

    import self_evolution
    assert self_evolution._SEMAPHORE is None, "闸门应保持惰性，未在导入期创建"

    # 在协程内首次使用时才真正创建，且同一对象复用
    async def _use():
        lock = engine._get_save_lock()
        assert lock is engine._get_save_lock(), "重复获取应返回同一把锁"
        sem = self_evolution._get_semaphore()
        assert sem is self_evolution._get_semaphore(), "重复获取应返回同一闸门"
        async with lock:
            pass
        async with sem:
            pass
        return True

    assert asyncio.run(_use())


# ============================================================
# 2. PEP 604 联合类型注解（导入期求值）
# ============================================================

def test_no_runtime_pep604_annotations():
    """无 ``__future__.annotations`` 时，函数签名不得使用 ``X | Y``。

    这类注解在 3.9 会于导入期抛 TypeError，纯语法检查抓不到。
    """
    violations = []
    for rel, path in _iter_source_files(include_tests=True):
        src = _read(path)
        try:
            tree = ast.parse(src)
        except SyntaxError:
            continue
        has_future = any(
            isinstance(n, ast.ImportFrom)
            and n.module == "__future__"
            and any(a.name == "annotations" for a in n.names)
            for n in tree.body
        )
        if has_future:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            args = node.args
            annotations = [
                a.annotation
                for a in list(args.args) + list(args.kwonlyargs) + list(args.posonlyargs)
                if a.annotation
            ]
            if node.returns:
                annotations.append(node.returns)
            for ann in annotations:
                if any(
                    isinstance(sub, ast.BinOp) and isinstance(sub.op, ast.BitOr)
                    for sub in ast.walk(ann)
                ):
                    violations.append(f"{rel}:{node.lineno}: {node.name}()")
                    break

    assert not violations, (
        "以下签名使用了 PEP 604 联合类型（``X | Y``）但所在模块没有 "
        "``from __future__ import annotations``，Python 3.9 导入期会抛 "
        "TypeError：\n  " + "\n  ".join(sorted(set(violations)))
    )


# ============================================================
# 3. 语法下限
# ============================================================

@pytest.mark.skipif(
    sys.version_info < (3, 8),
    reason="ast.parse 的 feature_version 需要 3.8+",
)
def test_all_sources_parse_under_py39_syntax():
    """所有源码（含 tests/devtools）必须能以 3.9 语法解析。"""
    violations = []
    for rel, path in _iter_source_files(include_tests=True):
        try:
            ast.parse(_read(path), feature_version=(3, 9))
        except SyntaxError as e:
            violations.append(f"{rel}:{e.lineno}: {e.msg}")

    assert not violations, (
        "以下文件使用了 Python 3.9 不支持的语法：\n  " + "\n  ".join(violations)
    )
