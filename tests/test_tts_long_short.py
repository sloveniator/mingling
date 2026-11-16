"""TTS 长/短文本分流 + 「没听清」转文字 + 入站 msgid 去重 的回归测试。

需求（2026-08-18）：
- 短文本(<阈值) → 给文字，不触发语音，且不压住文本。
- 长文本(>=阈值) → 转语音；语音成功发出时压住核心文本回复（避免既文本又语音）；
  语音全失败时回退为文字（不静默丢失）。
- 用户表达「没听清」时，重发上一条回复的文字版（短路 LLM）。
- 微信客服 webhook 重投的同一 msgid：在 LLM 调用前（request 钩子）短路，只处理一次；
  响应钩子不压空（v4.8.87 移除兜底，避免 follow-up 合并场景误杀正常回复）。
"""
import asyncio
from unittest.mock import AsyncMock, patch

from main import LLMResponse, MessageChain, MingbuPlugin, Plain
from tests.test_admin_api import FakeContext


class KVFakeContext(FakeContext):
    """带内存 KV 的上下文（满足 StarAdmin 初始化所需接口）。"""


class KVPlugin(MingbuPlugin):
    """自带内存 KV 的插件子类，供测试直接读写 get_kv_data/put_kv_data。"""

    def __init__(self, cfg):
        self._store = {}
        super().__init__(KVFakeContext(), cfg)

    async def get_kv_data(self, key, default=None):
        return self._store.get(key, default)

    async def put_kv_data(self, key, value):
        self._store[key] = value


class FakeEvent:
    def __init__(self, uid="u1", msg_id="m1", msg_type="FriendMessage", text=""):
        self._uid = uid
        self._msg_id = msg_id
        self._msg_type = msg_type
        self._text = text
        self.message_obj = type("MO", (), {})()
        self.message_obj.message_id = msg_id
        self.message_obj.type = msg_type
        self.message_obj.group_id = ""
        self.message_str = text
        self.stopped = False
        self.sent = None

    def get_sender_id(self):
        return self._uid

    def stop_event(self):
        self.stopped = True

    async def send(self, message):
        self.sent = message


class FakeResp(LLMResponse):
    """模拟 AstrBot 的 LLMResponse（_tts_on_response 按 isinstance 提取 completion_text）。"""

    def __init__(self, text):
        self.completion_text = text
        self.response_text = text


def _make_plugin(tts_enabled=True, threshold=500, with_direct=False):
    cfg = {
        "background_watch_enabled": False,
        "tts_enabled": tts_enabled,
        "tts_long_text_threshold": threshold,
    }
    if with_direct:
        cfg.update({
            "tts_api_key": "sk-test",
            "tts_api_base": "https://api.example.com/v1",
            "tts_model": "FunAudioLLM/CosyVoice2-0.5B",
        })
    return KVPlugin(cfg)


def test_short_reply_no_voice_and_text_kept():
    """短文本：不触发语音，核心文本不被压住，且记录为上一条回复。"""
    plugin = _make_plugin()
    sent_tts = AsyncMock(return_value=False)
    with patch.object(plugin, "_send_tts", sent_tts):
        resp = FakeResp("这是一条很短的回复。")
        ev = FakeEvent(msg_id="s1")
        asyncio.run(plugin._on_llm_response(ev, resp))
    assert sent_tts.call_count == 0, "短文本不应触发 _send_tts"
    assert resp.completion_text == "这是一条很短的回复。", "短文本的核心文本不应被压住"
    assert plugin._store.get("mingbu.last_bot_text:u1") == "这是一条很短的回复。"


def test_long_reply_suppresses_text_when_voice_sent():
    """长文本且语音成功发出：压住核心文本回复（避免既文本又语音）。"""
    plugin = _make_plugin(with_direct=True)
    sent_tts = AsyncMock(return_value=True)
    with patch.object(plugin, "_send_tts", sent_tts):
        resp = FakeResp("命" * 600)
        ev = FakeEvent(msg_id="l1")
        asyncio.run(plugin._on_llm_response(ev, resp))
    assert sent_tts.call_count == 1, "长文本应触发一次 _send_tts"
    assert resp.completion_text == "", "长文本转语音后核心文本应被压住"


