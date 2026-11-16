"""2.0 项②：AstrBot 原生插件配置页保存路径的 observe 热重载实测（证伪优先）。

背景：命灵后台配置页（stars/apis/system.py save_config）保存 integration_mode
会显式走 configure() + _apply_hot_reload(data) → reload_flags()，已闭环。
但 **AstrBot 原生插件配置页**（data/config/<plugin>_config.json 的 WebUI 编辑）
不经 save_config，走 AstrBot 自身的 Star 重载生命周期。本测试实证该路径下
core.flags 是否随之热更新（observe 是否不重启即生效）。

AstrBot 原生页两种可能触发路径，分别实测：
  P1 只回调 core.configure(new_static)：configure 不重建 flags。
  P2 整个 Star 重新实例化（__init__ → configure → initialize → ensure）：
     ensure() 用 FeatureFlags.from_config(_config) 重建，但原生页传入的是
     **静态 config**，不含 KV 运行时覆盖层里的 integration_mode=observe，
     故重建出的 flags 可能仍是 enhance。
"""
import asyncio

import pytest
from test_admin_api import build_star


def _init(star):
    asyncio.run(star.initialize())
    return star


def test_native_page_configure_only_keeps_stale_flags():
    """P1：原生页只回调 core.configure(静态 config)（无 reload_flags）→ flags 不变。

    这是「原生页 observe 切换不重启不生效」的根因实证：configure 本身不重建
    flags，缺 _apply_hot_reload 的 reload_flags 推送。
    """
    star = _init(build_star())
    assert star.core.flags.mode == "enhance"

    # 模拟原生页：直接改 core._config 后只调 configure（AstrBot 热重载回调形态）
    star.core._config["integration_mode"] = "observe"
    star.core.configure(star.core._config)

    # 证伪断言：configure 不触发 reload_flags，flags 仍为 enhance（未热更新）
    assert star.core.flags.mode == "enhance", (
        "若此断言失败(flags 已变 observe)，说明 configure 会重建 flags，"
        "则原生页无需额外接线；实测应为 enhance 即缺口所在"
    )


def test_native_page_reinstantiate_reads_static_not_overlay():
    """P2：原生页重载形态 = core.configure(静态 config)（不触发 reload_flags）。

    实测取证：configure() 只合并 _config(+overlay) + _sync_api_client，**不重建
    flags**。即便 overlay 已含 integration_mode=observe，仅经 configure 后
    flags.mode 仍为 enhance——必须显式 reload_flags() 才生效。命灵后台页靠
    _apply_hot_reload→reload_flags 补上了这步，原生页缺这步，故 observe 不重启不生效。
    """
    star = _init(build_star())
    # 命灵后台页语义：observe 进 KV 覆盖层
    star.core._runtime_overlay = {"integration_mode": "observe"}
    star.core.configure(star.core._config)
    # 实测断言：仅 configure（无 reload_flags）→ flags 仍是 enhance（缺口）
    assert star.core.flags.mode == "enhance", (
        "若此断言失败(flags 已 observe)，说明 configure 会重建 flags；"
        "实测为 enhance 证明原生页缺 reload_flags 推送")
    # 对照：显式 reload_flags() 后 observe 才生效（命灵后台页正是靠这步）
    star.core.reload_flags()
    assert star.core.flags.mode == "observe", "显式 reload_flags 后 observe 应生效"

    # 模拟原生页整 Star 重载：丢弃覆盖层（原生页只持久化静态 config）
    fresh = build_star()  # 干净内核，静态 config 无 integration_mode=observe
    _init(fresh)
    # 覆盖层被丢弃 → 重载后 flags 回落 enhance（原生页若不同步 overlay 即此结果）
    assert fresh.core.flags.mode == "enhance", (
        "覆盖层被丢弃时重载后应为 enhance；若 observe 丢失即原生页未同步 overlay")
    print(f"\n[native-page-probe] 仅 configure 后 mode={star.core.flags.mode}(缺口)；"
          f"reload_flags 后={star.core.flags.mode}；丢弃 overlay 重载后={fresh.core.flags.mode}")


def test_full_reload_picks_up_static_observe():
    """对照：若原生页保存触发整 Star 重载且 observe 已持久化到静态 config，
    ensure() 重建 flags 能否读到 observe 并生效？——把两条可能路径都测清。"""
    star = _init(build_star())
    assert star.core.flags.mode == "enhance"

    # 模拟原生页把 observe 持久化进静态 config 后，触发整 Star 重载（干净内核）
    fresh = build_star(integration_mode="observe")
    _init(fresh)
    assert fresh.core.flags.mode == "observe", (
        "整 Star 重载 + 静态 config 含 observe → ensure 重建 flags 应读到 observe")
    assert fresh.core.flags.should_modify_request() is False
    print(f"\n[full-reload-probe] 静态 config 含 observe 的整重载 → mode="
          f"{fresh.core.flags.mode}（ensure 从静态 config 重建，路径健康）")


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
