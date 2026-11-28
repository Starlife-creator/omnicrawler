"""插件权限白名单：主仓与市场仓必须同集合（`check_plugin_permission_parity`）。

## 它守什么（2026-11-27 实测）

| 位置 | 用途 |
|---|---|
| 主仓 `plugin_sandbox.ALLOWED_PERMISSIONS` | **加载时**判「这个插件能不能跑」 |
| 市场 `validate_submission.ALLOWED_PLUGIN_PERMISSIONS` | **投稿时**判「这个插件能不能发」 |

两侧不同集合 → 单向死锁，且症状都不指向根因：

- 主仓有、市场无 ⇒ 插件本地能跑，投稿被拒（作者以为是自己写错了）
- 市场有、主仓无 ⇒ 投稿过审，用户装了加载失败（放行了一个跑不起来的插件）

实测漏项：`component:ocr`（主仓 2026-11-17 加的，市场侧漏了三个月）。
"""

from __future__ import annotations

from tools.gates.check_plugin_permission_parity import (
    differences,
    main,
    market_permissions,
)

_SAMPLE = """
ALLOWED_PLUGIN_PERMISSIONS = {
    "records:read", "records:write",
    # 注释里提到 "fake:commented" 不该被计入
    "component:ocr",
}
"""


def test_parses_the_market_permission_set() -> None:
    assert market_permissions(_SAMPLE) == {"records:read", "records:write", "component:ocr"}


def test_comment_lines_inside_the_set_are_ignored() -> None:
    """★ 注释里形似权限名的字符串必须被剥掉。

    真实文件里那段集合带注释；若不剥注释，「市场少一项」的判据可能
    被一条注释抵消 —— 那是**假绿**。
    """
    parsed = market_permissions(_SAMPLE)
    assert "fake:commented" not in parsed, f"注释被当成了权限：{sorted(parsed)}"


def test_missing_block_returns_empty_not_everything() -> None:
    """★ 解析失败必须返回空集，让 `main` 的「判据失效」分支接住。

    若返回「全部权限」，「市场有没有漏」就恒真了。
    """
    assert market_permissions("# 没有这个常量") == set()


def test_differences_reports_both_directions() -> None:
    only_core, only_market = differences({"a:1", "b:2"}, {"b:2", "c:3"})
    assert only_core == ["a:1"]
    assert only_market == ["c:3"]


def test_differences_empty_when_equal() -> None:
    assert differences({"a:1"}, {"a:1"}) == ([], [])


def test_real_repository_is_consistent() -> None:
    """端到端：真实两个仓必须一致（市场仓不可达时 skip）。"""
    import pytest

    from tools.gates.check_plugin_permission_parity import MARKET_VALIDATOR

    if not MARKET_VALIDATOR.is_file():
        pytest.skip("没有市场 checkout")
    assert main([]) == 0


def test_core_whitelist_is_never_empty() -> None:
    """★ 空集合判红的前提：主仓白名单本身不能是空的。"""
    from omnicrawler.plugins.plugin_sandbox import ALLOWED_PERMISSIONS

    assert len(ALLOWED_PERMISSIONS) >= 10

# ── 能力名同样是跨仓漂移的一类 ────────────────────────────────────────


_CAP_SAMPLE = """
ALLOWED_CAPABILITIES = {
    "system.info",
    # 注释里的 "ghost.commented" 不该被计入
    "view.richtext",
    "view.progress",
}
"""


def test_parses_the_market_capability_set() -> None:
    from tools.gates.check_plugin_permission_parity import market_capabilities

    assert market_capabilities(_CAP_SAMPLE) == {"system.info", "view.richtext", "view.progress"}


def test_capability_comments_are_ignored() -> None:
    from tools.gates.check_plugin_permission_parity import market_capabilities

    parsed = market_capabilities(_CAP_SAMPLE)
    assert "ghost.commented" not in parsed, f"注释被当成了能力：{sorted(parsed)}"


def test_missing_capability_block_returns_empty() -> None:
    from tools.gates.check_plugin_permission_parity import market_capabilities

    assert market_capabilities("# 没有这个常量") == set()


def test_capability_lists_are_equal_across_repos() -> None:
    """★ 端到端：两侧能力白名单必须逐项相等（市场仓不可达时 skip）。"""
    import pytest

    from omnicrawler.plugins.plugin_broker_contracts import CAPABILITY_VERSIONS
    from tools.gates.check_plugin_permission_parity import (
        MARKET_VALIDATOR,
        differences,
        market_capabilities,
    )

    if not MARKET_VALIDATOR.is_file():
        pytest.skip("没有市场 checkout")
    market = market_capabilities(MARKET_VALIDATOR.read_text(encoding="utf-8"))
    only_core, only_market = differences(set(CAPABILITY_VERSIONS), market)
    assert only_core == [], f"只在主仓 ⇒ 声明它的插件投稿会被拒：{only_core}"
    assert only_market == [], f"只在市场 ⇒ 过审后运行期被拒载：{only_market}"