def test_long_reply_keeps_text_when_voice_fails():
    """长文本但语音全部失败：回退为文字，不静默丢失。"""
    plugin = _make_plugin(with_direct=True)
    sent_tts = AsyncMock(return_value=False)
    with patch.object(plugin, "_send_tts", sent_tts):
        resp = FakeResp("命" * 600)
        ev = FakeEvent(msg_id="l2")
        asyncio.run(plugin._on_llm_response(ev, resp))
    assert sent_tts.call_count == 1
    assert resp.completion_text == "命" * 600, "语音失败应回退为文字"


# ---- 语义驱动触发（v4.8.99）：强情绪回复即使短也读出来 ----

def test_emotional_short_reply_triggers_voice():
    """带明显情绪的短回复（安慰/开心）：语义触发语音，核心文本被压住。"""
    plugin = _make_plugin(with_direct=True)  # 阈值 500，但情绪语义不依赖长度
    sent_tts = AsyncMock(return_value=True)
    with patch.object(plugin, "_send_tts", sent_tts):
        resp = FakeResp("别难过，我一直都在你身边呀。")
        ev = FakeEvent(msg_id="e1")
        asyncio.run(plugin._on_llm_response(ev, resp))
    assert sent_tts.call_count == 1, "强情绪短回复应触发 _send_tts"
    assert resp.completion_text == "", "情绪语义语音发出后核心文本应被压住"


def test_neutral_short_reply_no_voice():
    """中性短回复（无情绪词）：不触发语音，文本保留。"""
    plugin = _make_plugin()
    sent_tts = AsyncMock(return_value=False)
    with patch.object(plugin, "_send_tts", sent_tts):
        resp = FakeResp("你的八字排好了，稍等。")
        ev = FakeEvent(msg_id="n1")
        asyncio.run(plugin._on_llm_response(ev, resp))
    assert sent_tts.call_count == 0, "中性短回复不应触发语音"
    assert resp.completion_text == "你的八字排好了，稍等。"


def test_tts_should_trigger_semantic_path():
    """tts_should_trigger 纯函数：情绪语义路径独立于长度阈值。"""
    from main import tts_should_trigger
    # 短文本 + 强情绪 → 触发（原因含"情绪语义"）
    should, reason = tts_should_trigger("好开心呀！今天终于搞定了！", threshold=500)
    assert should is True
    assert "情绪语义" in reason
    # 长文本仍走长文本路径
    should, reason = tts_should_trigger("命" * 600, threshold=500)
    assert should is True
    assert reason == "长文本"
    # 中性短文本 → 不触发
    should, _ = tts_should_trigger("八字排好了，稍等。", threshold=500)
    assert should is False


def test_meitingqing_transcribe_resends_text():
    """用户说没听清：重发上一条回复文字，且不调用 LLM。"""
    plugin = _make_plugin()
    asyncio.run(plugin.put_kv_data("mingbu.last_bot_text:u1", "上一条很长的语音回复文字。"))
    body = AsyncMock()
    with patch.object(plugin, "_on_llm_request_body", body):
        ev = FakeEvent(msg_id="t1", text="我没听清，再说一遍")
        asyncio.run(plugin._on_llm_request(ev, object()))
    assert body.call_count == 0, "没听清应短路，不调用 LLM"
    assert ev.stopped is True
    assert ev.sent is not None
    assert isinstance(ev.sent, MessageChain)
    assert getattr(ev.sent.chain[0], "type", None) == "Plain"
    assert "上一条很长的语音回复文字。" in ev.sent.chain[0].text


def test_meitingqing_no_stored_text_passes_through():
    """没听清但无历史回复：放行给 LLM 正常处理。"""
    plugin = _make_plugin()
    body = AsyncMock()
    with patch.object(plugin, "_on_llm_request_body", body):
        ev = FakeEvent(msg_id="t2", text="我没听清")
        asyncio.run(plugin._on_llm_request(ev, object()))
    assert body.call_count == 1, "无历史回复时应走正常 LLM 流程"
    assert ev.stopped is False


