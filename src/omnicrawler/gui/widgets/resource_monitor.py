"""资源监控组件。

显示子进程内存占用和磁盘剩余空间。
若 psutil 未安装，自动隐藏，不报错。
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QHBoxLayout, QLabel, QWidget

from ..design_system import FONT_SIZE, ThemeManager
from ..i18n import _

try:
    import psutil
    _PSUTIL_AVAILABLE = True
except ImportError:
    _PSUTIL_AVAILABLE = False


class ResourceMonitor(QWidget):
    """资源占用指示器。

    显示内存占用和磁盘剩余空间。
    内存 > 2GB 或磁盘 < 500MB 时变红警告。
    若 psutil 未安装，整个组件自动隐藏。
    """

    MEMORY_WARN_THRESHOLD = 2 * 1024 * 1024 * 1024  # 2 GB
    DISK_WARN_THRESHOLD = 500 * 1024 * 1024  # 500 MB

    def __init__(
        self,
        parent: QWidget | None = None,
        project_root: Path | None = None,
    ) -> None:
        super().__init__(parent)
        self._project_root = project_root or Path.cwd()
        self._pid: int | None = None

        self.setVisible(_PSUTIL_AVAILABLE)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.setSpacing(12)

        self._mem_label = QLabel(_("内存: --"))
        self._mem_label.setObjectName("resourceLabel")
        layout.addWidget(self._mem_label)

        self._disk_label = QLabel(_("磁盘: --"))
        self._disk_label.setObjectName("resourceLabel")
        layout.addWidget(self._disk_label)

        # 定时刷新
        self._refresh_timer = QTimer(self)
        self._refresh_timer.timeout.connect(self.refresh)
        self._refresh_timer.setInterval(3000)  # 每 3 秒

        # 应用令牌样式并监听主题切换
        self._apply_token_style()
        ThemeManager.instance().theme_changed.connect(self._apply_token_style)

    def _apply_token_style(self, *_args) -> None:
        """从设计令牌生成标签样式，自动跟随主题。"""
        t = ThemeManager.instance().tokens
        normal = f"font-size: {FONT_SIZE['caption']}px; color: {t.muted};"
        self._normal_style = normal
        self._warn_style = (
            f"font-size: {FONT_SIZE['caption']}px; color: {t.danger}; font-weight: bold;"
        )
        self._mem_label.setStyleSheet(normal)
        self._disk_label.setStyleSheet(normal)

    def set_pid(self, pid: int | None) -> None:
        """设置要监控的子进程 PID。"""
        self._pid = pid
        if pid is not None and _PSUTIL_AVAILABLE:
            self._refresh_timer.start()
        else:
            self._refresh_timer.stop()

    def refresh(self) -> None:
        """刷新资源数据显示。"""
        if not _PSUTIL_AVAILABLE:
            return

        # 内存
        if self._pid is not None:
            try:
                proc = psutil.Process(self._pid)
                mem_info = proc.memory_info()
                mem_mb = mem_info.rss / (1024 * 1024)
                if mem_mb >= 1024:
                    self._mem_label.setText(_("内存: {:.1f} GB").format(mem_mb / 1024))
                else:
                    self._mem_label.setText(_("内存: {:.0f} MB").format(mem_mb))

                # 超阈值警告
                if mem_info.rss > self.MEMORY_WARN_THRESHOLD:
                    self._mem_label.setStyleSheet(self._warn_style)
                else:
                    self._mem_label.setStyleSheet(self._normal_style)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                self._mem_label.setText(_("内存: --"))

        # 磁盘
        try:
            import shutil
            usage = shutil.disk_usage(self._project_root)
            free_gb = usage.free / (1024 * 1024 * 1024)
            self._disk_label.setText(_("磁盘: {:.1f} GB 可用").format(free_gb))

            if usage.free < self.DISK_WARN_THRESHOLD:
                self._disk_label.setStyleSheet(self._warn_style)
            else:
                self._disk_label.setStyleSheet(self._normal_style)
        except Exception:
            self._disk_label.setText(_("磁盘: --"))
