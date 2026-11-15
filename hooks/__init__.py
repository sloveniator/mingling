"""
hooks/ -- LLM 注入钩子子包
==========================

此前 6 个钩子类与 HookChain 全部挤在 core.py 尾部（约 170 行），
使这个本已承担插件生命周期、事件分发、人格构建等职责的"上帝类"文件
进一步膨胀。拆分后：

- 每个钩子一个文件，职责边界一目了然，改一个钩子不必翻阅千行 core.py；
- 钩子仅通过传入的 ``core`` 参数访问运行时依赖（鸭子类型），
  不 import core，因此不产生循环依赖；
- core.py 继续 re-export 全部符号，``from core import CrisisHook, HookChain``
  等既有写法（含现有测试）保持可用。

新增钩子的步骤：建一个 ``hooks/xxx.py`` 继承 ``HookProcessor``，
在本文件导入并加入 ``DEFAULT_HOOK_CLASSES``（顺序即优先级）。
"""
from .base import HookChain, HookProcessor
from .blessing import BlessingHook
from .character import CharacterHook
from .counseling import CounselingHook
from .crisis import CrisisHook
from .persona import PersonaHook

# 顺序即执行优先级：危机支持最高，祝福最低。
DEFAULT_HOOK_CLASSES = (
    CrisisHook,
    PersonaHook,
    CharacterHook,
    CounselingHook,
    BlessingHook,
)

_DEFAULT_CHAIN: "HookChain | None" = None


def build_default_chain() -> HookChain:
    """按默认优先级组装一条全新的钩子链。"""
    chain = HookChain()
    for cls in DEFAULT_HOOK_CLASSES:
        chain.register(cls())
    return chain


def get_default_chain() -> HookChain:
    """返回进程内共享的默认钩子链。

    所有内置钩子都是无状态的（只读取调用时传入的 core/event/req，
    不持有实例字段），因此可以安全共享。此前每次 LLM 请求都要新建
    1 条链 + 5 个钩子对象，属于热路径上的无谓分配。
    """
    global _DEFAULT_CHAIN
    if _DEFAULT_CHAIN is None:
        _DEFAULT_CHAIN = build_default_chain()
    return _DEFAULT_CHAIN


__all__ = [
    "HookProcessor",
    "HookChain",
    "CrisisHook",
    "PersonaHook",
    "CharacterHook",
    "CounselingHook",
    "BlessingHook",
    "DEFAULT_HOOK_CLASSES",
    "build_default_chain",
    "get_default_chain",
]
