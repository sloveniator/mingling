"""刷新（热重载/重新 configure）后外部服务配置不丢失的回归测试。

根因：AstrBot 在热重载 / 重新实例化 Star 时会再次调用 ``MingbuCore.configure()``，
传入的静态 config 不含 KV 运行时覆盖层（``mingbu.runtime_config`` 里的外部服务
API 地址/密钥/模型等）。旧实现用 ``self._config = dict(config)`` 整体替换内存态，
且 ``ensure()`` 的 ``_ensured`` 守卫让 ``_load_runtime_config`` 不再执行，导致
KV 里的外部服务地址在刷新后从内存态消失、运行时读到空值。

修复：
1. ``configure()`` 原地更新（clear+update）并重新合并已缓存的 KV 覆盖层镜像
   ``_runtime_overlay``；
2. ``ensure()`` 把 ``_load_runtime_config`` 提到 ``_ensured`` 守卫之前，任何一次
   ``initialize()``（含热重载）都会重新从 KV 捞回覆盖层。
"""
import asyncio

from tests.test_admin_api import FakeContext, call, payload_of


def _reset_core():
    import core as core_mod
    core_mod._CORE = None


def _make_star(store, cfg=None):
    """构造 Star，但不复位 core 单例——模拟「热重载」时 core 保留、只重实例化 Star。"""
    from stars.admin import StarAdmin

    class S(StarAdmin):
        async def get_kv_data(self, key, default=None):
            return store.get(key, default)

        async def put_kv_data(self, key, value):
            store[key] = value

    if cfg is None:
        cfg = {
            "background_watch_enabled": False,
            "self_evolution_enabled": False,
            "proactive_enabled": False,
            "news_enabled": False,
        }
    return S(FakeContext(), cfg)


def _init(star):
    asyncio.run(star.initialize())


def _post(star, body):
    return payload_of(call(star.api_config, method="POST", body=body))


def _get(star):
    return payload_of(call(star.api_config, method="GET"))


def test_hot_reload_preserves_external_service_urls():
    """热重载（重实例化 Star，core 保留）后，KV 里的外部服务地址/密钥/模型不丢。"""
    _reset_core()
    store = {}

    # 首次加载 + 保存外部服务配置
    # 注：STT 已改走 AstrBot Provider 路由（v4.8.x），stt_api_base/ stt_api_key
    # 直连键已从 _conf_schema.json 移除、coerce_by_schema 会丢弃它们，故改用
    # 仍在用且声明于 schema 的外部服务键（tts / image_vision / interpret）验证持久化。
    star1 = _make_star(store)
    _init(star1)
    _post(star1, {
        "tts_api_base": "https://tts.example.com/v1",
        "tts_api_key": "sk-tts-secret",
        "tts_model": "tts-test",
        "image_vision_api_base": "https://vision.example.com/v1",
        "image_vision_api_key": "sk-vision-secret",
        "mingbu_interpret_base_url": "https://interpret.example.com/v1",
    })

    # 热重载：重新实例化 Star（触发 __init__ → core.configure(静态 config)），
    # 再次 initialize（ensure 命中 _ensured=True，但会重新加载 KV 覆盖层）。
    star2 = _make_star(store)
    _init(star2)

    cfg = star2.core._config
    assert cfg.get("tts_api_base") == "https://tts.example.com/v1"
    assert cfg.get("tts_api_key") == "sk-tts-secret"
    assert cfg.get("tts_model") == "tts-test"
    assert cfg.get("image_vision_api_base") == "https://vision.example.com/v1"
    assert cfg.get("image_vision_api_key") == "sk-vision-secret"
    assert cfg.get("mingbu_interpret_base_url") == "https://interpret.example.com/v1"


