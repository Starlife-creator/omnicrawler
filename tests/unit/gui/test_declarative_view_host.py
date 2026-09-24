"""声明式插件视图宿主的自测 —— 锁定已拍板的 QDockWidget 面板形态与重建状态保持。

§10.2 机制 #3（2026-09-20 已拍板）：插件的 ``view`` 是**面板**（QDockWidget，
左/右/底部、可浮动、可关闭），``view.action`` 返回新描述符触发**整面板重建**。
本文件此前为零覆盖 ⇒ 形态被无意改坏不会有任何判据说话。

§8.4 #4（P1 长列表前置）：整面板重建不得丢 ``resource_list`` 的滚动位置。
"""

from __future__ import annotations

from typing import Any

import pytest
from PySide6 import QtCore, QtWidgets

from omnicrawler.gui import declarative_view_host as dvh_module
from omnicrawler.gui.declarative_view_host import DeclarativeViewController


@pytest.fixture(scope="module")
def qapp() -> QtWidgets.QApplication:
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


class _StubSurface:
    """替身媒体面：宿主形态测试不触碰 QtMultimedia 重型栈。"""

    def __init__(self, *_args: Any, **_kwargs: Any) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


class _StubAdapter:
    def __init__(self, descriptor: dict[str, Any]) -> None:
        self._descriptor = descriptor
        self.bound_surface: Any = None
        self.actions: list[tuple[str, dict[str, Any]]] = []
        self.response: dict[str, Any] = {}

    def describe(self) -> dict[str, Any]:
        return self._descriptor

    def bind_surface(self, surface: Any) -> None:
        self.bound_surface = surface

    def action(self, action: str, payload: dict[str, Any]) -> dict[str, Any]:
        self.actions.append((action, payload))
        return self.response


def _descriptor(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "view_id": "issue-browser",
        "title": "需求清单",
        "preferred_zone": "right",
        "movable": True,
        "resizable": True,
        "floatable": True,
        "default_width": 380,
        "default_height": 640,
        "minimum_width": 240,
        "minimum_height": 160,
        "components": [
            {"type": "button", "id": "refresh", "label": "刷新", "action": "refresh"},
            {
                "type": "resource_list",
                "id": "issues",
                "label": "Issues",
                "items": [{"id": f"i{n}", "label": f"issue {n}"} for n in range(200)],
                "empty_text": "暂无",
            },
        ],
    }
    base.update(overrides)
    return base


def _controller(monkeypatch: pytest.MonkeyPatch, descriptor: dict[str, Any]):
    monkeypatch.setattr(dvh_module, "MediaSurfaceService", _StubSurface)
    main = QtWidgets.QMainWindow()
    adapter = _StubAdapter(descriptor)
    controller = DeclarativeViewController(main, "issue-browser", adapter)
    return main, adapter, controller


