"""更新检查的自测 —— 已安装插件在市场出现新版本时必须给出提示（§十 P1）。"""

from __future__ import annotations

from pathlib import Path

from omnicrawler.gui.views.plugin_market_logic import (
    _installed_version,
    _update_available,
)

_ENTRY = {"versions": {"0.4.0": {}, "0.5.0": {}}}


def test_update_available_returns_newest() -> None:
    assert _update_available(_ENTRY, "0.4.0") == "0.5.0"


def test_up_to_date_returns_none() -> None:
    assert _update_available(_ENTRY, "0.5.0") is None


def test_newer_than_all_is_not_a_downgrade_hint() -> None:
    assert _update_available(_ENTRY, "9.9.9") is None


def test_version_comparison_is_numeric_not_lexicographic() -> None:
    entry = {"versions": {"0.9.0": {}, "0.10.0": {}}}
    assert _update_available(entry, "0.9.0") == "0.10.0"


def test_empty_inputs_return_none() -> None:
    assert _update_available({}, "0.4.0") is None
    assert _update_available(_ENTRY, "") is None


def test_installed_version_read_statically(tmp_path: Path) -> None:
    (tmp_path / "plugin.py").write_text(
        "PLUGIN_METADATA = {'name': 'x', 'version': '0.4.0', 'permissions': []}\n",
        encoding="utf-8",
    )
    assert _installed_version(tmp_path) == "0.4.0"


def test_installed_version_empty_when_unreadable(tmp_path: Path) -> None:
    assert _installed_version(tmp_path) == ""
