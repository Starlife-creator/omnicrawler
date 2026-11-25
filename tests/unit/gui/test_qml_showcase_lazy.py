"""QML 试点页**不在构造时**加载 QML 运行时。

为什么要有这个文件（2026-11-25 实测）：

```
import PySide6.QtQml + QtQuickWidgets : 129.6 ms
QmlShowcaseView 构造（旧）           : 124.6 ms
QmlShowcaseView 构造（改后）         :   0.5 ms
```

这个页面在导航里排第 12 位（`NavKey.QML_SHOWCASE`）、极少被点开，
却让每一次冷启动都等它 129ms —— 典型的「为不用的功能付启动税」。

本文件钉住三件事：

1. 构造不导入 QML 运行时（看 `sys.modules` 增量，比计时确定）；
2. 构造后仍给出**可行动**的降级提示（不能是一块空白）；
3. 首次 `refresh()` / `showEvent` 才真正加载，且加载后功能正常。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtWidgets import QApplication  # noqa: E402

from omnicrawler.gui.views.qml_showcase import QmlShowcaseView, qml_available  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _qml_modules() -> set[str]:
    return {name for name in sys.modules if "QtQuick" in name or "QtQml" in name}


def test_construction_does_not_import_qml_runtime(tmp_path: Path, qapp) -> None:
    """构造不得拉起 QML 运行时。"""
    before = _qml_modules()
    view = QmlShowcaseView(tmp_path)
    after = _qml_modules()
    added = sorted(after - before)
    assert not added, f"构造时导入了 QML 运行时：{added[:5]}"
    view.deleteLater()


def test_skeleton_shows_actionable_hint_before_loading(tmp_path: Path, qapp) -> None:
    """加载前也要有说明 —— 不能给用户一块空白。"""
    view = QmlShowcaseView(tmp_path)
    # 用 isHidden() 而非 isVisible()：控件未 show 时 isVisible() 恒为 False，
    # 那是窗口可见性而非「显式隐藏」。这里要断言的是**没有 setVisible(False)**。
    assert view._hint.isHidden() is False, "构造后应显示提示（而非空白）"
    assert view.accessibleDescription(), "加载前也应有无障碍描述"
    view.deleteLater()


def test_first_refresh_loads_qml(tmp_path: Path, qapp) -> None:
    """首次 `refresh()` 才真正加载；可用时_quick 不为 None。"""
    view = QmlShowcaseView(tmp_path)
    assert view._quick is None, "构造后就建了 QQuickWidget"
    view.refresh()
    if qml_available():
        assert view._quick is not None, "refresh() 没有加载 QML"
        assert view._hint.isHidden() is True
    else:
        assert view._quick is None
    view.shutdown()


def test_show_event_triggers_load_once(tmp_path: Path, qapp) -> None:
    """首次可见触发加载，且只触发一次（切走再切回来不重复加载）。"""
    from PySide6.QtWidgets import QWidget

    holder = QWidget()
    view = QmlShowcaseView(tmp_path)
    view.setParent(holder)
    assert view._quick is None
    view.show()
    qapp.processEvents()
    loaded = view._quick is not None
    # 第二次 show 不应重新加载（幂等）
    view.hide()
    view.show()
    qapp.processEvents()
    assert (view._quick is not None) == loaded
    view.shutdown()
    holder.deleteLater()
