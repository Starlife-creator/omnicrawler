"""Tests for UI registration buckets and ui:* permission policy.

Covers register_theme/action/panel/status + describe() output, and the
permission rule: ui:* is auto-approved for local-source plugins but must
be explicitly approved for market-source plugins.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from omnicrawl.plugins.plugins import (
    MARKET_DIR_NAME,
    Registry,
    load_local_plugins,
)


def _write_plugin(plugin_dir: Path, body: str) -> Path:
    plugin_dir.mkdir(parents=True, exist_ok=True)
    plugin = plugin_dir / "plugin.py"
    plugin.write_text(body, encoding="utf-8")
    return plugin


def test_registry_ui_buckets_and_describe() -> None:
    registry = Registry()
    registry.register_theme("demo", "演示", tokens={"primary": "#112233"})
    registry.register_ui_action("demo.act", "动作", lambda mw: None, section="plugins")
    registry.register_ui_panel("demo.panel", "面板", lambda mw: None)
    registry.register_status_widget(lambda: None)

    info = registry.describe()["ui"]
    assert info["themes"] == ["demo"]
    assert info["actions"] == ["demo.act"]
    assert info["panels"] == ["demo.panel"]
    assert info["status_widgets"] == 1


def test_registry_rejects_duplicates() -> None:
    registry = Registry()
    registry.register_theme("demo", "演示", tokens={"primary": "#112233"})
    with pytest.raises(ValueError, match="重复"):
        registry.register_theme("demo", "另一个", tokens={})
    registry.register_ui_action("a", "A", lambda: None)
    with pytest.raises(ValueError, match="重复"):
        registry.register_ui_action("a", "B", lambda: None)


def test_local_plugin_ui_permissions_auto_approved(tmp_path: Path) -> None:
    plugin = _write_plugin(
        tmp_path / "plug",
        "PLUGIN_METADATA = {'name': 'ui-local', 'permissions': "
        "['ui:theme', 'ui:action', 'ui:panel', 'ui:status']}\n"
        "def register(registry):\n"
        "    registry.register_theme('t1', 'T1', tokens={'primary': '#112233'})\n"
        "    registry.register_ui_action('a1', 'A1', lambda: None)\n"
        "    registry.register_ui_panel('p1', 'P1', lambda mw: None)\n"
        "    registry.register_status_widget(lambda: None)\n",
    )
    registry = Registry()
    load_local_plugins(registry, [str(plugin)], tmp_path)
    assert registry.plugins[0].name == "ui-local"
    assert "t1" in registry.themes
    assert "a1" in registry.ui_actions
    assert "p1" in registry.ui_panels
    assert len(registry.status_widgets) == 1


def test_market_plugin_ui_permissions_require_approval(tmp_path: Path) -> None:
    plugin = _write_plugin(
        tmp_path / MARKET_DIR_NAME / "plug",
        "PLUGIN_METADATA = {'name': 'ui-market', 'permissions': ['ui:theme']}\n"
        "def register(registry):\n"
        "    registry.register_theme('t2', 'T2', tokens={'primary': '#112233'})\n",
    )
    registry = Registry()
    with pytest.raises(PermissionError, match="ui:theme"):
        load_local_plugins(registry, [str(plugin)], tmp_path)
    # 显式批准后放行
    approved = Registry()
    load_local_plugins(approved, [str(plugin)], tmp_path, approved_permissions=("ui:theme",))
    assert "t2" in approved.themes


def test_market_plugin_non_ui_permissions_unchanged(tmp_path: Path) -> None:
    plugin = _write_plugin(
        tmp_path / MARKET_DIR_NAME / "plug",
        "PLUGIN_METADATA = {'name': 'net-market', 'permissions': ['network']}\n"
        "def register(registry): pass\n",
    )
    registry = Registry()
    with pytest.raises(PermissionError, match="network"):
        load_local_plugins(registry, [str(plugin)], tmp_path)
