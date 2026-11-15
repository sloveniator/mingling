"""
tests/test_metadata_consistency.py -- 发布元数据一致性
========================================================

起因（v4.8.0）：发布时只改了 _version.py，metadata.yaml 与 README 徽章仍停在
4.7.4。AstrBot 插件管理器读 metadata.yaml，管理面板读 _version.py（经
stars/admin.py 的 /stats 接口返回），于是同一插件在两处显示不同版本，
更新检查也随之失准。这类"发布清单漏项"靠人肉 review 很难稳定拦住，用测试钉死。

v4.8.2 复盘时发现本文件自身有三处失准，一并修正：

1. 必填字段清单是凭印象写的。按 AstrBot StarMetadata 的实际定义，必填项为
   name / author / desc / version 四个，display_name 属可选（v4.5.0+ 才生效）；
   而真正决定"能否检查更新"的 repo 既没被检查，metadata.yaml 里也确实没有。
2. changelog 断言用全文子串匹配，README 任意位置出现版本号即可通过——例如
   文件结构注释里的"v4.8.2 从 core.py 拆出"。这与"发了版却没写变更说明"的
   拦截意图不符，已收紧到「版本演进」章节内按条目格式匹配。
3. 漏掉了第四处版本号：pages/admin/index.html 页脚占位。它硬编码在 4.7.4 且
   app.js 从不回填，面板长期显示错版本——恰是本文件声称要防的场景。现已改为
   由 /stats 回填，这里同时钉死降级占位值与回填逻辑本身。
"""
import os
import re

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_SEMVER = re.compile(r"^\d+\.\d+\.\d+$")

# AstrBot StarMetadata 中标记为 Required 的字段。display_name / short_desc 虽然
# 本插件都有，但框架层面可选，不在此列——把可选项当必填会让断言失去指向性。
_REQUIRED_METADATA_FIELDS = ("name", "author", "desc", "version")


def _read(rel: str) -> str:
    with open(os.path.join(_ROOT, rel), encoding="utf-8") as f:
        return f.read()


def _version_py() -> str:
    m = re.search(r'VERSION\s*=\s*["\']([^"\']+)["\']', _read("_version.py"))
    assert m, "_version.py 中找不到 VERSION"
    return m.group(1)


def _metadata_field(field: str) -> str:
    m = re.search(rf"^{field}:\s*(\S+)\s*$", _read("metadata.yaml"), re.M)
    assert m, f"metadata.yaml 中找不到 {field}"
    return m.group(1).strip("\"'")


def _readme_badge() -> str:
    m = re.search(r"img\.shields\.io/badge/version-([0-9.]+)-", _read("README.md"))
    assert m, "README.md 中找不到 version 徽章"
    return m.group(1)


def _readme_section(title: str) -> str:
    """截取某个二级章节的正文，用于把断言限定在语义正确的范围内。"""
    body = _read("README.md")
    m = re.search(rf"^##\s+{re.escape(title)}\s*$", body, re.M)
    assert m, f"README.md 中找不到章节「{title}」"
    nxt = re.search(r"^##\s+", body[m.end():], re.M)
    return body[m.end(): m.end() + nxt.start()] if nxt else body[m.end():]


def _admin_page_version() -> str:
    m = re.search(
        r'<span\s+id="version">\s*([0-9.]+)\s*</span>',
        _read("pages/admin/index.html"),
    )
    assert m, "pages/admin/index.html 中找不到 id=version 的页脚占位"
    return m.group(1)


def test_version_is_semver():
    assert _SEMVER.match(_version_py()), f"版本号不是 semver: {_version_py()}"


def test_metadata_matches_version_py():
    assert _metadata_field("version") == _version_py(), (
        f"metadata.yaml({_metadata_field('version')}) 与 _version.py({_version_py()}) 不一致；"
        "AstrBot 插件管理器读前者、管理面板读后者，发布前必须同步"
    )


def test_readme_badge_matches_version_py():
    assert _readme_badge() == _version_py(), (
        f"README 徽章({_readme_badge()}) 与 _version.py({_version_py()}) 不一致"
    )


def test_admin_page_placeholder_matches_version_py():
    """管理面板页脚的降级占位值必须随发布同步。

    它是接口不可用时用户唯一能看到的版本信息，v4.8.2 之前长期停在 4.7.4。
    """
    assert _admin_page_version() == _version_py(), (
        f"pages/admin/index.html 页脚占位({_admin_page_version()}) 与 "
        f"_version.py({_version_py()}) 不一致"
    )


def test_admin_page_version_is_refreshed_from_api():
    """页脚版本必须由 /stats 回填，纯静态字面量会随时间必然腐化。

    历史教训：admin API 早就返回了 version 字段，前端却从不消费，
    于是占位值一路滞后三个版本无人察觉。
    """
    app_js = _read("pages/admin/app.js")
    writes_element = re.search(r'\$\(\s*["\']version["\']\s*\)', app_js)
    reads_api_field = re.search(r"\.version\b", app_js)
    assert writes_element and reads_api_field, (
        "pages/admin/app.js 未从 /stats 响应回填页脚版本号；"
        "只靠 index.html 里的字面量会导致面板长期显示旧版本"
    )


def test_changelog_entry_exists_in_version_history():
    """「版本演进」章节里必须有当前版本的条目，避免发了版却没写变更说明。

    刻意限定在该章节内并要求匹配条目格式（**vX.Y.Z**），而不是全文搜字符串——
    否则正文任意处顺带提及版本号就能让断言失效。
    """
    version = _version_py()
    section = _readme_section("版本演进")
    assert re.search(rf"^\*\*v{re.escape(version)}\*\*", section, re.M), (
        f"README「版本演进」章节里没有 **v{version}** 的条目"
    )


def test_metadata_required_fields():
    raw = _read("metadata.yaml")
    for field in _REQUIRED_METADATA_FIELDS:
        assert re.search(rf"^{field}:", raw, re.M), f"metadata.yaml 缺少必填字段 {field}"


def test_metadata_declares_repo():
    """repo 是 AstrBot 更新检查的来源，也是插件市场 schema 的 MUST 字段。"""
    repo = _metadata_field("repo")
    assert repo.startswith("https://github.com/"), (
        f"metadata.yaml 的 repo 必须是 HTTPS GitHub 地址，当前为 {repo}"
    )


def test_astrbot_version_requirement_matches_readme():
    """元数据声明的 AstrBot 版本下限要与 README 对用户的承诺一致。

    此前 README 三处写「需要 AstrBot ≥ 4.26」，元数据却没有 astrbot_version，
    PEP 440 校验从未生效，低版本用户装上后只会在运行时报错。
    """
    declared = _metadata_field("astrbot_version")
    m = re.match(r">=\s*([0-9.]+)$", declared)
    assert m, f"astrbot_version 应为 PEP 440 下限约束（如 >=4.26），当前为 {declared}"

    badge = re.search(r"img\.shields\.io/badge/AstrBot-([0-9.]+)%2B-", _read("README.md"))
    assert badge, "README.md 中找不到 AstrBot 版本徽章"
    assert m.group(1) == badge.group(1), (
        f"metadata.yaml 声明 AstrBot {declared}，README 徽章却写 {badge.group(1)}+"
    )