def test_msgid_dedup_drops_duplicate():
    """同一 msgid 的重投在 LLM 调用前被短路（省配额 + 不重复回复）。"""
    plugin = _make_plugin(with_direct=True)
    plugin._seen_msgids = {}
    body = AsyncMock()
    with patch.object(plugin, "_on_llm_request_body", body):
        # 第一次进入：正常放行，标记 msgid，LLM 被调用
        ev1 = FakeEvent(msg_id="dup1")
        asyncio.run(plugin._on_llm_request(ev1, object()))
        assert not ev1.stopped, "首次处理不应短路"
        assert body.call_count == 1
        # 第二次重投：LLM 调用前 stop_event 短路，不再调用 body（省配额）
        ev2 = FakeEvent(msg_id="dup1")
        asyncio.run(plugin._on_llm_request(ev2, object()))
        assert ev2.stopped, "重投应在 LLM 调用前 stop_event"
        assert body.call_count == 1, "重投不应再触发 LLM 调用"
    # response 钩子：同 msgid 的正常响应不应被压空（v4.8.87 移除兜底——
    # follow-up 合并场景下唯一且正常的回复会被误杀，见 main.py 注释）
    resp = FakeResp("你好呀" * 20)  # 短文本，不触发 TTS
    ev3 = FakeEvent(msg_id="dup1")
    asyncio.run(plugin._on_llm_response(ev3, resp))
    assert resp.completion_text == "你好呀" * 20, "同 msgid 的正常响应不应被压空"


def test_dedup_full_flow_three_deliveries_one_reply():
    """端到端闭环：微信重投 3 次 → 只调 1 次 LLM、只发 1 条回复（不压空）。

    模拟生产日志场景（05:47 / 07:33）：同一条语音被微信重投 3 次。
    request 钩子对第 2/3 次投递短路（省配额），第 1 次投递启动的 run 的
    正常响应在 response 钩子中完整放行（记录对话 + 文本保留，TTS 按规则触发）。
    """
    plugin = _make_plugin(with_direct=True)
    plugin._seen_msgids = {}
    body = AsyncMock()
    with patch.object(plugin, "_on_llm_request_body", body):
        # 投递 1：正常启动 run，标记 msgid
        ev1 = FakeEvent(msg_id="flow1")
        asyncio.run(plugin._on_llm_request(ev1, object()))
        assert not ev1.stopped, "首次投递不应短路"
        assert body.call_count == 1
        # 投递 2 / 3：重投，request 前短路，不调 LLM
        for _ in range(2):
            evn = FakeEvent(msg_id="flow1")
            asyncio.run(plugin._on_llm_request(evn, object()))
            assert evn.stopped, "重投应在 LLM 调用前短路"
        assert body.call_count == 1, "3 次投递只应调 1 次 LLM"
    # 第 1 次投递的 run 产生唯一响应：完整放行（不被压空、不被丢弃）
    resp = FakeResp("就到这里吧。" * 10)  # 短文本，不触发 TTS
    ev_out = FakeEvent(msg_id="flow1")
    asyncio.run(plugin._on_llm_response(ev_out, resp))
    assert resp.completion_text == "就到这里吧。" * 10, "唯一响应不应被压空"
    assert ev_out.sent is None, "正常路径不应额外 send 任何内容"


# ---- 开关语义（v4.8.100）：tts_auto_for_long_text 只控长文本路径 ----

