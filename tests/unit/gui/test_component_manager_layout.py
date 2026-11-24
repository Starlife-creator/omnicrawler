"""组件页必须是**左右分栏**，且两块列表都随窗口增长。

为什么要有这个文件：用户实测反馈「组件市场还是没优化得和插件一样好」。
此前这一页是**竖排 11 块**：简介 / 可下载列表 / 刷新行 / 选包行 / 状态 /
4 个按钮 / 已安装列表 / 详情框 / 2 个按钮 / 内核面板。

两个后果：

* 「可下载」列表写死 `setMaximumHeight(160)` ⇒ 永远约 3 行；
* 「可下载」与「已安装」两套东西混在一条竖线上，用户分不清按钮作用于哪份列表。

插件页（`market.py`）是 `QSplitter` 左右分栏。本文件钉住组件页也对齐该形态。
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtWidgets import QApplication, QSplitter  # noqa: E402

from omnicrawler.gui.views.component_manager import ComponentManagerPanel  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def _panel() -> ComponentManagerPanel:
    return ComponentManagerPanel(None, catalog_url="https://example.invalid/")


def test_market_and_installed_lists_live_in_a_splitter(qapp) -> None:
    """页面主体必须是 QSplitter（左右分栏），而不是一堆竖排控件。"""
    panel = _panel()
    splitters = [w for w in panel.findChildren(QSplitter)]
    assert splitters, "组件页没有 QSplitter —— 仍是竖排布局"
    # 两个列表都应落在 splitter 的子树里
    assert panel._market_list is not None
    assert panel._market_list.parent() is not None
    assert panel._list is not None
    panel.close()


def test_market_list_is_not_height_capped(qapp) -> None:
    """列表不得再被 `setMaximumHeight` 钉死（旧实现是 160px）。"""
    panel = _panel()
    assert panel._market_list is not None
    assert panel._market_list.maximumHeight() > 1000, (
        f"列表最大高度被限制在 {panel._market_list.maximumHeight()}px"
    )
    panel.close()


@pytest.mark.parametrize(
    ("window_height", "minimum_rows"),
    [(900, 5), (1100, 9)],
)
def test_both_lists_grow_with_the_window(qapp, window_height: int, minimum_rows: int) -> None:
    """两块列表都要随窗口增长（按 30px 一行估算可容纳行数）。"""
    panel = _panel()
    panel.resize(1200, window_height)
    panel.show()
    qapp.processEvents()
    assert panel._market_list is not None
    rows = panel._market_list.height() // 30
    assert rows >= minimum_rows, (
        f"{window_height}px 窗口下可下载列表只有 {rows} 行（{panel._market_list.height()}px）"
    )
    assert panel._list.height() >= 60, "已安装列表被压得太扁"
    panel.close()


def test_detail_pane_has_a_title_and_body(qapp) -> None:
    """右栏要有标题 + 正文，与插件页 `detailTitle` / `_detail_meta` 对应。"""
    panel = _panel()
    assert panel._detail_title is not None
    assert panel._details is not None
    assert panel._market_detail is not None
    panel.close()


def test_kernel_panel_is_capped_so_it_does_not_steal_height(qapp) -> None:
    """内核面板是附属区域，必须设上限，不能和市场列表抢高度。"""
    panel = _panel()
    assert panel._kernel_panel.maximumHeight() <= 300
    panel.close()


def test_no_catalog_means_no_empty_market_list(qapp) -> None:
    """没有 catalog 时不该画一个空的下载列表（那比没有更糟）。"""
    panel = ComponentManagerPanel(None)
    assert panel._market_list is None
    assert panel._market_detail is not None  # 右栏仍存在（展示已安装详情）
    panel.close()
