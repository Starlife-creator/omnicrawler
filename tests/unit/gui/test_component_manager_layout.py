"""组件页布局：**一份**列表 + 左右分栏 + 右栏双标签页。

为什么要有这个文件：用户两轮实测反馈把它推到了现在的形态。

第一轮：「只有一行，滚动才看得到其余」⇒ 列表被 `setMaximumHeight(160)` 钉死。
第二轮：「不用专门列一个已安装组件」「浏览器内核占到最下面一大长条，太难看」
⇒ 合并成一份列表，内核收进右栏标签页。

本文件钉住这三条，防止以后改回去。
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtWidgets import QApplication, QListWidget, QSplitter, QTabWidget  # noqa: E402

from omnicrawler.gui.views.component_manager import ComponentManagerPanel  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def _panel() -> ComponentManagerPanel:
    return ComponentManagerPanel(None, catalog_url="https://example.invalid/")


def test_there_is_exactly_one_component_list(qapp) -> None:
    """只有**一份**列表 —— 不再并列「已安装组件」。"""
    panel = _panel()
    lists = panel.findChildren(QListWidget)
    assert len(lists) == 1, f"有 {len(lists)} 份列表，应为 1 份：{[w.accessibleName() for w in lists]}"
    panel.close()


def test_installed_component_is_marked_in_that_single_list(qapp) -> None:
    """已装的条目标 ✓ 前缀，而不是挪到另一份列表里。"""
    panel = _panel()
    panel._apply("list", {
        "registry_sha256": "a" * 64,
        "components": [{"name": "ocr-demo", "version": "1.0.0"}],
    })
    assert panel._market_list is not None
    labels = [panel._market_list.item(i).text() for i in range(panel._market_list.count())]
    assert any("✓" in text and "ocr-demo" in text for text in labels), labels
    panel.close()


def test_layout_uses_a_splitter(qapp) -> None:
    """左右分栏（不是竖排堆叠）。"""
    panel = _panel()
    assert panel.findChildren(QSplitter), "组件页没有 QSplitter"
    panel.close()


def test_kernel_panel_is_a_tab_on_the_right_not_a_full_width_strip(qapp) -> None:
    """内核面板在右栏的标签页里 —— 不再横贯整页底部。"""
    panel = _panel()
    tabs = panel.findChildren(QTabWidget)
    assert tabs, "右栏应有标签页容器"
    titles: list[str] = []
    for tab in tabs:
        titles.extend(tab.tabText(i) for i in range(tab.count()))
    assert any("内核" in title for title in titles), titles
    assert any("详情" in title for title in titles), titles
    panel.close()


def test_market_list_is_not_height_capped(qapp) -> None:
    """列表不得再被 `setMaximumHeight` 钉死（旧实现是 160px）。"""
    panel = _panel()
    assert panel._market_list is not None
    assert panel._market_list.maximumHeight() > 1000
    panel.close()


@pytest.mark.parametrize("window_height", [700, 900, 1100])
def test_list_grows_with_the_window(qapp, window_height: int) -> None:
    """列表随窗口增长（按 30px 一行估算行数）。"""
    panel = _panel()
    panel.resize(1200, window_height)
    panel.show()
    qapp.processEvents()
    assert panel._market_list is not None
    rows = panel._market_list.height() // 30
    assert rows >= 4, f"{window_height}px 窗口下只有 {rows} 行"
    panel.close()


def test_no_catalog_hides_only_the_market_controls(qapp) -> None:
    """无 catalog ⇒ 没有搜索框与安装按钮，但列表仍在（承载本地已装）。"""
    panel = ComponentManagerPanel(None)
    assert panel._market_list is not None
    assert panel._market_search is None
    assert panel._market_install is None
    panel.close()
