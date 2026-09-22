"""多市场源体验的自测 —— 自定义源必须经校验与确认（§10.2 #4）。

GUI 切换流程的测试在 test_plugin_market_install_flow.py（stub 宿主与夹具在那边）。
"""

from __future__ import annotations

from pathlib import Path

from omnicrawler.gui.views.plugin_market_logic import _validate_market_source


def test_https_source_is_accepted() -> None:
    assert _validate_market_source("https://market.example.com/") is None


def test_local_dir_with_catalog_is_accepted(tmp_path: Path) -> None:
    (tmp_path / "catalog.json").write_text("{}", encoding="utf-8")
    assert _validate_market_source(str(tmp_path)) is None


def test_plain_http_is_rejected() -> None:
    assert _validate_market_source("http://market.example.com/") is not None


def test_empty_and_garbage_are_rejected() -> None:
    assert _validate_market_source("") is not None
    assert _validate_market_source("随便一个字符串") is not None


def test_local_dir_without_catalog_is_rejected(tmp_path: Path) -> None:
    assert _validate_market_source(str(tmp_path)) is not None
