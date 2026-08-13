"""下载文件列表视图。

显示下载文件夹内的文件，支持双击打开和打开文件夹。
"""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt, QUrl
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..i18n import _
from ..widgets.empty_state import EmptyState


class FileList(QWidget):
    """下载文件列表。

    功能：
    - 列出指定目录下的文件
    - 双击使用系统默认程序打开
    - 打开文件夹按钮
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._directory: Path | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)

        # 信息栏
        info_layout = QHBoxLayout()
        self._info_label = QLabel(_("未加载下载目录"))
        info_layout.addWidget(self._info_label)
        info_layout.addStretch()

        open_folder_btn = QPushButton(_("打开文件夹"))
        open_folder_btn.clicked.connect(self._open_folder)
        info_layout.addWidget(open_folder_btn)

        refresh_btn = QPushButton(_("刷新"))
        refresh_btn.clicked.connect(self.refresh)
        info_layout.addWidget(refresh_btn)

        layout.addLayout(info_layout)

        # 文件列表（P3：空态统一 EmptyState，空态有主 CTA「刷新」）
        self._empty_state = EmptyState(
            icon="📂",
            title=_("暂无下载文件"),
            description=_("任务下载的附件会出现在这里；点击「刷新」重新扫描目录。"),
            action_label=_("刷新"),
            action_callback=self.refresh,
        )
        self._list = QListWidget()
        self._list.setAlternatingRowColors(True)
        self._list.itemDoubleClicked.connect(self._on_item_double_clicked)
        layout.addWidget(self._empty_state)
        layout.addWidget(self._list)

    def set_directory(self, directory: Path) -> None:
        """设置要显示的目录。"""
        self._directory = directory
        self.refresh()

    def refresh(self) -> None:
        """刷新文件列表。"""
        self._list.clear()

        if self._directory is None or not self._directory.is_dir():
            self._info_label.setText(_("未找到下载目录"))
            self._list.hide()
            self._empty_state.show()
            return

        files = sorted(self._directory.iterdir(), key=lambda f: f.stat().st_mtime, reverse=True)
        file_count = 0
        for fp in files:
            if fp.is_file():
                item = QListWidgetItem(fp.name)
                item.setData(Qt.ItemDataRole.UserRole, str(fp))
                item.setToolTip(str(fp))
                self._list.addItem(item)
                file_count += 1

        self._info_label.setText(_("共 {0} 个文件").format(file_count))
        self._list.setVisible(file_count > 0)
        self._empty_state.setVisible(file_count == 0)

    def _on_item_double_clicked(self, item: QListWidgetItem) -> None:
        """双击打开文件。"""
        filepath = item.data(Qt.ItemDataRole.UserRole)
        if filepath:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(filepath)))

    def _open_folder(self) -> None:
        """打开文件夹。"""
        if self._directory and self._directory.is_dir():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._directory)))
