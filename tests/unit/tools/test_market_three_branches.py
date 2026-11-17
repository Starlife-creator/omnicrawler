"""市场是「OmniCrawler 市场」，下辖**插件 / 模板 / 组件**三个分支。

## 这条守卫防什么

2026-11-18 维护者指出两处名实不符：

1. 视图文件叫 `plugin_market.py`，而它的 `MarketView` 是**三分支容器**
   （插件 / 模板 / 组件），名字只覆盖了三分之一；
2. 用户可见文案写「插件市场」，让模板与组件入口显得无处可寻。

已改为 `market.py` + `MarketView` + 「OmniCrawler 市场」。
本文件把这两条钉住，防止后续改动退回旧名。

## 判据

* 容器模块名与类名不得再含 `plugin_market` / `PluginMarketView`；
* 导航标签必须是「OmniCrawler 市场」（含 emoji 前缀亦可）；
* 容器内确有**三个**标签页，且分别是插件 / 模板 / 组件。
"""

from __future__ import annotations

from pathlib import Path

import pytest

_VIEWS = Path(__file__).resolve().parents[3] / "src" / "omnicrawler" / "gui" / "views"


def test_market_container_module_is_not_plugin_specific() -> None:
    """容器模块必须叫 `market.py`，不能叫 `plugin_market.py`。"""
    assert (_VIEWS / "market.py").is_file(), (
        "容器模块应为 market.py —— 它是插件/模板/组件三分支的容器，"
        "叫 plugin_market 只覆盖三分之一"
    )
    assert not (_VIEWS / "plugin_market.py").exists(), (
        "plugin_market.py 不应再存在（已重命名为 market.py）"
    )


def test_market_container_class_is_not_plugin_specific() -> None:
    """容器类必须叫 `MarketView`。"""
    source = (_VIEWS / "market.py").read_text(encoding="utf-8")
    assert "class MarketView(" in source, "容器类应为 MarketView"
    assert "class PluginMarketView(" not in source, (
        "PluginMarketView 不应再存在（已重命名为 MarketView）"
    )


def test_plugin_branch_modules_keep_their_names() -> None:
    """六个 `plugin_market_*` 子模块**应当保留**旧名。

    它们确实只服务插件分支（浏览/目录/安装/动作/逻辑/worker），
    改名反而是无意义的历史掩盖——与 `pdfx`/`convertx` 同一判断。
    """
    for suffix in ("browse", "catalog", "install", "actions", "logic", "workers"):
        path = _VIEWS / f"plugin_market_{suffix}.py"
        assert path.is_file(), (
            f"{path.name} 应保留 —— 它只服务插件分支，名字准确"
        )


def test_nav_label_says_omnicrawler_market() -> None:
    """导航标签必须是「OmniCrawler 市场」，不是「插件市场」。"""
    from omnicrawler.gui import navigation
    from omnicrawler.gui.delegates import central_area

    label = central_area._NAV_LABELS[navigation.NavKey.PLUGIN_MARKET]
    assert "OmniCrawler 市场" in label, (
        f"导航标签是 {label!r}，应为「OmniCrawler 市场」——"
        "否则模板与组件入口看起来无处可寻"
    )
    assert "插件市场" not in label, f"导航标签仍含旧名：{label!r}"


def test_market_container_has_three_branches() -> None:
    """容器里必须确有插件 / 模板 / 组件三个标签页。"""
    source = (_VIEWS / "market.py").read_text(encoding="utf-8")

    assert "_build_plugin_pane" in source, "缺少插件分支"
    assert "TemplateMarketView" in source, "缺少模板分支"
    assert "ComponentManagerPanel" in source, "缺少组件分支"

    for branch in ('_("插件")', '_("模板")', '_("组件")'):
        assert branch in source, f"标签页缺 {branch}"


@pytest.mark.skipif(
    not ((_VIEWS / "market.py").is_file()), reason="需要 market.py"
)
def test_market_view_instantiates_with_three_tabs() -> None:
    """实例化后 `QTabWidget` 里应有三项（真实 Qt 对象，不只看源码）。"""
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication, QTabWidget

    app = QApplication.instance() or QApplication([])
    assert app is not None

    from omnicrawler.gui.views.market import MarketView

    # 真实签名是 (project_root, parent, app_config)；传 None 走默认探测。
    view = MarketView(project_root=None)
    try:
        tab_widgets = view.findChildren(QTabWidget)
        assert tab_widgets, "MarketView 里没有 QTabWidget"
        # 最内层那个承载三分支
        branch_tabs = [
            w for w in tab_widgets
            if [w.tabText(i) for i in range(w.count())][:3] == ["插件", "模板", "组件"]
        ]
        assert branch_tabs, (
            "没找到含「插件/模板/组件」三项的标签栏；"
            f"实际标签：{[w.tabText(i) for w in tab_widgets for i in range(w.count())]}"
        )
    finally:
        view.deleteLater()
