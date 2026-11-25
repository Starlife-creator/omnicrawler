"""组件页首次可见时**自动拉取市场目录**（与插件页一致）。

为什么要有这个文件：插件页 `plugin_market_catalog.py:75` 的 `showEvent` 会
`refresh()` 自动拉目录；而组件页此前 `showEvent` 只列已安装组件，
**市场目录不会自动拉** —— 用户必须自己点「刷新」才知道市场里有什么。

两页不一致，且对用户不友好（打开一页看到空列表，以为没有组件）。
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtWidgets import QApplication  # noqa: E402

from omnicrawler.gui.views.component_manager import ComponentManagerPanel  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def test_first_show_also_loads_the_market_catalog(qapp, monkeypatch) -> None:
    """首次 showEvent 除了列已安装，还要拉市场目录。"""
    calls: list[str] = []
    monkeypatch.setattr(
        ComponentManagerPanel, "_reload_catalog", lambda self: calls.append("catalog")
    )
    monkeypatch.setattr(
        ComponentManagerPanel, "refresh", lambda self: calls.append("installed")
    )
    panel = ComponentManagerPanel(None, catalog_url="https://example.invalid/")
    panel.show()
    qapp.processEvents()
    assert "installed" in calls, "首次显示没有列已安装组件"
    assert "catalog" in calls, "首次显示没有自动拉市场目录（与插件页不一致）"
    panel.close()


def test_second_show_does_not_reload_again(qapp, monkeypatch) -> None:
    """只自动拉一次 —— 用户切走再切回来不该重新联网。"""
    calls: list[str] = []
    monkeypatch.setattr(
        ComponentManagerPanel, "_reload_catalog", lambda self: calls.append("catalog")
    )
    monkeypatch.setattr(
        ComponentManagerPanel, "refresh", lambda self: calls.append("installed")
    )
    panel = ComponentManagerPanel(None, catalog_url="https://example.invalid/")
    panel.show()
    qapp.processEvents()
    first = calls.count("catalog")
    panel.hide()
    panel.show()
    qapp.processEvents()
    assert calls.count("catalog") == first, "重复显示又拉了一次目录"
    panel.close()


def test_no_catalog_url_means_no_catalog_fetch(qapp, monkeypatch) -> None:
    """没有 catalog_url 时不该发起市场拉取。"""
    calls: list[str] = []
    monkeypatch.setattr(
        ComponentManagerPanel, "_reload_catalog", lambda self: calls.append("catalog")
    )
    monkeypatch.setattr(
        ComponentManagerPanel, "refresh", lambda self: calls.append("installed")
    )
    panel = ComponentManagerPanel(None)
    panel.show()
    qapp.processEvents()
    assert "installed" in calls
    assert not calls.count("catalog"), "无 catalog_url 却拉了市场目录"
    panel.close()


def test_catalog_reload_runs_off_the_gui_thread(qapp) -> None:
    """拉目录走后台 worker —— 它含网络 IO（超时 30s），不能在 GUI 线程做。"""
    panel = ComponentManagerPanel(None, catalog_url="https://example.invalid/")
    panel._reload_catalog()
    assert panel._worker is not None, "`_reload_catalog` 未启动 worker（仍在同步执行）"
    assert panel._worker.isRunning() or panel._worker.isFinished()
    if panel._worker.isRunning():
        panel._worker.wait(15000)
    qapp.processEvents()
    panel.close()
