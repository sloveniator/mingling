"""回归测试：proactive 主动消息必须用 MessageChain 包裹，绝不能传裸 str。

生产环境曾因 `from astrbot.api.event import MessageChain, Plain` 整体导入，
而 AstrBot v4.27.3 的 Plain 已挪到 astrbot.api.message_components，
导致整条 import 抛 ImportError、MessageChain/Plain 双双为 None，
触发 `chain = text`（裸 str）传给 context.send_message，
webchat 适配器遍历 `.chain` 抛 AttributeError: 'str' object has no attribute 'chain'。

修复：MessageChain 与 Plain 分开导入（各自 try/except），且构造出的对象必须有 .chain。
"""
import proactive


def test_proactive_imports_resolve():
    # 两者都必须非 None（否则会回退成裸 str 传给 send_message）
    assert proactive.MessageChain is not None, "MessageChain 导入失败，主动消息会退化成 str"
    assert proactive.Plain is not None, "Plain 导入失败，主动消息会退化成 str"


def test_proactive_builds_chain_with_chain_attr():
    # 复刻 proactive.py:211 / :359 的构造，确认产物带 .chain（webchat 适配器需要）
    text = "想你了，冒个泡。"
    chain = (
        proactive.MessageChain([proactive.Plain(text=text)])
        if proactive.MessageChain and proactive.Plain
        else text
    )
    assert hasattr(chain, "chain"), (
        "主动消息未用 MessageChain 包裹（会触发 "
        "'str' object has no attribute 'chain'）"
    )
