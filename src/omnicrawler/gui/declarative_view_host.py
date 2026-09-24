"""Render the fixed declarative-view vocabulary for isolated plugins."""

from __future__ import annotations

import logging
from typing import Any

from PySide6 import QtCore, QtGui, QtWidgets

from .i18n import _
from .media_surface import MediaSurfaceService
from .widgets.toast import ToastManager

LOGGER = logging.getLogger(__name__)


class _ProgressRelay(QtCore.QObject):
    """U6：工作线程 → GUI 线程的进度投递桥。

    broker 的 capability 处理器跑在 pipeline 工作线程；Qt 信号从非 GUI 线程
    emit 时自动排队到接收者所属线程，这是唯一允许的跨线程形态——
    绝不在工作线程直接触碰 QWidget。
    """

    triggered = QtCore.Signal(dict)


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
        # U6（2026-09-24）：绑定运行进度投递桥。限速合并：突发上报只保留最新一帧，
        # 每 300ms 至多刷一次（≤2s 验收间隔的裕量）；面板缺席/已关闭时静默丢弃。
        self._closed = False
        self._pending_progress: dict[str, Any] | None = None
        self._progress_relay = _ProgressRelay(self)
        self._progress_relay.triggered.connect(self._on_progress_queued)
        self._progress_timer = QtCore.QTimer(self)
        self._progress_timer.setSingleShot(True)
        self._progress_timer.setInterval(300)
        self._progress_timer.timeout.connect(self._flush_pending_progress)
        bind_relay = getattr(adapter, "bind_progress_relay", None)
        if callable(bind_relay):
            bind_relay(self._progress_relay.triggered.emit)
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
        # §8.4 #4（P1 长列表）：整面板重建不得丢长列表滚动位置。
        # 销毁旧面板前按组件 id 捕获各列表的滚动位置，新面板建好后恢复。
        saved_scrolls = self._capture_list_scrolls(self.dock.widget())
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
        self._restore_list_scrolls(saved_scrolls)

    def _capture_list_scrolls(self, panel: QtWidgets.QWidget | None) -> dict[str, int]:
        saved: dict[str, int] = {}
        if panel is None:
            return saved
        for listing in panel.findChildren(QtWidgets.QListWidget):
            key = listing.objectName()
            if key:
                saved[key] = listing.verticalScrollBar().value()
        return saved

    def _restore_list_scrolls(self, saved: dict[str, int]) -> None:
        if not saved:
            return
        panel = self.dock.widget()
        if panel is None:
            return
        for listing in panel.findChildren(QtWidgets.QListWidget):
            key = listing.objectName()
            if key in saved:
                # doItemsLayout 确保未显示时滚动范围已就绪，否则 setValue 会被钳到 0
                listing.doItemsLayout()
                listing.verticalScrollBar().setValue(saved[key])

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
        elif kind == "text":
            # 2026-09-24 U4：单行文本输入——值经 view.action 以 {"value": 文本} 上送
            # （与 select 的 value 同通道）。只挂 editingFinished（回车与失焦都会触发）：
            # 若同时挂 returnPressed 会造成同一次回车双重派发、动作被执行两次。
            if item.get("label"):
                layout.addWidget(QtWidgets.QLabel(item["label"]))
            editor = QtWidgets.QLineEdit(str(item.get("value", "")))
            editor.setObjectName(f"declarativeText_{item['id']}")
            editor.setPlaceholderText(str(item.get("placeholder", "")))
            editor.setMaxLength(int(item.get("maxlength", 512)))
            editor.editingFinished.connect(
                lambda current=item, widget=editor: self._dispatch(
                    current, {"value": widget.text()}
                )
            )
            layout.addWidget(editor)
        elif kind == "progress":
            # 2026-09-24 U6：运行进度区——数值不由描述符携带，只由 view.progress
            # 推送刷新（_apply_progress）；描述符仅声明占位区。
            if item.get("label"):
                layout.addWidget(QtWidgets.QLabel(item["label"]))
            bar = QtWidgets.QProgressBar()
            bar.setObjectName(f"declarativeProgress_{item['id']}")
            bar.setRange(0, 1)
            bar.setValue(0)
            bar.setFormat(_("待运行"))
            layout.addWidget(bar)
            status = QtWidgets.QLabel("")
            status.setObjectName(f"declarativeProgressText_{item['id']}")
            status.setWordWrap(True)
            layout.addWidget(status)
        elif kind == "resource_list":
            if item.get("label"):
                layout.addWidget(QtWidgets.QLabel(item["label"]))
            listing = QtWidgets.QListWidget()
            listing.setObjectName(f"declarativeList_{item['id']}")
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

        elif kind == "rich_text":
            # 2026-09-24 P2.1：受限长文本——结构化段纯文本渲染，绝不当 HTML 解释；
            # link 段点击后弹确认框（外链需确认是 §十 10.3 的隔离要求）。
            for segment in item.get("segments", []):
                seg_type = segment["type"]
                if seg_type == "heading":
                    heading = QtWidgets.QLabel(segment["text"])
                    heading.setWordWrap(True)
                    heading.setObjectName("declarativeRichHeading")
                    layout.addWidget(heading)
                elif seg_type == "bullet":
                    bullet = QtWidgets.QLabel("• " + segment["text"])
                    bullet.setWordWrap(True)
                    bullet.setIndent(12)
                    layout.addWidget(bullet)
                elif seg_type == "link":
                    url = segment.get("url", "")
                    link = QtWidgets.QPushButton(segment["text"])
                    link.setObjectName("declarativeRichLink")
                    link.setToolTip(url)
                    link.clicked.connect(
                        lambda _checked=False, target=url, label=segment["text"]:
                        self._open_external_link(target, label)
                    )
                    layout.addWidget(link)
                else:  # paragraph
                    paragraph = QtWidgets.QLabel(segment["text"])
                    paragraph.setWordWrap(True)
                    layout.addWidget(paragraph)

    def _open_external_link(self, url: str, label: str) -> None:
        """外链需确认（§十 10.3）：不可信内容里的 URL 必须用户显式确认才打开。"""
        if not url:
            return
        message = _(
            "要在系统浏览器中打开以下链接吗？\n\n{label}\n{url}\n\n链接来自插件内容，请确认可信。"
        ).format(label=label, url=url)
        choice = QtWidgets.QMessageBox.question(
            self.dock,
            _("打开外部链接"),
            message,
            QtWidgets.QMessageBox.StandardButton.Yes | QtWidgets.QMessageBox.StandardButton.No,
            QtWidgets.QMessageBox.StandardButton.No,
        )
        if choice == QtWidgets.QMessageBox.StandardButton.Yes:
            QtGui.QDesktopServices.openUrl(QtCore.QUrl(url))

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

    def _on_progress_queued(self, data: dict[str, Any]) -> None:
        """进度帧到达（已排队到 GUI 线程）：立即刷最新一帧并开启合并窗口。"""
        if self._closed:
            return
        self._pending_progress = dict(data)
        if self._progress_timer.isActive():
            return  # 合并窗口内：只保留最新一帧，到期统一刷
        self._apply_progress(self._pending_progress)
        self._pending_progress = None
        self._progress_timer.start()

    def _flush_pending_progress(self) -> None:
        if self._closed or self._pending_progress is None:
            return
        data = self._pending_progress
        self._pending_progress = None
        self._apply_progress(data)

    def _apply_progress(self, data: dict[str, Any]) -> None:
        """把一帧进度刷进面板；面板未挂载时静默丢弃（U6 生命周期要求）。"""
        panel = self.dock.widget()
        if panel is None:
            return
        try:
            done = int(data.get("done", 0))
            total = int(data.get("total", 0))
        except (TypeError, ValueError):
            return
        total = max(total, 0)
        done = min(max(done, 0), total if total else 0)
        summary_bits: list[str] = []
        success, failed = data.get("success"), data.get("failed")
        if isinstance(success, int) or isinstance(failed, int):
            summary_bits.append(
                _("成功 {done} · 失败 {failed}").format(done=int(success or 0), failed=int(failed or 0))
            )
        current = str(data.get("current_doi") or "").strip()
        if current:
            summary_bits.append(current)
        summary = " · ".join(summary_bits)
        for bar in panel.findChildren(QtWidgets.QProgressBar):
            if not bar.objectName().startswith("declarativeProgress_"):
                continue
            if total > 0:
                bar.setRange(0, total)
                bar.setValue(done)
                bar.setFormat("%v / %m")
            else:
                bar.setRange(0, 0)  # total 未知 → 忙碌态
        for status in panel.findChildren(QtWidgets.QLabel):
            if status.objectName().startswith("declarativeProgressText_"):
                status.setText(summary)

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

        self._closed = True
        self._progress_timer.stop()
        self._pending_progress = None
        self._surface.close()
        self._main_window.removeDockWidget(self.dock)
        self.dock.deleteLater()


def install_declarative_view(
    main_window: Any, plugin_id: str, adapter: Any
) -> DeclarativeViewController:
    return DeclarativeViewController(main_window, plugin_id, adapter)
