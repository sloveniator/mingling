"""
tests/test_devtools_isolation.py -- 开发工具与运行时的隔离护栏
==============================================================
部署脚本用 ``git clone`` 整仓拉取，因此 ``devtools/``、``tests/`` 会一并落到
生产的插件目录里。它们本身不被运行时导入，但这个"不被导入"必须是**可验证的
不变量**，而不是靠约定。

风险有多具体：``devtools/astrbot_stub.py`` 会往 ``sys.modules`` 里塞一整套
假的 ``astrbot.*`` 模块，其中 logger 的所有方法都是 ``pass``。一旦它在生产
环境被导入，插件会静默跑在空壳桩上——日志全丢、行为异常且极难定位。

本文件从两个方向设卡：
  1. **静态传递闭包**：从 ``main.py`` 出发解析 import 图，证明整个运行时闭包
     不含任何开发目录。这比逐文件 grep 强，跨层间接依赖也逃不掉。
  2. **运行时硬护栏**：``install_astrbot_stub()`` 在检测到真实 AstrBot 已加载时
     必须抛异常，而不是默默把真模块顶掉。

注意本文件**不主张**在部署时物理删掉这些文件：部署脚本靠 ``git merge --ff-only``
做升级，删除已跟踪文件会让工作区变脏、后续升级直接失败。隔离靠依赖方向保证，
而非靠删文件。
"""
import ast
import os
import sys

import pytest

PLUGIN_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 仅供开发/测试使用，绝不可出现在运行时 import 闭包里。
DEV_ONLY_TOP_LEVEL = {"devtools", "tests", "prototype"}

# 其中「必须存在」的目录。prototype/ 已在 .gitignore（第 29 行）且从未入库，
# 属可选开发目录——全新克隆必然没有，不能强制要求存在，否则该护栏在干净
# 检出上恒失败。devtools/ 与 tests/ 是版本控制内的真实目录，必须存在。
REQUIRED_DEV_DIRS = {"devtools", "tests"}

# 运行时入口。AstrBot 只加载 main.py，其余全是它的传递依赖。
RUNTIME_ENTRY = "main"

# 运行时代码不得依赖的测试期库。
FORBIDDEN_RUNTIME_IMPORTS = {"pytest", "unittest", "_pytest"}


def _module_file(dotted):
    """把点分模块名解析为磁盘文件；不存在则返回 None（外部依赖）。"""
    parts = dotted.split(".")
    as_module = os.path.join(PLUGIN_DIR, *parts) + ".py"
    if os.path.isfile(as_module):
        return as_module
    as_package = os.path.join(PLUGIN_DIR, *parts, "__init__.py")
    if os.path.isfile(as_package):
        return as_package
    return None


def _iter_imports(path, dotted):
    """产出该文件里所有 import 的目标模块名（已把相对导入解析为绝对点分名）。

    ``dotted`` 是该文件自身的点分名，用来确定相对导入的基准包。
    """
    with open(path, encoding="utf-8") as fh:
        tree = ast.parse(fh.read(), filename=path)

    # 自身所在包：模块 a.b.c 的包是 a.b；包的 __init__ 自身就是包。
    own_parts = dotted.split(".")
    pkg_parts = own_parts if path.endswith("__init__.py") else own_parts[:-1]

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                # level=1 → 当前包；level=2 → 上一层，依此类推。
                base = pkg_parts[: len(pkg_parts) - (node.level - 1)]
                target = base + (node.module.split(".") if node.module else [])
                yield ".".join(target)
                # `from .pkg import mod` 里 mod 也可能是子模块。
                for alias in node.names:
                    yield ".".join(target + [alias.name])
            else:
                yield node.module or ""
                for alias in node.names:
                    yield f"{node.module}.{alias.name}" if node.module else alias.name


