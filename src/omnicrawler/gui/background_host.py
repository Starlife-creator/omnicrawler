"""Host-owned declarative local-media backgrounds for trusted UI plugins."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PySide6 import QtCore, QtGui, QtMultimedia, QtMultimediaWidgets, QtWidgets

from .i18n import _

IMAGE_EXTENSIONS = frozenset({".bmp", ".gif", ".jpeg", ".jpg", ".png", ".webp"})
VIDEO_EXTENSIONS = frozenset({".avi", ".m4v", ".mkv", ".mov", ".mp4", ".webm"})
SUPPORTED_EXTENSIONS = IMAGE_EXTENSIONS | VIDEO_EXTENSIONS
MAX_LIBRARY_ITEMS = 500
MAX_SCANNED_ENTRIES = 5000


@dataclass(frozen=True, slots=True)
class LocalMedia:
    path: Path
    kind: str


def discover_local_media(root: str | Path) -> list[LocalMedia]:
    """Scan one user-selected tree with fixed host limits and no symlink traversal."""

    selected = Path(root).expanduser()
    if not selected.is_dir() or selected.is_symlink():
        return []
    found: list[LocalMedia] = []
    examined = 0
    try:
        for directory, names, filenames in os.walk(selected, followlinks=False):
            names[:] = sorted(name for name in names if not (Path(directory) / name).is_symlink())
            for filename in sorted(filenames):
                examined += 1
                if examined > MAX_SCANNED_ENTRIES or len(found) >= MAX_LIBRARY_ITEMS:
                    return sorted(found, key=lambda item: str(item.path).casefold())
                candidate = Path(directory) / filename
                try:
                    if candidate.is_symlink() or not candidate.is_file():
                        continue
                except OSError:
                    continue
                suffix = candidate.suffix.casefold()
                if suffix in SUPPORTED_EXTENSIONS:
                    found.append(
                        LocalMedia(candidate.resolve(), "video" if suffix in VIDEO_EXTENSIONS else "image")
                    )
    except OSError:
        return []
    return sorted(found, key=lambda item: str(item.path).casefold())


class _BackgroundLayer(QtWidgets.QWidget):
    def __init__(self, owner: QtWidgets.QWidget) -> None:
        super().__init__(owner)
        self.pixmap: QtGui.QPixmap | None = None
        self.opacity = 0.24
        self.fit_mode = "cover"
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:
        super().paintEvent(event)
        if self.pixmap is None or self.pixmap.isNull():
            return
        aspect = {
            "stretch": QtCore.Qt.AspectRatioMode.IgnoreAspectRatio,
            "contain": QtCore.Qt.AspectRatioMode.KeepAspectRatio,
        }.get(self.fit_mode, QtCore.Qt.AspectRatioMode.KeepAspectRatioByExpanding)
        scaled = self.pixmap.scaled(
            self.size(), aspect, QtCore.Qt.TransformationMode.SmoothTransformation
        )
        point = QtCore.QPoint(
            (self.width() - scaled.width()) // 2,
            (self.height() - scaled.height()) // 2,
        )
        painter = QtGui.QPainter(self)
        painter.setOpacity(self.opacity)
        painter.drawPixmap(point, scaled)


class BackgroundController(QtCore.QObject):
    """One host-controlled renderer and preference namespace per declaration."""

    def __init__(self, main_window: Any, registration: Any) -> None:
        super().__init__(main_window)
        self.main_window = main_window
        self.registration = registration
        self.items: list[LocalMedia] = []
        self.index = -1
        self.active = False
        self.paused = False
        self.settings = QtCore.QSettings(
            "Starlife", f"OmniCrawler-Background-{registration.background_id}"
        )
        self.layer = _BackgroundLayer(main_window)
        self.layer.setObjectName(f"pluginBackground_{registration.background_id}")
        self.video = QtMultimediaWidgets.QVideoWidget(self.layer)
        self.video.setAttribute(QtCore.Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.video_effect = QtWidgets.QGraphicsOpacityEffect(self.video)
        self.video.setGraphicsEffect(self.video_effect)
        self.audio = QtMultimedia.QAudioOutput(self.layer)
        self.audio.setMuted(True)
        self.player = QtMultimedia.QMediaPlayer(self.layer)
        self.player.setAudioOutput(self.audio)
        self.player.setVideoOutput(self.video)
        self.player.mediaStatusChanged.connect(self._media_status_changed)
        self.movie: QtGui.QMovie | None = None
        self.scrim = QtWidgets.QWidget(self.layer)
        self.scrim.setAttribute(QtCore.Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.timer = QtCore.QTimer(self.layer)
        self.timer.timeout.connect(self.next)
        main_window.installEventFilter(self)
        self._restore()
        self._sync_geometry()
        self.layer.hide()

    def eventFilter(self, watched: QtCore.QObject, event: QtCore.QEvent) -> bool:
        if watched is self.main_window and event.type() in {
            QtCore.QEvent.Type.Resize,
            QtCore.QEvent.Type.Move,
            QtCore.QEvent.Type.Show,
        }:
            QtCore.QTimer.singleShot(0, self._sync_geometry)
        return False

    def _restore(self) -> None:
        self.layer.opacity = max(
            0.05,
            min(float(str(self.settings.value("opacity", self.registration.default_opacity))), 0.85),
        )
        self.dim = max(
            0.0,
            min(float(str(self.settings.value("dim", self.registration.default_dim))), 0.85),
        )
        self.fit_mode = str(self.settings.value("fit", "cover"))
        if self.fit_mode not in {"cover", "contain", "stretch"}:
            self.fit_mode = "cover"
        self.layer.fit_mode = self.fit_mode
        library = str(self.settings.value("library", ""))
        if library:
            self.set_library(library)
        self._apply_effects()

    def _sync_geometry(self) -> None:
        central = self.main_window.centralWidget()
        if central is None:
            self.layer.setGeometry(self.main_window.rect())
        else:
            top_left = central.mapTo(self.main_window, QtCore.QPoint(0, 0))
            self.layer.setGeometry(top_left.x(), top_left.y(), central.width(), central.height())
        self.video.setGeometry(self.layer.rect())
        self.scrim.setGeometry(self.layer.rect())
        self.scrim.raise_()
        if self.active:
            self.layer.raise_()

    def _apply_effects(self) -> None:
        self.video_effect.setOpacity(self.layer.opacity)
        self.scrim.setStyleSheet(f"background-color: rgba(0, 0, 0, {round(255 * self.dim)});")

    def set_library(self, root: str | Path) -> int:
        self.items = discover_local_media(root)
        self.index = 0 if self.items else -1
        if self.items:
            self.settings.setValue("library", str(Path(root).resolve()))
        return len(self.items)

    def set_media(self, path: str | Path) -> None:
        """Display one host-resolved media file without persisting its parent path."""

        candidate = Path(path)
        if candidate.is_symlink() or not candidate.is_file():
            raise ValueError("背景媒体必须是存在的真实文件")
        suffix = candidate.suffix.casefold()
        if suffix not in SUPPORTED_EXTENSIONS:
            raise ValueError(f"背景表面不支持此媒体格式: {suffix or '(无扩展名)'}")
        self.items = [
            LocalMedia(
                candidate.resolve(),
                "video" if suffix in VIDEO_EXTENSIONS else "image",
            )
        ]
        self.index = 0
        if not self.enable():
            raise ValueError("背景媒体无法启用")

    def set_rendered_image(self, png: bytes) -> None:
        """Display host-rendered image bytes without exposing a filesystem path."""

        pixmap = QtGui.QPixmap()
        if not png or not pixmap.loadFromData(png, "PNG"):
            raise ValueError("宿主渲染结果不是有效 PNG")
        current = getattr(self.main_window, "_active_plugin_background", None)
        if current is not None and current is not self:
            current.disable()
        self.main_window._active_plugin_background = self
        self.items = []
        self.index = -1
        self.active = True
        self.player.stop()
        self.video.hide()
        if self.movie is not None:
            self.movie.stop()
            self.movie = None
        self.layer.pixmap = pixmap
        self.layer.show()
        self._sync_geometry()
        self.layer.update()

    def set_opacity(self, value: int) -> None:
        self.layer.opacity = max(0.05, min(value / 100, 0.85))
        self.settings.setValue("opacity", self.layer.opacity)
        self._apply_effects()
        self.layer.update()

    def set_dim(self, value: int) -> None:
        self.dim = max(0.0, min(value / 100, 0.85))
        self.settings.setValue("dim", self.dim)
        self._apply_effects()

    def set_fit(self, mode: str) -> None:
        if mode not in {"cover", "contain", "stretch"}:
            return
        self.fit_mode = mode
        self.layer.fit_mode = mode
        aspect = {
            "stretch": QtCore.Qt.AspectRatioMode.IgnoreAspectRatio,
            "contain": QtCore.Qt.AspectRatioMode.KeepAspectRatio,
        }.get(mode, QtCore.Qt.AspectRatioMode.KeepAspectRatioByExpanding)
        self.video.setAspectRatioMode(aspect)
        self.settings.setValue("fit", mode)
        self.layer.update()

    def set_rotation(self, seconds: int) -> None:
        bounded = max(0, min(int(seconds), 3600))
        self.settings.setValue("rotation_seconds", bounded)
        if bounded and self.active:
            self.timer.start(bounded * 1000)
        else:
            self.timer.stop()

    def enable(self) -> bool:
        if not self.items:
            return False
        current = getattr(self.main_window, "_active_plugin_background", None)
        if current is not None and current is not self:
            current.disable()
        self.main_window._active_plugin_background = self
        self.active = True
        self.layer.show()
        self._sync_geometry()
        self.show_index(max(0, self.index))
        self.set_rotation(int(str(self.settings.value("rotation_seconds", 0))))
        return True

    def disable(self) -> None:
        self.active = False
        self.timer.stop()
        self.player.stop()
        if self.movie is not None:
            self.movie.stop()
        self.video.hide()
        self.layer.hide()
        if getattr(self.main_window, "_active_plugin_background", None) is self:
            self.main_window._active_plugin_background = None

    def toggle(self) -> bool:
        if self.active:
            self.disable()
            return False
        return self.enable()

    def show_index(self, index: int) -> None:
        if not self.items:
            return
        self.index = index % len(self.items)
        item = self.items[self.index]
        self.player.stop()
        if self.movie is not None:
            self.movie.stop()
            self.movie = None
        if item.kind == "video":
            self.layer.pixmap = None
            self.video.show()
            self.player.setSource(QtCore.QUrl.fromLocalFile(str(item.path)))
            if not self.paused:
                self.player.play()
        else:
            self.video.hide()
            if item.path.suffix.casefold() == ".gif":
                self.movie = QtGui.QMovie(str(item.path))
                self.movie.frameChanged.connect(
                    lambda _frame: self._show_movie_frame()
                )
                self.movie.start()
            else:
                self.layer.pixmap = QtGui.QPixmap(str(item.path))
            self.layer.update()

    def _show_movie_frame(self) -> None:
        if self.movie is not None:
            self.layer.pixmap = self.movie.currentPixmap()
            self.layer.update()

    def next(self) -> None:
        if self.items:
            self.show_index(self.index + 1)

    def previous(self) -> None:
        if self.items:
            self.show_index(self.index - 1)

    def set_paused(self, paused: bool) -> None:
        self.paused = paused
        if paused:
            self.player.pause()
        elif self.active and not self.player.source().isEmpty():
            self.player.play()

    def _media_status_changed(self, status: Any) -> None:
        if status == QtMultimedia.QMediaPlayer.MediaStatus.EndOfMedia and self.active:
            self.next()

    def make_panel(self) -> QtWidgets.QWidget:
        panel = QtWidgets.QWidget(self.main_window)
        layout = QtWidgets.QVBoxLayout(panel)
        note = QtWidgets.QLabel(_("仅渲染你明确选择的本地图片、GIF 与视频；网页和脚本不会执行。"))
        note.setWordWrap(True)
        status = QtWidgets.QLabel(_("尚未选择媒体目录"))
        status.setWordWrap(True)
        choose = QtWidgets.QPushButton(_("选择媒体目录…"))
        toggle = QtWidgets.QPushButton(_("启用背景"))
        row = QtWidgets.QHBoxLayout()
        previous = QtWidgets.QPushButton(_("上一个"))
        following = QtWidgets.QPushButton(_("下一个"))
        row.addWidget(previous)
        row.addWidget(following)
        opacity = QtWidgets.QSlider(QtCore.Qt.Orientation.Horizontal)
        opacity.setRange(5, 85)
        opacity.setValue(round(self.layer.opacity * 100))
        dim = QtWidgets.QSlider(QtCore.Qt.Orientation.Horizontal)
        dim.setRange(0, 85)
        dim.setValue(round(self.dim * 100))
        fit = QtWidgets.QComboBox()
        for label, value in (
            (_("覆盖裁剪"), "cover"),
            (_("完整包含"), "contain"),
            (_("拉伸"), "stretch"),
        ):
            fit.addItem(label, value)
        fit.setCurrentIndex(max(0, fit.findData(self.fit_mode)))
        rotation = QtWidgets.QSpinBox()
        rotation.setRange(0, 3600)
        rotation.setSuffix(_(" 秒"))
        rotation.setSpecialValueText(_("不轮播"))
        rotation.setValue(int(str(self.settings.value("rotation_seconds", 0))))
        pause = QtWidgets.QCheckBox(_("暂停视频"))
        for widget in (note, status, choose, toggle):
            layout.addWidget(widget)
        layout.addLayout(row)
        layout.addWidget(pause)
        for label, setting_widget in (
            (_("背景可见度"), opacity),
            (_("暗色遮罩"), dim),
            (_("适配方式"), fit),
            (_("自动轮播"), rotation),
        ):
            layout.addWidget(QtWidgets.QLabel(label))
            layout.addWidget(setting_widget)
        layout.addStretch(1)

        def select_library() -> None:
            selected = QtWidgets.QFileDialog.getExistingDirectory(panel, _("选择本地媒体目录"))
            if selected:
                count = self.set_library(selected)
                status.setText(
                    _(f"已发现 {count} 个受支持媒体文件")
                    if count
                    else _("没有受支持媒体")
                )

        def toggle_background() -> None:
            enabled = self.toggle()
            toggle.setText(_("停用背景") if enabled else _("启用背景"))

        choose.clicked.connect(select_library)
        toggle.clicked.connect(toggle_background)
        previous.clicked.connect(self.previous)
        following.clicked.connect(self.next)
        pause.toggled.connect(self.set_paused)
        opacity.valueChanged.connect(self.set_opacity)
        dim.valueChanged.connect(self.set_dim)
        fit.currentIndexChanged.connect(lambda _index: self.set_fit(str(fit.currentData())))
        rotation.valueChanged.connect(self.set_rotation)
        return panel


def install_background(main_window: Any, registration: Any) -> BackgroundController:
    """Mount standard host controls for one declarative registration."""

    controller = BackgroundController(main_window, registration)
    dock = QtWidgets.QDockWidget(registration.label, main_window)
    dock.setObjectName(f"pluginBackgroundPanel_{registration.background_id}")
    dock.setAllowedAreas(
        QtCore.Qt.DockWidgetArea.LeftDockWidgetArea
        | QtCore.Qt.DockWidgetArea.RightDockWidgetArea
    )
    dock.setWidget(controller.make_panel())
    main_window.addDockWidget(QtCore.Qt.DockWidgetArea.RightDockWidgetArea, dock)
    status = QtWidgets.QLabel(_(f"  {registration.label} · 本地宿主模式  "))
    status.setToolTip(_("无网络、无网页脚本、无插件自定义绘制层"))
    main_window.statusBar().addPermanentWidget(status)
    return controller