def test_configure_alone_does_not_drop_overlay():
    """只重新 configure（不重新 initialize）也不能丢 KV 覆盖层——覆盖 AstrBot
    仅调用 configure 而不重跑 initialize 的热更新路径。"""
    _reset_core()
    store = {}

    star = _make_star(store)
    _init(star)
    _post(star, {"tts_api_base": "https://tts.example.com/v1", "tts_api_key": "sk-tts"})

    # 模拟 AstrBot 仅推送新的静态 config（不含外部服务键）
    star.core.configure({"background_watch_enabled": False})

    assert star.core._config.get("tts_api_base") == "https://tts.example.com/v1"
    assert star.core._config.get("tts_api_key") == "sk-tts"


def test_partial_save_syncs_overlay_mirror():
    """保存后 overlay 镜像必须包含本次提交，否则下次 configure 会用旧镜像覆盖新值。"""
    _reset_core()
    store = {}

    star = _make_star(store)
    _init(star)

    _post(star, {"tts_api_base": "https://tts.example.com/v1"})
    assert star.core._runtime_overlay.get("tts_api_base") == "https://tts.example.com/v1"

    # 第二次只保存另一个键，overlay 镜像应同时保留两个键
    _post(star, {"tts_model": "cosyvoice-v1"})
    overlay = star.core._runtime_overlay
    assert overlay.get("tts_api_base") == "https://tts.example.com/v1"
    assert overlay.get("tts_model") == "cosyvoice-v1"


def test_mingbu_api_url_in_kv_rebuilds_client_after_reload():
    """KV 里的命理服务地址在热重载后应驱动客户端重建（请求地址不失效）。"""
    _reset_core()
    store = {}

    star1 = _make_star(store)
    _init(star1)
    _post(star1, {"mingbu_api_url": "http://192.168.9.9:2963"})
    assert star1.core.api.base_url == "http://192.168.9.9:2963"

    # 热重载
    star2 = _make_star(store)
    _init(star2)

    assert star2.core._config.get("mingbu_api_url") == "http://192.168.9.9:2963"
    assert star2.core.api.base_url == "http://192.168.9.9:2963"


def test_save_writes_back_to_astrbot_static_config():
    """后台页保存后，应把值回写进 AstrBot 静态 self.config 并调用 save_config()，
    让原生插件配置页与后台页展示同一份值（消除双入口不同步）。"""
    _reset_core()
    store = {}

    class FakeAstrBotConfig(dict):
        def __init__(self):
            super().__init__()
            self.saved = 0

        def save_config(self):
            self.saved += 1

    star = _make_star(store)
    _init(star)
    # 模拟 AstrBot 传入的静态 AstrBotConfig 对象
    static_cfg = FakeAstrBotConfig()
    star.config = static_cfg

    _post(star, {"tts_api_base": "https://api.siliconflow.cn/v1", "tts_api_key": "sk-sf"})

    # 键已同步进静态 config，且被持久化
    assert static_cfg.get("tts_api_base") == "https://api.siliconflow.cn/v1"
    assert static_cfg.get("tts_api_key") == "sk-sf"
    assert static_cfg.saved >= 1


def test_save_without_static_config_silently_skips():
    """没有 self.config（或没有 save_config）时回写应静默跳过，不影响后台保存。"""
    _reset_core()
    store = {}

    star = _make_star(store)
    _init(star)
    star.config = {}  # 普通 dict，无 save_config
    resp = _post(star, {"tts_api_base": "https://tts.example.com/v1"})
    assert resp.get("ok") is True
    # KV 仍是最终真源
    assert star.core._config.get("tts_api_base") == "https://tts.example.com/v1"


def test_star_exposes_config_after_init():
    """MingbuStar.__init__ 必须显式保存 self.config（AstrBot 官方模板要求），
    否则 _sync_astrbot_static_config 拿不到 AstrBotConfig、原生页永不更新。"""
    _reset_core()
    store = {}

    cfg = {
        "tts_api_base": "https://api.siliconflow.cn/v1",
        "background_watch_enabled": False,
    }
    star = _make_star(store, cfg)
    _init(star)
    # self.config 应等于传入的 config（不是 None），保证回写可用
    assert star.config is not None
    assert star.config.get("tts_api_base") == "https://api.siliconflow.cn/v1"
