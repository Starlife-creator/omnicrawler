"""多市场源体验的自测 —— 自定义源必须经校验与确认（§10.2 #4）。

GUI 切换流程的测试在 test_plugin_market_install_flow.py（stub 宿主与夹具在那边）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

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


class _StubConfig:
    """只提供 ``path`` 的最小配置替身。"""

    def __init__(self, path: Path) -> None:
        self.path = path


class _SourcesHost:
    """只跑 ``_open_sources_dialog`` 的最小宿主（不建完整视图/Qt 事件循环）。"""

    def __init__(self, config_path: Path | None) -> None:
        self._app_config = _StubConfig(config_path) if config_path is not None else None
        self._catalog_sources: list[Any] = []
        self.refresh_calls = 0

    def refresh(self) -> None:
        self.refresh_calls += 1


@pytest.fixture()
def sources_toasts(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """捕获提示文案（``ToastManager.instance()`` 是类方法调用）。"""

    messages: list[str] = []

    class _Recorder:
        def info(self, msg: str) -> None:
            messages.append(msg)

    recorder = _Recorder()
    monkeypatch.setattr(
        "omnicrawler.gui.widgets.toast.ToastManager.instance",
        lambda: recorder,
    )
    return messages


def _forbid_dialog(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(*_a: Any, **_k: Any) -> None:
        raise AssertionError("配置不存在时不应构造索引源对话框")

    monkeypatch.setattr("omnicrawler.gui.views.market_sources_dialog.MarketSourcesDialog", _boom)


def test_missing_config_file_does_not_raise(tmp_path: Path, sources_toasts: list[str], monkeypatch) -> None:
    """配置尚未落盘时点“管理源”只提示，不抛 FileNotFoundError 崩窗口。

    回归：`_open_sources_dialog` 原先只判 ``path is None``，新建项目未保存配置时
    ``config_path`` 非空但文件不存在，对话框与命令层直接读文件 ⇒ 未捕获异常。
    """
    from omnicrawler.gui.views.plugin_market import PluginMarketView

    _forbid_dialog(monkeypatch)
    host = _SourcesHost(tmp_path / "not-created-yet.yaml")

    PluginMarketView._open_sources_dialog(host)  # type: ignore[arg-type]

    assert sources_toasts, "应给出提示而不是崩掉"
    assert host.refresh_calls == 0, "配置不存在时不应刷新"


def test_no_config_object_does_not_raise(sources_toasts: list[str], monkeypatch) -> None:
    """完全没有 ``_app_config`` 时同样只提示。"""
    from omnicrawler.gui.views.plugin_market import PluginMarketView

    _forbid_dialog(monkeypatch)
    host = _SourcesHost(None)

    PluginMarketView._open_sources_dialog(host)  # type: ignore[arg-type]

    assert sources_toasts
