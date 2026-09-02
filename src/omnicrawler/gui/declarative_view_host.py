"""Render the fixed declarative-view vocabulary for isolated plugins."""

from __future__ import annotations

import logging
from typing import Any

from PySide6 import QtCore, QtWidgets

from .i18n import _
from .media_surface import MediaSurfaceService
from .widgets.toast import ToastManager

LOGGER = logging.getLogger(__name__)


class DeclarativeViewController(QtCore.QObject):
    """Own one movable dock and translate widgets into data-only actions."""

    def __init__(self, main_window: Any, plugin_id: str, adapter: Any) -> None:
        super().__init__(main_window)
        self._main_window = main_window
        self._plugin_id = plugin_id
        self._adapter = adapter
        self._descriptor = adapter.describe()
        self._surface = MediaSurfaceService(main_window, plugin_id, self._descriptor["title"])
        adapter.bind_surface(self._surface)
        self.dock = QtWidgets.QDockWidget(self._descriptor["title"], main_window)
        self.dock.setObjectName(
            f"declarativePluginView_{plugin_id}_{self._descriptor['view_id']}"
        )
        self._apply_dock_policy()
        self._rebuild(self._descriptor)
        area = {
            "left": QtCore.Qt.DockWidgetArea.LeftDockWidgetArea,
            "bottom": QtCore.Qt.DockWidgetArea.BottomDockWidgetArea,
        }.get(self._descriptor["preferred_zone"], QtCore.Qt.DockWidgetArea.RightDockWidgetArea)
        main_window.addDockWidget(area, self.dock)

    @property
    def surface(self) -> MediaSurfaceService:
        return self._surface

    def _apply_dock_policy(self) -> None:
        self.dock.setAllowedAreas(
            QtCore.Qt.DockWidgetArea.LeftDockWidgetArea
            | QtCore.Qt.DockWidgetArea.RightDockWidgetArea
            | QtCore.Qt.DockWidgetArea.BottomDockWidgetArea
        )
        features = QtWidgets.QDockWidget.DockWidgetFeature.DockWidgetClosable
        if self._descriptor["movable"]:
            features |= QtWidgets.QDockWidget.DockWidgetFeature.DockWidgetMovable
        if self._descriptor["floatable"]:
            features |= QtWidgets.QDockWidget.DockWidgetFeature.DockWidgetFloatable
        self.dock.setFeatures(features)

    def _rebuild(self, descriptor: dict[str, Any]) -> None:
        self._descriptor = descriptor
        panel = QtWidgets.QWidget(self.dock)
        panel.setMinimumSize(descriptor["minimum_width"], descriptor["minimum_height"])
        panel.resize(descriptor["default_width"], descriptor["default_height"])
        if not descriptor["resizable"]:
            panel.setFixedSize(descriptor["default_width"], descriptor["default_height"])
        layout = QtWidgets.QVBoxLayout(panel)
        for component in descriptor["components"]:
            self._add_component(layout, component)
        layout.addStretch(1)
        old = self.dock.widget()
        self.dock.setWidget(panel)
        if old is not None:
            old.deleteLater()

    def _add_component(self, layout: QtWidgets.QVBoxLayout, item: dict[str, Any]) -> None:
        kind = item["type"]
        if kind == "label":
            label = QtWidgets.QLabel(item.get("text", item.get("label", "")))
            label.setWordWrap(True)
            layout.addWidget(label)
        elif kind == "button":
            button = QtWidgets.QPushButton(item.get("label", item["id"]))
            button.clicked.connect(lambda _checked=False, current=item: self._dispatch(current, {}))
            layout.addWidget(button)
        elif kind == "directory_picker":
            button = QtWidgets.QPushButton(item.get("label", _("选择目录…")))
            button.clicked.connect(
                lambda _checked=False, current=item: self._choose_directory(current)
            )
            layout.addWidget(button)
        elif kind == "slider":
            if item.get("label"):
                layout.addWidget(QtWidgets.QLabel(item["label"]))
            slider = QtWidgets.QSlider(QtCore.Qt.Orientation.Horizontal)
            slider.setRange(item["minimum"], item["maximum"])
            slider.setValue(item["value"])
            slider.sliderReleased.connect(
                lambda current=item, widget=slider: self._dispatch(
                    current, {"value": widget.value()}
                )
            )
            layout.addWidget(slider)
        elif kind == "select":
            if item.get("label"):
                layout.addWidget(QtWidgets.QLabel(item["label"]))
            combo = QtWidgets.QComboBox()
            for option in item["options"]:
                combo.addItem(option["label"], option["value"])
            combo.setCurrentIndex(max(0, combo.findData(item.get("value", ""))))
            combo.activated.connect(
                lambda _index, current=item, widget=combo: self._dispatch(
                    current, {"value": widget.currentData()}
                )
            )
            layout.addWidget(combo)
        elif kind == "resource_list":
            if item.get("label"):
                layout.addWidget(QtWidgets.QLabel(item["label"]))
            listing = QtWidgets.QListWidget()
            for resource in item["items"]:
                row = QtWidgets.QListWidgetItem(resource["label"])
                row.setData(QtCore.Qt.ItemDataRole.UserRole, resource["id"])
                if resource.get("subtitle"):
                    row.setToolTip(resource["subtitle"])
                listing.addItem(row)
            if not item["items"] and item.get("empty_text"):
                listing.addItem(item["empty_text"])
                listing.item(0).setFlags(QtCore.Qt.ItemFlag.NoItemFlags)
            listing.itemActivated.connect(
                lambda row, current=item: self._dispatch(
                    current,
                    {"item_id": str(row.data(QtCore.Qt.ItemDataRole.UserRole) or "")},
                )
            )
            layout.addWidget(listing)

    def _choose_directory(self, item: dict[str, Any]) -> None:
        try:
            if item.get("discovery_kind"):
                handle = self._adapter.discover_directory(
                    item["discovery_kind"], item["discovery_id"]
                )
            else:
                selected = QtWidgets.QFileDialog.getExistingDirectory(
                    self.dock, item.get("directory_label", _("选择资源目录"))
                )
                if not selected:
                    return
                handle = self._adapter.grant_directory(
                    selected, label=item.get("directory_label", "")
                )
            self._dispatch(item, {"resource_handle": handle})
        except Exception as exc:  # noqa: BLE001
            self._report(exc)

    def _dispatch(self, component: dict[str, Any], payload: dict[str, Any]) -> None:
        action = component.get("action", "")
        if not action:
            return
        try:
            response = self._adapter.action(
                action, {"component_id": component["id"], **payload}
            )
            if "view" in response:
                self._rebuild(response["view"])
            message = str(response.get("message", "")).strip()
            if message:
                ToastManager.instance().success(message)
        except Exception as exc:  # noqa: BLE001
            self._report(exc)

    def _report(self, exc: Exception) -> None:
        LOGGER.exception(_("声明式插件视图 %s 操作失败"), self._plugin_id)
        try:
            ToastManager.instance().error(_(f"插件 {self._plugin_id} 操作失败：{exc}"))
        except Exception:  # noqa: BLE001
            pass

    def close(self) -> None:
        """Unmount this view before a plugin reload and release its media surface."""

        self._surface.close()
        self._main_window.removeDockWidget(self.dock)
        self.dock.deleteLater()


def install_declarative_view(
    main_window: Any, plugin_id: str, adapter: Any
) -> DeclarativeViewController:
    return DeclarativeViewController(main_window, plugin_id, adapter)
