"""插件市场页的**只读**依赖徽标测试（决策四 A：打开即检测，不弹框）。

只测纯函数 ``dependency_badge`` / ``dependency_badge_tooltip`` /
``plugin_needs_dependency_install``：齐全时静默（§R2），缺失时只给展示文本。
"""

from __future__ import annotations

from omnicrawler.gui.views.plugin_market_logic import (
    dependency_badge,
    dependency_badge_tooltip,
    plugin_needs_dependency_install,
)
from omnicrawler.plugins.plugin_dependency_check import DependencyStatus


def _status(*missing: str) -> DependencyStatus:
    return DependencyStatus(
        plugin_id="demo",
        declared=missing,
        missing=missing,
        requirements=missing,
    )


class TestDependencyBadge:
    def test_ready_is_silent(self) -> None:
        assert dependency_badge(_status()) == ""
        assert dependency_badge_tooltip(_status()) == ""
        assert plugin_needs_dependency_install(_status()) is False

    def test_none_status_is_silent(self) -> None:
        assert dependency_badge(None) == ""
        assert dependency_badge_tooltip(None) == ""
        assert plugin_needs_dependency_install(None) is False

    def test_missing_shows_count(self) -> None:
        assert dependency_badge(_status("playwright", "pypdf")) == "[缺依赖 2]"
        assert plugin_needs_dependency_install(_status("playwright")) is True

    def test_tooltip_lists_all(self) -> None:
        tip = dependency_badge_tooltip(_status("playwright", "pypdf"))
        assert "playwright" in tip and "pypdf" in tip
