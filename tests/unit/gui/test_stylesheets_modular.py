"""Tests for modular GUI stylesheets package & QSS compilation caching."""

from __future__ import annotations

import importlib.util

import pytest

pytestmark = pytest.mark.skipif(
    importlib.util.find_spec("PySide6") is None,
    reason="Stylesheet tests require PySide6",
)


def test_modular_stylesheet_renders_all_sections_and_caches_by_tokens_and_scale() -> None:
    from omnicrawler.gui.design_system import _cached_stylesheet, stylesheet, theme_tokens

    _cached_stylesheet.cache_clear()
    light = theme_tokens("light")
    dark = theme_tokens("dark")

    qss_light_100 = stylesheet(light, scale=100)
    qss_light_100_again = stylesheet(light, scale=100)
    qss_dark_125 = stylesheet(dark, scale=125)

    assert qss_light_100 is qss_light_100_again
    assert qss_light_100 != qss_dark_125
    for selector in (
        "QMainWindow",
        "QPushButton",
        "QTableView",
        "QListWidget#mainNavigation",
        "QDialog#commandPaletteDialog",
        "QGroupBox",
    ):
        assert selector in qss_light_100
    info = _cached_stylesheet.cache_info()
    assert info.hits >= 1