def test_long_text_suppressed_when_auto_long_text_off():
    """关闭「长文本自动语音」：长文本不再触发语音（仅文字）。"""
    cfg = {
        "background_watch_enabled": False,
        "tts_enabled": True,
        "tts_long_text_threshold": 500,
        "tts_auto_for_long_text": False,  # 用户只关长文本路径
        "tts_api_key": "sk-test",
        "tts_api_base": "https://api.example.com/v1",
        "tts_model": "FunAudioLLM/CosyVoice2-0.5B",
    }
    plugin = KVPlugin(cfg)
    sent_tts = AsyncMock(return_value=True)
    with patch.object(plugin, "_send_tts", sent_tts):
        resp = FakeResp("命" * 600)  # 长文本
        ev = FakeEvent(msg_id="lg_off1")
        asyncio.run(plugin._on_llm_response(ev, resp))
    assert sent_tts.call_count == 0, "长文本自动语音关闭后，长文本不应触发 _send_tts"
    assert resp.completion_text == "命" * 600, "长文本关闭后核心文本应保留"


def test_emotional_short_reply_still_triggers_when_auto_long_text_off():
    """关闭「长文本自动语音」：情绪语义短回复仍触发语音（情绪路由不受影响）。"""
    cfg = {
        "background_watch_enabled": False,
        "tts_enabled": True,
        "tts_long_text_threshold": 500,
        "tts_auto_for_long_text": False,  # 只关长文本
        "tts_emotion_routing_enabled": True,
        "tts_api_key": "sk-test",
        "tts_api_base": "https://api.example.com/v1",
        "tts_model": "FunAudioLLM/CosyVoice2-0.5B",
    }
    plugin = KVPlugin(cfg)
    sent_tts = AsyncMock(return_value=True)
    with patch.object(plugin, "_send_tts", sent_tts):
        resp = FakeResp("别难过，我一直都在你身边呀。")  # 情绪短回复
        ev = FakeEvent(msg_id="emo_off1")
        asyncio.run(plugin._on_llm_response(ev, resp))
    assert sent_tts.call_count == 1, "关闭长文本开关后，情绪语义短回复仍应触发 _send_tts"


# ---- v4.8.106→v4.8.141：微信客服 45007 在 Provider 路径的行为 ----
# v4.8.106 时代 `_send_voice_chunk_retry_split` 会对 45007 段做文本二分重切。
# v4.8.141 起 TTS 合成完全交由 AstrBot provider，Provider 返回的 audio 是已
# 合成文件、无法按文本二分，故 45007 行为改为「记失败、不误计成功」，
# 源头预防靠 load_tts_settings 把单段钳到 max_chunk_chars（≤微信 60s 上限）。

class _FakeEventSend:
    """模拟 event.send：第一次抛 45007（超时长），之后成功。"""

    def __init__(self, fail_first=1):
        self.send_calls = []
        self.fail_first = fail_first

    async def send(self, chain):
        self.send_calls.append(chain)
        if len(self.send_calls) <= self.fail_first:
            raise Exception("Error code: 45007, message: playtime out of limit")
        return None


def test_send_tts_45007_mid_segment_partial_success():
    """Provider 路径：一段 45007、一段成功 → 返回 True（部分成功不误报失败）。"""
    import asyncio
    from unittest.mock import patch

    from astrbot_plugin_mingbu import provider_router
    from astrbot_plugin_mingbu.main import MingbuPlugin, _send_voice_with_backoff

    from tests.test_admin_api import FakeContext

    plugin = MingbuPlugin(FakeContext(), {
        "background_watch_enabled": False, "provider_tts": "test-provider",
        "tts_voice": "",  # 不透传 voice，让 provider 单参 get_audio
    })
    send_calls = []

    class _P:
        async def get_audio(self, text, voice=None):
            return b"RIFFaudio"

    async def fake_get(ctx, pid=""):
        return _P()

    async def fake_backoff(event, file_value, **kw):
        send_calls.append(file_value)
        if len(send_calls) == 1:
            return "too_long"  # 第一段超微信 60s
        return True  # 第二段成功

    with patch.object(provider_router, "get_tts_provider", fake_get), \
         patch("astrbot_plugin_mingbu.main._send_voice_with_backoff", fake_backoff), \
         patch("astrbot_plugin_mingbu.main.TTS_SEGMENT_GAP", 0):
        ev = _FakeEventSend()
        ok = asyncio.run(plugin._send_tts(
            ev, "这是一段足够长会被切分成多段的测试文本，用来验证 45007 部分成功语义。" * 6))

    assert ok is True, "有一段成功即应返回 True（部分成功）"
    assert len(send_calls) >= 2, "45007 段后应继续尝试发送后续段"


