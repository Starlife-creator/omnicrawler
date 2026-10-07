"""Local PDF reading-order review with owned asynchronous parsing."""
from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QCloseEvent, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QSplitter,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ...document_ir.base import DocumentIR
from ...document_ir.pdf_layout import confirm_table_continuations, validate_columns
from ..i18n import _


class PdfLayoutWorker(QThread):
    ready = Signal(object, bytes)
    failed = Signal(str)

    def __init__(self, path: Path, options: dict[str, Any], page: int, parent: QWidget) -> None:
        super().__init__(parent)
        self.path, self.options, self.page = path, options, page

    def run(self) -> None:
        try:
            from ...document_ir import parse_document
            from ...pdfx.parser import render_page

            document = parse_document(self.path, self.options)
            if not self.isInterruptionRequested():
                image = render_page(str(self.path), self.page, dpi=72)
                if not self.isInterruptionRequested():
                    self.ready.emit(document, image)
        except Exception as exc:
            if not self.isInterruptionRequested():
                self.failed.emit(str(exc))


class PdfLayoutReviewDialog(QDialog):
    def __init__(self, path: Path, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(_("PDF 版式预览与校正"))
        self.setAccessibleName(_("PDF 版式预览与校正"))
        self.resize(1050, 760)
        self.path = path
        self._worker: PdfLayoutWorker | None = None
        self._closing = False
        self._document: DocumentIR | None = None
        self._signature: tuple[str, bool, int] | None = None
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(_("自动分栏是建议。核对阅读顺序后可修改分隔线；续表仅合并勾选项，原 PDF 保留。")))
        controls = QHBoxLayout()
        self.page = QSpinBox()
        self.page.setRange(1, 200)
        self.page.setAccessibleName(_("预览页码"))
        controls.addWidget(QLabel(_("页码")))
        controls.addWidget(self.page)
        self.automatic = QCheckBox(_("建议分栏"))
        self.automatic.setChecked(True)
        controls.addWidget(self.automatic)
        self.columns = QLineEdit()
        self.columns.setPlaceholderText(_("分隔线 PDF 点坐标，如 300；留空单栏"))
        self.columns.setAccessibleName(_("手动分栏坐标"))
        self.columns.setEnabled(False)
        self.automatic.toggled.connect(lambda checked: self.columns.setEnabled(not checked))
        controls.addWidget(self.columns, 1)
        self.preview = QPushButton(_("更新预览"))
        self.preview.clicked.connect(self._load)
        controls.addWidget(self.preview)
        self.export = QPushButton(_("导出校正结果"))
        self.export.setEnabled(False)
        self.export.clicked.connect(self._export)
        controls.addWidget(self.export)
        layout.addLayout(controls)
        self.status = QLabel(str(path))
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        split = QSplitter()
        self.image = QLabel()
        self.image.setAccessibleName(_("原始 PDF 与分栏线"))
        scroll = QScrollArea()
        scroll.setWidget(self.image)
        split.addWidget(scroll)
        self.text = QTextEdit()
        self.text.setReadOnly(True)
        self.text.setAccessibleName(_("校正后的阅读顺序"))
        split.addWidget(self.text)
        layout.addWidget(split, 1)
        self.tables = QListWidget()
        self.tables.setAccessibleName(_("确认需要合并的续表"))
        self.tables.setMaximumHeight(100)
        self.tables.itemChanged.connect(self._refresh_text)
        layout.addWidget(self.tables)
        for signal in (self.columns.textChanged, self.automatic.toggled, self.page.valueChanged):
            signal.connect(self._invalidate)

    def _invalidate(self, *_args: Any) -> None:
        self.export.setEnabled(False)

    def _current_signature(self) -> tuple[str, bool, int]:
        return self.columns.text().strip(), self.automatic.isChecked(), self.page.value()

    def _load(self) -> None:
        if self._worker is not None:
            return
        try:
            columns = validate_columns([float(value.strip()) for value in self.columns.text().split(",") if value.strip()])
            options = {"auto_columns": True} if self.automatic.isChecked() else {"column_boundaries": columns}
        except ValueError as exc:
            self.status.setText(str(exc))
            return
        self._document = None
        self.export.setEnabled(False)
        self.preview.setEnabled(False)
        self._signature = self._current_signature()
        self._worker = PdfLayoutWorker(self.path, options, self.page.value(), self)
        self._worker.ready.connect(self._ready)
        self._worker.failed.connect(self.status.setText)
        self._worker.finished.connect(self._finished)
        self._worker.start()

    def _ready(self, document: DocumentIR, image: bytes) -> None:
        if self._closing or self._signature != self._current_signature():
            self.status.setText(_("选项已变化，请更新预览。"))
            return
        self._document = document
        pixmap = QPixmap()
        pixmap.loadFromData(image)
        painter = QPainter(pixmap)
        painter.setPen(QPen(Qt.GlobalColor.red, 2))
        boundaries = document.metadata.get("inferred_columns", {}).get(str(self.page.value()), document.metadata.get("column_boundaries", []))
        for boundary in boundaries:
            painter.drawLine(int(boundary), 0, int(boundary), pixmap.height())
        painter.end()
        self.image.setPixmap(pixmap)
        self.image.resize(pixmap.size())
        self.tables.clear()
        for candidate in document.metadata.get("table_continuation_candidates", []):
            item = QListWidgetItem(_("合并表 {0} → {1}（页 {2}）").format(candidate["before_table"] + 1, candidate["after_table"] + 1, candidate["pages"]))
            item.setData(Qt.ItemDataRole.UserRole, (candidate["before_table"], candidate["after_table"]))
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Unchecked)
            self.tables.addItem(item)
        self.status.setText("\n".join(document.warnings) or _("预览完成，请核对后导出。"))
        self._refresh_text()
        self.export.setEnabled(True)

    def reviewed_document(self) -> DocumentIR:
        if self._document is None or self._signature != self._current_signature():
            raise ValueError(_("请先更新预览。"))
        pairs = [tuple(self.tables.item(index).data(Qt.ItemDataRole.UserRole)) for index in range(self.tables.count())
                 if self.tables.item(index).checkState() == Qt.CheckState.Checked]
        return confirm_table_continuations(self._document, pairs)

    def _refresh_text(self, *_args: Any) -> None:
        if self._document is not None:
            try:
                self.text.setPlainText(self.reviewed_document().to_markdown())
            except ValueError as exc:
                self.status.setText(str(exc))
                self.export.setEnabled(False)

    def _export(self) -> None:
        directory = QFileDialog.getExistingDirectory(self, _("选择校正结果目录"))
        if not directory:
            return
        try:
            document = self.reviewed_document()
            target = Path(directory) / (self.path.stem + "-reviewed")
            target.mkdir(exist_ok=False)
            (target / "document.md").write_text(document.to_markdown(), encoding="utf-8")
            payload = asdict(document)
            payload["source"] = str(document.source)
            (target / "document.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            self.status.setText(_("已导出：{0}").format(target))
        except (ValueError, OSError) as exc:
            QMessageBox.warning(self, _("导出未完成"), str(exc))

    def _finished(self) -> None:
        worker, self._worker = self._worker, None
        if worker is not None:
            worker.deleteLater()
        self.preview.setEnabled(True)
        if self._closing:
            self.close()

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        if self._worker is not None:
            self._closing = True
            self._worker.requestInterruption()
            event.ignore()
            return
        super().closeEvent(event)

    def reject(self) -> None:
        if self._worker is not None:
            self.close()
        else:
            super().reject()
