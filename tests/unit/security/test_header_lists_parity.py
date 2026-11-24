"""三份「敏感请求头」名单必须同口径。

为什么要有这个文件：同一件事（哪些请求头是凭据）在仓库里被实现了三次：

| 位置 | 用途 |
|---|---|
| `security.egress._SENSITIVE_HEADER` | 出网守卫：识别请求里带了哪些凭据头 |
| `fetching.browser_guards._SENSITIVE_HEADER_NAMES` | 跨来源时剔除凭据头 |
| `plugins.plugin_subprocess_adapter._SENSITIVE_HEADERS` | 传给插件子进程前遮蔽 |

实测（2026-11-24）：第三份漏了 `api-key` 与 `x-auth-token`，这两个头**原样**
进入了插件生命周期 JSON（`_redact_headers` 是真实出口）。

三份分开维护就必然漂移 —— 本文件把「同口径」变成可执行判据。
"""

from __future__ import annotations

import pytest

from omnicrawler.fetching import browser_guards
from omnicrawler.plugins import plugin_subprocess_adapter
from omnicrawler.security import egress

#: 三份名单都必须覆盖的最小集合。
#:
#: 判据取这个显式集合，而不是「三份都有的交集」—— 后者在全部漏掉某一项时
#: 仍然通过（空集合恒真）。
REQUIRED = frozenset(
    {
        "authorization",
        "proxy-authorization",
        "cookie",
        "set-cookie",
        "x-api-key",
        "api-key",
        "x-auth-token",
    }
)


def _plugin_names() -> frozenset[str]:
    return frozenset(name.casefold() for name in plugin_subprocess_adapter._SENSITIVE_HEADERS)


def _browser_names() -> frozenset[str]:
    return frozenset(name.casefold() for name in browser_guards._SENSITIVE_HEADER_NAMES)


def _egress_matches(name: str) -> bool:
    return bool(egress._SENSITIVE_HEADER.match(name))


@pytest.mark.parametrize("name", sorted(REQUIRED))
def test_required_header_is_covered_by_every_list(name: str) -> None:
    """每个该遮蔽的头，三份名单都要认。"""
    missing = []
    if not _egress_matches(name):
        missing.append("security.egress")
    if name not in _browser_names():
        missing.append("fetching.browser_guards")
    if name not in _plugin_names():
        missing.append("plugins.plugin_subprocess_adapter")
    assert not missing, f"{name} 未被以下名单覆盖：{missing}"


def test_plugin_and_browser_lists_are_identical() -> None:
    """两份 frozenset 形态的名单应当逐字相等（差异没有正当理由）。"""
    assert _plugin_names() == _browser_names(), (
        f"仅 plugin 有：{sorted(_plugin_names() - _browser_names())}；"
        f"仅 browser 有：{sorted(_browser_names() - _plugin_names())}"
    )


def test_case_insensitive_matching() -> None:
    """HTTP 头名大小写不敏感，遮蔽也必须如此。"""
    assert "x-api-key" in _plugin_names()
    assert _egress_matches("X-API-KEY")


def test_the_required_set_is_not_empty() -> None:
    """空集合会让上面所有参数化用例恒真 —— 这条挡住那种退化。"""
    assert len(REQUIRED) >= 7