def test_view_renders_as_dock_widget_with_policy(
    qapp: QtWidgets.QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """★ 形态锁定（§10.2 #3 已拍板）：面板＝QDockWidget，右坞、可浮动、可关闭。"""
    main, _adapter, controller = _controller(monkeypatch, _descriptor())

    dock = controller.dock
    assert isinstance(dock, QtWidgets.QDockWidget)
    assert main.findChild(QtWidgets.QDockWidget) is dock
    assert main.dockWidgetArea(dock) == QtCore.Qt.DockWidgetArea.RightDockWidgetArea
    assert dock.allowedAreas() == (
        QtCore.Qt.DockWidgetArea.LeftDockWidgetArea
        | QtCore.Qt.DockWidgetArea.RightDockWidgetArea
        | QtCore.Qt.DockWidgetArea.BottomDockWidgetArea
    )
    features = dock.features()
    assert features & QtWidgets.QDockWidget.DockWidgetFeature.DockWidgetClosable
    assert features & QtWidgets.QDockWidget.DockWidgetFeature.DockWidgetMovable
    assert features & QtWidgets.QDockWidget.DockWidgetFeature.DockWidgetFloatable
    main.deleteLater()


def test_immovable_descriptor_drops_movable_and_floatable(
    qapp: QtWidgets.QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    main, _adapter, controller = _controller(
        monkeypatch, _descriptor(movable=False, floatable=False)
    )
    features = controller.dock.features()
    assert features & QtWidgets.QDockWidget.DockWidgetFeature.DockWidgetClosable
    assert not features & QtWidgets.QDockWidget.DockWidgetFeature.DockWidgetMovable
    assert not features & QtWidgets.QDockWidget.DockWidgetFeature.DockWidgetFloatable
    main.deleteLater()


def test_action_returning_new_descriptor_triggers_full_panel_rebuild(
    qapp: QtWidgets.QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """★ 机制锁定：``view.action`` 返回新描述符 ⇒ 整面板重建（旧面板被替换销毁）。"""
    main, adapter, controller = _controller(monkeypatch, _descriptor())
    old_panel = controller.dock.widget()
    adapter.response = {
        "view": _descriptor(
            components=[
                {"type": "label", "id": "hint", "text": "重建后的说明"},
            ]
        )
    }

    button = old_panel.findChildren(QtWidgets.QPushButton)[0]
    button.click()

    assert adapter.actions == [("refresh", {"component_id": "refresh"})]
    new_panel = controller.dock.widget()
    assert new_panel is not old_panel
    labels = [w.text() for w in new_panel.findChildren(QtWidgets.QLabel)]
    assert any("重建后的说明" in text for text in labels)
    main.deleteLater()


def test_rebuild_preserves_list_scroll_position(
    qapp: QtWidgets.QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """★ §8.4 #4 回归：整面板重建后 resource_list 滚动位置不得归零。"""
    main, adapter, controller = _controller(monkeypatch, _descriptor())
    listing = controller.dock.widget().findChildren(QtWidgets.QListWidget)[0]
    listing.doItemsLayout()
    assert listing.verticalScrollBar().maximum() > 0  # 前置：列表确实可滚动
    listing.verticalScrollBar().setValue(
        listing.verticalScrollBar().maximum() // 2
    )
    saved = listing.verticalScrollBar().value()
    assert saved > 0
    adapter.response = {"view": _descriptor()}

    controller.dock.widget().findChildren(QtWidgets.QPushButton)[0].click()

    new_listing = controller.dock.widget().findChildren(QtWidgets.QListWidget)[0]
    assert new_listing is not listing
    assert new_listing.objectName() == "declarativeList_issues"
    assert new_listing.verticalScrollBar().value() == saved
    main.deleteLater()


def test_rich_text_renders_plain_text_and_confirms_links(
    qapp: QtWidgets.QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """P2.1（2026-09-24）：rich_text 段纯文本渲染；外链点击必须确认，拒绝则不开。

    反向断言：拒绝确认后 openUrl 不得被调用（外链需确认是 §十 10.3 隔离要求）。
    """
    descriptor = _descriptor(components=[
        {
            "type": "rich_text", "id": "body",
            "segments": [
                {"type": "heading", "text": "标题段"},
                {"type": "paragraph", "text": "正文段"},
                {"type": "bullet", "text": "条目段"},
                {"type": "link", "text": "项目主页", "url": "https://example.com"},
            ],
        },
    ])
    main, _adapter, controller = _controller(monkeypatch, descriptor)

    dock = controller.dock
    texts = [w.text() for w in dock.findChildren(QtWidgets.QLabel)]
    assert "标题段" in texts
    assert any(t.startswith("• ") and "条目段" in t for t in texts)
    links = dock.findChildren(QtWidgets.QPushButton, "declarativeRichLink")
    assert len(links) == 1 and links[0].text() == "项目主页"

    opened: list[str] = []
    monkeypatch.setattr(
        dvh_module.QtGui.QDesktopServices, "openUrl",
        lambda url: opened.append(url.toString()) or True,
    )
    # 用户拒绝（默认按钮 = No）⇒ 不打开
    monkeypatch.setattr(
        dvh_module.QtWidgets.QMessageBox, "question",
        lambda *args, **kwargs: QtWidgets.QMessageBox.StandardButton.No,
    )
    links[0].click()
    assert opened == []
    # 用户确认 ⇒ 打开且 URL 原样
    monkeypatch.setattr(
        dvh_module.QtWidgets.QMessageBox, "question",
        lambda *args, **kwargs: QtWidgets.QMessageBox.StandardButton.Yes,
    )
    links[0].click()
    assert opened == ["https://example.com"]
    main.deleteLater()