def test_send_tts_45007_all_segments_not_sent():
    """Provider 路径：全部段都 45007 → 返回 False（不误计成功）。"""
    import asyncio
    from unittest.mock import patch

    from astrbot_plugin_mingbu import provider_router
    from astrbot_plugin_mingbu.main import MingbuPlugin, _send_voice_with_backoff

    from tests.test_admin_api import FakeContext

    plugin = MingbuPlugin(FakeContext(), {
        "background_watch_enabled": False, "provider_tts": "test-provider",
        "tts_voice": "",
    })
    send_calls = []

    class _P:
        async def get_audio(self, text, voice=None):
            return b"RIFFaudio"

    async def fake_get(ctx, pid=""):
        return _P()

    async def fake_backoff(event, file_value, **kw):
        send_calls.append(file_value)
        return "too_long"  # 永远 45007

    with patch.object(provider_router, "get_tts_provider", fake_get), \
         patch("astrbot_plugin_mingbu.main._send_voice_with_backoff", fake_backoff), \
         patch("astrbot_plugin_mingbu.main.TTS_SEGMENT_GAP", 0):
        ev = _FakeEventSend(fail_first=999)
        ok = asyncio.run(plugin._send_tts(
            ev, "这是一段足够长会被切分成多段的测试文本，用来验证全 45007 失败语义。" * 6))

    assert ok is False, "全部段 45007 应返回 False，不得误计成功"
    assert len(send_calls) >= 2, "每段都应尝试发送（Provider 路径无法二分，只能逐段记失败）"


def test_provider_fallback_45007_not_counted_as_sent():
    """Provider 回退段 45007 不误计为成功（is True 才计）。"""
    from unittest.mock import AsyncMock

    from main import _send_voice_with_backoff

    class _Ev:
        async def send(self, chain):
            raise Exception("Error code: 45007, message: playtime out of limit")

    result = asyncio.run(_send_voice_with_backoff(_Ev(), "/tmp/a.mp3"))
    assert result == "too_long", "45007 应返回 too_long 信号而非 True"
    # 验证调用方按 is True 判断 → "too_long" 不会被误计成功
    assert (result is True) is False


# ---- v4.8.107：输出侧机器腔过滤 + TTS 结果回注 ----

def test_strip_machine_talk_removes_tts_markers():
    """机器腔残留（(TTS active)/注:.../系统说明）应从朗读文本中剥离。"""
    from main import _strip_machine_talk

    assert "(TTS active)" not in _strip_machine_talk("别难过呀。(TTS active)")
    assert "注：当前仍无法发送语音文件" not in _strip_machine_talk("好。（注：当前仍无法发送语音文件）")
    assert "系统未安装语音合成插件" not in _strip_machine_talk("抱歉，系统未安装语音合成插件。")
    assert "TTS功能也正常" not in _strip_machine_talk("现在TTS功能也正常。你放心吧。")
    # 正常内容保留
    out = _strip_machine_talk("别难过呀，我一直在你身边。")
    assert "别难过呀" in out and "我一直在你身边" in out


def test_strip_machine_talk_keeps_normal_text():
    """清洗不误伤正常文本（无机器腔时原样保留）。"""
    from main import _strip_machine_talk

    t = "今天天气不错，阳光透过窗帘洒在桌面上。"
    assert _strip_machine_talk(t) == t