def _runtime_closure():
    """从 main.py 出发的运行时 import 传递闭包。

    返回 ``(已解析模块集合, 边集合)``；边用于失败时打印依赖链。
    """
    entry_file = _module_file(RUNTIME_ENTRY)
    assert entry_file, f"找不到运行时入口 {RUNTIME_ENTRY}.py"

    seen = {RUNTIME_ENTRY}
    edges = []
    stack = [(RUNTIME_ENTRY, entry_file)]
    while stack:
        dotted, path = stack.pop()
        for target in _iter_imports(path, dotted):
            if not target:
                continue
            edges.append((dotted, target))
            if target in seen:
                continue
            target_file = _module_file(target)
            if target_file is None:
                # 外部依赖（aiohttp/astrbot/标准库）——不进闭包，但边已记录。
                seen.add(target)
                continue
            seen.add(target)
            stack.append((target, target_file))
    return seen, edges


def _runtime_py_files():
    """所有运行时 Python 文件（排除开发目录与虚拟环境）。"""
    skip_dirs = DEV_ONLY_TOP_LEVEL | {".venv", ".git", "__pycache__",
                                      ".pytest_cache", ".ruff_cache", "pages"}
    out = []
    for root, dirs, files in os.walk(PLUGIN_DIR):
        dirs[:] = [d for d in dirs if d not in skip_dirs]
        for name in files:
            if not name.endswith(".py"):
                continue
            full = os.path.join(root, name)
            rel = os.path.relpath(full, PLUGIN_DIR)
            # verify_hooks.py 是容器内诊断脚本，按开发工具对待。
            if rel == "verify_hooks.py":
                continue
            out.append(full)
    return out


class TestRuntimeClosureExcludesDevtools:
    """从 main.py 出发的传递闭包不得触碰任何开发目录。"""

    @pytest.fixture(scope="class")
    @classmethod
    def closure(cls):
        return _runtime_closure()

    def test_closure_is_non_trivial(self, closure):
        """闭包必须真的展开了——否则下面的断言是空转。"""
        seen, edges = closure
        local = [m for m in seen if _module_file(m)]
        assert len(local) >= 20, (
            f"运行时闭包只解析出 {len(local)} 个本地模块（边 {len(edges)} 条），"
            f"疑似 import 解析逻辑失效，本文件的隔离断言会变成空转"
        )

    def test_no_devtools_in_runtime_closure(self, closure):
        seen, edges = closure
        offenders = [m for m in seen if m.split(".")[0] in DEV_ONLY_TOP_LEVEL]
        if offenders:
            chains = [f"{src} → {dst}" for src, dst in edges
                      if dst.split(".")[0] in DEV_ONLY_TOP_LEVEL]
            pytest.fail(
                f"运行时 import 闭包里出现开发目录模块：{sorted(offenders)}\n"
                f"依赖链：\n  " + "\n  ".join(sorted(set(chains))) + "\n"
                "devtools/tests 只能被测试导入，运行时严禁依赖"
            )

    def test_no_test_libs_in_runtime_closure(self, closure):
        seen, _ = closure
        offenders = [m for m in seen
                     if m.split(".")[0] in FORBIDDEN_RUNTIME_IMPORTS]
        assert not offenders, (
            f"运行时闭包依赖了测试期库：{sorted(offenders)}。"
            f"生产环境不保证安装这些包，会导致插件加载失败"
        )


class TestRuntimeSourcesAreClean:
    """逐文件复核：运行时源码里不得出现开发目录的 import。

    与闭包检查互补——闭包只覆盖"从 main 可达"的部分，这里覆盖全部运行时文件，
    包括暂时没被 main 引用到的。
    """

    def test_no_runtime_file_imports_devtools(self):
        offenders = []
        for path in _runtime_py_files():
            rel = os.path.relpath(path, PLUGIN_DIR)
            dotted = rel[:-3].replace(os.sep, ".")
            for target in _iter_imports(path, dotted):
                top = target.split(".")[0] if target else ""
                if top in DEV_ONLY_TOP_LEVEL or top in FORBIDDEN_RUNTIME_IMPORTS:
                    offenders.append(f"{rel}: import {target}")
        assert not offenders, (
            "运行时源码引用了开发目录/测试库：\n  " + "\n  ".join(offenders)
        )

    def test_dev_only_dirs_still_exist(self):
        """清单不能腐坏：版本控制内的开发目录被改名后本护栏会静默失效。

        只校验 REQUIRED_DEV_DIRS（gitignore 内的可选目录如 prototype/ 不强制，
        否则干净检出于恒失败——它本就不入库）。
        """
        missing = [d for d in REQUIRED_DEV_DIRS
                   if not os.path.isdir(os.path.join(PLUGIN_DIR, d))]
        assert not missing, (
            f"REQUIRED_DEV_DIRS 里的目录不存在：{missing}。"
            f"若已改名/删除，请同步更新本文件的清单"
        )


