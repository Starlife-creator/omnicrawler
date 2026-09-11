"""PdfWorkbenchView 的「拖放与扫描」域 —— 由 pdf_workbench.py 抽出的 Mixin（P1-3 第二批）。

涵盖文件/目录进入工作台的全部入口：
- ``_browse_dir`` / ``_add_files``：对话框选取目录与文件
- ``_stage_dropped_files`` / ``dragEnterEvent`` / ``dragMoveEvent`` / ``dropEvent``：
  拖放暂存（tests/gui/test_pdf_workbench_dragdrop.py 直调 ``_stage_dropped_files``）
- ``_scan_directory`` / ``_apply_scan_result`` / ``_apply_scan_error``：后台扫描与结果上屏
- ``_format_size``：文件大小格式化

以 Mixin 形式保留 ``self`` 语义，宿主属性与全部调用点不变。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import Slot
from PySide6.QtGui import QDragEnterEvent, QDragMoveEvent, QDropEvent
from PySide6.QtWidgets import QFileDialog, QListWidgetItem

from ..i18n import _
from ..widgets.toast import ToastManager

if TYPE_CHECKING:
    from PySide6.QtWidgets import (
        QLabel,
        QLineEdit,
        QListWidget,
        QPushButton,
        QVBoxLayout,
        QWidget,
    )

    # 类型检查期把宿主视作 QWidget；运行期仍为 object，不改变 PdfWorkbenchView 的 MRO。
    _Base = QWidget
else:
    _Base = object


class PdfScanMixin(_Base):
    """拖放与扫描域：文件入口、拖放暂存与后台扫描。"""

    # ---- 宿主契约：实例属性 ----
    _state: str
    _pdf_files: list[Path]
    _root: QVBoxLayout
    _dir_input: QLineEdit
    _scan_btn: QPushButton
    _scan_status: QLabel
    _file_list: QListWidget
    _file_count_label: QLabel
    _execute_btn: QPushButton
    @Slot()
    def _browse_dir(self) -> None:
        path = QFileDialog.getExistingDirectory(self, _("选择 PDF 目录"))
        if path:
            self._dir_input.setText(path)
            self._scan_directory()

    @Slot()
    def _add_files(self) -> None:
        """通过文件对话框多选 PDF 文件，暂存后扫描（与拖入文件共用归集逻辑）。"""
        paths, _selected_filter = QFileDialog.getOpenFileNames(
            self,
            _("选择 PDF 文件"),
            "",
            _("PDF 文件 (*.pdf)"),
        )
        if not paths:
            return
        staging = self._stage_dropped_files([Path(p) for p in paths])
        if staging is None:
            return
        self._dir_input.setText(str(staging))
        toast = ToastManager.instance()
        toast.success(_(f"已暂存 {len(paths)} 个 PDF 文件，开始扫描"))
        self._scan_directory()

    def _stage_dropped_files(self, files: list[Path]) -> Path | None:
        """将拖入/添加的 PDF 归集到持久化暂存目录，返回该目录路径。

        - 优先硬链接（同盘零成本，不复制大文件内容）
        - 跨盘或权限不足时回退 shutil.copy2
        - 每次调用前清空旧内容，避免上次拖入残留干扰本次扫描
        - 同名冲突自动加序号后缀
        """
        import shutil

        from ...core.runtime_paths import portable_data_root

        staging = portable_data_root() / ".omnicrawler" / "pdf-workbench" / "dropped"
        try:
            if staging.exists():
                shutil.rmtree(staging, ignore_errors=True)
            staging.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            toast = ToastManager.instance()
            toast.error(_(f"无法创建暂存目录: {exc}"))
            return None

        for src in files:
            dst = staging / src.name
            if dst.exists():
                # 同名冲突：加序号 (1)、(2)...
                stem, suffix = dst.stem, dst.suffix
                idx = 1
                while dst.exists():
                    dst = staging / f"{stem} ({idx}){suffix}"
                    idx += 1
            try:
                os.link(src, dst)  # 优先硬链接
            except OSError:
                try:
                    shutil.copy2(src, dst)
                except OSError as exc:
                    # 单个文件失败不阻断整体，仅提示
                    toast = ToastManager.instance()
                    toast.warning(_(f"无法暂存 {src.name}: {exc}"))
                    continue
        return staging

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:  # noqa: N802 — Qt 命名
        """接受含 PDF 文件或目录的拖放。"""
        if event.mimeData().hasUrls():
            for url in event.mimeData().urls():
                if not url.isLocalFile():
                    continue
                path = Path(url.toLocalFile())
                if path.is_file() and path.suffix.lower() == ".pdf":
                    event.acceptProposedAction()
                    return
                if path.is_dir():
                    event.acceptProposedAction()
                    return
        event.ignore()

    def dragMoveEvent(self, event: QDragMoveEvent) -> None:  # noqa: N802 — Qt 命名
        """dragEnter 已校验类型，此处统一放行以维持拖放视觉反馈。"""
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event: QDropEvent) -> None:  # noqa: N802 — Qt 命名
        """拖入目录 → 直接扫描；拖入 PDF 文件 → 暂存后扫描。"""
        pdf_files: list[Path] = []
        dir_dropped: Path | None = None
        for url in event.mimeData().urls():
            if not url.isLocalFile():
                continue
            path = Path(url.toLocalFile())
            if path.is_file() and path.suffix.lower() == ".pdf":
                pdf_files.append(path)
            elif path.is_dir() and dir_dropped is None:
                dir_dropped = path

        if dir_dropped is not None:
            self._dir_input.setText(str(dir_dropped))
            event.acceptProposedAction()
            self._scan_directory()
            return

        if pdf_files:
            staging = self._stage_dropped_files(pdf_files)
            if staging is not None:
                self._dir_input.setText(str(staging))
                toast = ToastManager.instance()
                toast.success(_(f"已暂存 {len(pdf_files)} 个 PDF 文件，开始扫描"))
                self._scan_directory()
            event.acceptProposedAction()
            return

        event.ignore()

    @Slot()
    def _scan_directory(self) -> None:
        dir_path = self._dir_input.text().strip()
        if not dir_path:
            self._scan_status.setText(_("请先选择或输入目录路径"))
            return

        p = Path(dir_path).expanduser()
        if not p.is_dir():
            self._scan_status.setText(_(f"目录不存在: {dir_path}"))
            return

        self._state = "scanning"
        self._scan_btn.setEnabled(False)
        self._scan_status.setText(_("正在扫描..."))

        # S3.1.1：目录扫描移入后台线程（大目录 rglob 不冻结界面）
        from ..core.background_worker import BackgroundWorker, run_worker

        class _ScanWorker(BackgroundWorker):
            def __init__(self, root: Path, parent: QWidget | None = None) -> None:
                super().__init__(parent)
                self._root = root

            def work(self) -> list[Path]:
                return sorted(self._root.rglob("*.pdf"))

        run_worker(
            _ScanWorker(p),
            on_succeeded=self._apply_scan_result,
            on_failed=lambda error: self._apply_scan_error(error),
        )

    def _apply_scan_result(self, pdfs: list[Path]) -> None:
        self._pdf_files = pdfs
        self._file_list.clear()

        total_size = 0
        for pdf in pdfs:
            try:
                size = pdf.stat().st_size
            except OSError:
                size = 0
            total_size += size
            size_str = self._format_size(size)
            item = QListWidgetItem(f"  {pdf.name}  ({size_str})")
            item.setToolTip(str(pdf))
            self._file_list.addItem(item)

        count = len(pdfs)
        size_str = self._format_size(total_size)
        self._file_count_label.setText(_(f"共 {count} 个 PDF 文件，总计 {size_str}"))

        if count == 0:
            self._scan_status.setText(_("未找到 PDF 文件，请检查目录路径"))
            self._execute_btn.setEnabled(False)
            self._state = "idle"
        else:
            self._scan_status.setText(_(f"扫描完成 — {count} 个 PDF 文件已就绪"))
            self._execute_btn.setEnabled(True)
            self._state = "ready"
        self._scan_btn.setEnabled(True)

    def _apply_scan_error(self, error: str) -> None:
        self._scan_status.setText(_(f"扫描失败: {error}"))
        self._state = "idle"
        self._scan_btn.setEnabled(True)

    @staticmethod
    def _format_size(size: int) -> str:
        if size < 1024:
            return f"{size} B"
        elif size < 1024 * 1024:
            return f"{size / 1024:.1f} KB"
        elif size < 1024 * 1024 * 1024:
            return f"{size / (1024 * 1024):.1f} MB"
        else:
            return f"{size / (1024 * 1024 * 1024):.2f} GB"