def test_tts_result_recorded_in_kv():
    """_tts_on_response 应把发送结果写入 KV（供请求钩子回注）。"""
    from tests.test_admin_api import FakeContext

    class KVPlugin2(MingbuPlugin):
        def __init__(self, cfg):
            self._store = {}
            super().__init__(FakeContext(), cfg)
        async def get_kv_data(self, key, default=None):
            return self._store.get(key, default)
        async def put_kv_data(self, key, value):
            self._store[key] = value

    plugin = KVPlugin2({
        "background_watch_enabled": False,
        "tts_enabled": True,
        "tts_long_text_threshold": 500,
        "tts_api_key": "k",
        "tts_api_base": "https://x/v1",
        "tts_model": "m",
    })
    with patch.object(plugin, "_send_tts", AsyncMock(return_value=True)):
        resp = FakeResp("别难过，我一直都在你身边呀。")
        ev = FakeEvent(msg_id="ttsres1")
        asyncio.run(plugin._on_llm_response(ev, resp))
    rec = plugin._store.get("mingbu.last_tts_result:u1")
    assert rec is not None, "应记录 TTS 结果"
    assert rec.get("ok") is True
    assert "情绪" in rec.get("reason", "") or "joy" in rec.get("reason", "") or "sad" in rec.get("reason", "")


def test_tts_result_failure_recorded():
    """发送失败时结果记录 ok=False。"""
    from tests.test_admin_api import FakeContext

    class KVPlugin3(MingbuPlugin):
        def __init__(self, cfg):
            self._store = {}
            super().__init__(FakeContext(), cfg)
        async def get_kv_data(self, key, default=None):
            return self._store.get(key, default)
        async def put_kv_data(self, key, value):
            self._store[key] = value

    plugin = KVPlugin3({
        "background_watch_enabled": False,
        "tts_enabled": True,
        "tts_long_text_threshold": 500,
        "tts_api_key": "k",
        "tts_api_base": "https://x/v1",
        "tts_model": "m",
    })
    with patch.object(plugin, "_send_tts", AsyncMock(return_value=False)):
        resp = FakeResp("别难过，我一直都在你身边呀。")
        ev = FakeEvent(msg_id="ttsres2")
        asyncio.run(plugin._on_llm_response(ev, resp))
    rec = plugin._store.get("mingbu.last_tts_result:u1")
    assert rec is not None
    assert rec.get("ok") is False


# ---- v4.8.108+：图片上下文串扰根治（新图不被识别成旧图） ----

def test_image_message_injects_current_image_hint():
    """当前消息含图片时，请求上下文应注入当前图摘要。"""
    from types import SimpleNamespace

    from star_mixins.llm_hook import _inject_current_image_hint

    class FakeMsgObj:
        type = "FriendMessage"
        message = [SimpleNamespace(type="Image", url="http://x/a.jpg")]

    class FakeEventImg:
        message_obj = FakeMsgObj()
        message_str = "你能识别这张图吗"

        def get_sender_id(self):
            return "u-img"

    class FakeReqImg:
        def __init__(self):
            self.extra_user_content_parts = []

    class FakePlugin:
        _config = {"image_vision_enabled": True}

        async def _summarize_image_urls(self, uid, image_urls):
            return "城市街景中的三位奔跑角色"

    req = FakeReqImg()
    ok = asyncio.run(_inject_current_image_hint(FakePlugin(), FakeEventImg(), req))
    assert ok is True, "含图消息应注入提示"
    joined = " ".join(str(p) for p in req.extra_user_content_parts)
    assert "城市街景中的三位奔跑角色" in joined, "应注入当前图摘要内容"
    assert "内容大致是" in joined and "不用再调用任何文件读取工具" in joined


def test_image_hint_skipped_without_image():
    """无图片消息不注入；image_vision 关闭时不注入。"""
    from types import SimpleNamespace

    from star_mixins.llm_hook import _inject_current_image_hint

    class FakeMsgObjNoImg:
        type = "FriendMessage"
        message = [SimpleNamespace(type="Text", text="你好")]

    class FakeEventText:
        message_obj = FakeMsgObjNoImg()

        def get_sender_id(self):
            return "u-text"

    class FakeReq:
        def __init__(self):
            self.extra_user_content_parts = []

    class FakePlugin:
        _config = {"image_vision_enabled": True}

        async def _summarize_image_urls(self, uid, image_urls):
            return "摘要"

    req = FakeReq()
    assert asyncio.run(_inject_current_image_hint(
        FakePlugin(), FakeEventText(), req)) is False
    assert req.extra_user_content_parts == []

    # image_vision 关闭时即使有图也不注入
    class FakeMsgObjImg:
        type = "FriendMessage"
        message = [SimpleNamespace(type="Image", url="http://x/a.jpg")]

    class FakeEventImg2:
        message_obj = FakeMsgObjImg()

        def get_sender_id(self):
            return "u-img2"

    class FakePluginOff:
        _config = {"image_vision_enabled": False}

        async def _summarize_image_urls(self, uid, image_urls):
            return "摘要"

    req2 = FakeReq()
    assert asyncio.run(_inject_current_image_hint(
        FakePluginOff(), FakeEventImg2(), req2)) is False
    assert req2.extra_user_content_parts == []