class TestDistributionExcludesDevFiles:
    """``.gitattributes`` 的 export-ignore 清单不能腐坏。

    ZIP 安装（AstrBot 插件市场的常见方式）走 ``git archive``，靠 export-ignore
    把开发内容挡在分发包外。新增开发目录却忘了登记，这层隔离会静默失效。
    """

    @staticmethod
    def _check_attr(path):
        """返回 git 认为该路径的 export-ignore 取值。"""
        import subprocess
        out = subprocess.run(
            ["git", "check-attr", "export-ignore", "--", path],
            cwd=PLUGIN_DIR, capture_output=True, text=True,
        )
        if out.returncode != 0:
            pytest.skip(f"git 不可用或不在仓库内：{out.stderr.strip()}")
        # 输出形如 "devtools/: export-ignore: set"
        return out.stdout.strip().rsplit(": ", 1)[-1]

    @pytest.mark.parametrize("path", sorted(DEV_ONLY_TOP_LEVEL) + ["verify_hooks.py"])
    def test_dev_paths_are_export_ignored(self, path):
        assert self._check_attr(path) == "set", (
            f"{path} 未标记 export-ignore，会被打进 ZIP 分发包。"
            f"请在 .gitattributes 里补一行 `{path}  export-ignore`"
        )

    @pytest.mark.parametrize("path", [
        "oracle_fixtures.json", "oracle_fixtures_large.json",
    ])
    def test_test_fixtures_are_export_ignored(self, path):
        """4.5MB 测试数据只被 tests/ 使用，不应随插件分发。"""
        assert self._check_attr(path) == "set", (
            f"{path} 未标记 export-ignore，白占分发包体积"
        )

    @pytest.mark.parametrize("path", [
        "main.py", "core.py", "metadata.yaml", "_conf_schema.json",
        "requirements.txt", "README.md",
    ])
    def test_runtime_files_are_not_export_ignored(self, path):
        """反向断言：运行时必需文件绝不能被误排除，否则插件直接装不起来。"""
        assert self._check_attr(path) != "set", (
            f"{path} 被标记了 export-ignore——它是运行时必需文件，"
            f"排除后 ZIP 安装的插件将无法加载"
        )


class TestStubRefusesRealRuntime:
    """桩安装器必须拒绝在真实 AstrBot 进程里运行。"""

    def test_stub_raises_when_real_astrbot_loaded(self):
        """真模块有 __file__，桩没有——据此识别并硬失败。"""
        from devtools.astrbot_stub import install_astrbot_stub

        astrbot = sys.modules.get("astrbot")
        assert astrbot is not None, "测试环境应已装好 astrbot 桩"
        had_file = hasattr(astrbot, "__file__")
        try:
            # 伪装成"真实 AstrBot 已加载"
            astrbot.__file__ = "/AstrBot/astrbot/__init__.py"
            with pytest.raises(RuntimeError, match="拒绝安装"):
                install_astrbot_stub()
        finally:
            if not had_file:
                del astrbot.__file__

    def test_stub_is_idempotent_in_test_env(self):
        """测试环境下重复调用必须无副作用（护栏不能误伤正常路径）。"""
        from devtools.astrbot_stub import install_astrbot_stub

        before = sys.modules["astrbot"]
        install_astrbot_stub()
        install_astrbot_stub()
        assert sys.modules["astrbot"] is before, "重复安装替换了已有桩模块"

    def test_stub_logger_is_a_noop_which_is_why_guard_matters(self):
        """把"桩 logger 是空实现"这一事实固化下来。

        它正是护栏存在的理由：顶掉真 logger 不会报错，只会静默丢日志。
        """
        from astrbot.api import logger

        # 桩 logger 的方法返回 None 且不抛异常——生产环境绝不能是这个。
        assert logger.info("probe") is None
        assert logger.warning("probe") is None
        assert logger.error("probe") is None
