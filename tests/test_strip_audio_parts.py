"""audio 分片剥离（历史语音残党净化）测试。

背景：STT 转写已把语音内容转成 text（[语音转写] …），但历史语音消息
被 AstrBot 序列化后可能以 audio 分片进入 LLM 请求体；非全模态主模型
（如 SiliconFlow Qwen3-VL，网关只放行 text+image_url）会 400 并每轮落
fallback。此处剥离 audio 分片纯属去冗余，绝不丢文字语义。
"""
from util import strip_audio_parts_from_messages


def test_strips_audio_parts_keeps_text():
    msgs = [{"role": "user", "content": [
        {"type": "text", "text": "[语音转写] 唱首歌来听听。"},
        {"type": "audio", "audio": "base64..."},
        {"type": "text", "text": "[Audio]"},
    ]}]
    strip_audio_parts_from_messages(msgs)
    content = msgs[0]["content"]
    # audio 分片被剔除，text 分片全保留
    assert len(content) == 2
    assert all(p["type"] == "text" for p in content)
    assert content[0]["text"] == "[语音转写] 唱首歌来听听。"
    assert content[1]["text"] == "[Audio]"


def test_strips_input_audio_variants():
    msgs = [{"role": "user", "content": [
        {"type": "text", "text": "你好"},
        {"type": "input_audio", "input_audio": {"data": "xx", "format": "wav"}},
        {"type": "audio_url", "audio_url": {"url": "http://x.wav"}},
    ]}]
    strip_audio_parts_from_messages(msgs)
    content = msgs[0]["content"]
    assert len(content) == 1
    assert content[0]["type"] == "text"


def test_all_audio_falls_back_to_message_text():
    msgs = [{"role": "user", "content": [
        {"type": "audio", "audio": "base64..."},
    ], "text": "[语音转写] 原始语音内容"}]
    strip_audio_parts_from_messages(msgs)
    # 全被剥离 → 用消息原文兜底（字符串），不留下空列表
    assert msgs[0]["content"] == "[语音转写] 原始语音内容"


def test_non_list_and_string_content_untouched():
    # 非 list messages 原样返回
    assert strip_audio_parts_from_messages(None) is None
    # 字符串 content 原样保留
    msgs = [{"role": "user", "content": "纯文本消息"}]
    strip_audio_parts_from_messages(msgs)
    assert msgs[0]["content"] == "纯文本消息"