def test_image_hint_fallback_when_summary_fails():
    """视觉摘要失败/超时时，回退为泛化「以当前图为准」提示。"""
    from types import SimpleNamespace

    from star_mixins.llm_hook import _inject_current_image_hint

    class FakeMsgObj:
        type = "FriendMessage"
        message = [SimpleNamespace(type="Image", url="http://x/a.jpg")]

    class FakeEventImg:
        message_obj = FakeMsgObj()

        def get_sender_id(self):
            return "u-img3"

    class FakeReq:
        def __init__(self):
            self.extra_user_content_parts = []

    class FakePluginEmpty:
        _config = {"image_vision_enabled": True}

        async def _summarize_image_urls(self, uid, image_urls):
            return ""  # 摘要失败返回空

    req = FakeReq()
    ok = asyncio.run(_inject_current_image_hint(FakePluginEmpty(), FakeEventImg(), req))
    assert ok is True, "摘要失败也应注入泛化提示"
    joined = " ".join(str(p) for p in req.extra_user_content_parts)
    assert "当前图片" in joined and "新图片" in joined


# ---- v4.8.110：45007 源头预防（单段钳制 150 字，防企微吞错误压文本） ----

def test_tts_max_chunk_clamped_to_150():
    """用户配置 tts_max_chunk_chars=300 会被钳制到 150（防 45007 静默吞错）。

    v4.9.0：钳制逻辑从 _send_tts 散读收敛至 tts_config.load_tts_settings
    （加载时钳制）。测试同步升级：源码文本断言 → 行为断言（不随实现措辞
    漂移），并新增源码防线钉住「_send_tts 必须经统一配置层读配置」。
    """
    import inspect

    from astrbot_plugin_mingbu.tts_config import load_tts_settings

    from main import TTS_MAX_CHUNK_CHARS, MingbuPlugin, _split_tts_chunks

    assert TTS_MAX_CHUNK_CHARS == 150, "默认单段上限应为 150（60s 内安全）"
    # 行为验证：配置 300 → 加载后必须钳制为 150；缺省亦为 150
    assert load_tts_settings({"tts_max_chunk_chars": 300}).max_chunk_chars == 150, \
        "tts_max_chunk_chars=300 必须被钳制为 150"
    assert load_tts_settings({}).max_chunk_chars == 150, "缺省单段上限应为 150"
    # 源码防线：_send_tts 必须经 load_tts_settings 读配置（禁止散读绕过钳制）
    src = inspect.getsource(MingbuPlugin._send_tts)
    assert "load_tts_settings" in src, "_send_tts 必须经 TTSSettings 统一读取配置"
    # 300 字长文切段：每段不得超过 150
    long_text = "这是一段用来验证切段钳制的长文本。" * 30  # 约 480 字
    chunks = _split_tts_chunks(long_text, 300)
    assert all(len(c) <= 300 for c in chunks)  # 原始切段尊重传入上限


def test_split_tts_chunks_respects_150_cap():
    """默认 150 上限下，长文本切段每段 ≤150。"""
    from main import _split_tts_chunks

    long_text = "今天天气不错，阳光透过窗帘洒在桌面上。" * 20  # 约 400 字
    chunks = _split_tts_chunks(long_text)
    assert len(chunks) >= 3, "400 字应按 150 上限切为多段"
    assert all(len(c) <= 150 for c in chunks), "每段不得超过 150 字"
