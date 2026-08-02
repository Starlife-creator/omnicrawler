from __future__ import annotations

from pathlib import Path

import yaml
from PyQt6.QtCore import QPoint, QRect, Qt, pyqtSignal
from PyQt6.QtGui import QImage, QMouseEvent, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ...pipeline_ops.pdf_region import make_region_rule


class RegionCanvas(QLabel):
    selected = pyqtSignal(tuple)

    def __init__(self) -> None:
        super().__init__()
        self._start: QPoint | None = None
        self._rect = QRect()
        self.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)

    def mousePressEvent(self, event: QMouseEvent | None) -> None:
        if event is None:
            return
        if event.button() == Qt.MouseButton.LeftButton:
            self._start = event.position().toPoint()
            self._rect = QRect(self._start, self._start)
            self.update()

    def mouseMoveEvent(self, event: QMouseEvent | None) -> None:
        if event is None:
            return
        if self._start is not None:
            self._rect = QRect(self._start, event.position().toPoint()).normalized()
            self.update()

    def mouseReleaseEvent(self, event: QMouseEvent | None) -> None:
        if event is None:
            return
        if self._start is not None:
            self._rect = QRect(self._start, event.position().toPoint()).normalized()
            self._start = None
            self.selected.emit((self._rect.left(), self._rect.top(), self._rect.right(), self._rect.bottom()))
            self.update()

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        if not self._rect.isNull():
            painter = QPainter(self)
            painter.setPen(QPen(Qt.GlobalColor.red, 2))
            painter.drawRect(self._rect)


class PdfRegionSelectorDialog(QDialog):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("PDF 页面框选字段")
        self.resize(1000, 760)
        self._pdf: Path | None = None
        self._page_width = 1.0
        self._page_height = 1.0
        self._image_width = 1
        self._image_height = 1
        self._selected_rect: tuple[int, int, int, int] | None = None
        layout = QVBoxLayout(self)
        controls = QHBoxLayout()
        open_button = QPushButton("选择 PDF")
        open_button.clicked.connect(self._open_pdf)
        controls.addWidget(open_button)
        self._path_label = QLabel("未选择文件")
        controls.addWidget(self._path_label, 1)
        self._page = QSpinBox()
        self._page.setMinimum(1)
        self._page.valueChanged.connect(self._render)
        controls.addWidget(QLabel("页码"))
        controls.addWidget(self._page)
        layout.addLayout(controls)
        form = QFormLayout()
        self._field_name = QLineEdit("field")
        form.addRow("字段名称", self._field_name)
        layout.addLayout(form)
        self._canvas = RegionCanvas()
        self._canvas.selected.connect(self._region_selected)
        scroll = QScrollArea()
        scroll.setWidget(self._canvas)
        scroll.setWidgetResizable(False)
        layout.addWidget(scroll, 1)
        self._preview = QLabel("拖动鼠标框选字段区域后显示文字预览。")
        self._preview.setWordWrap(True)
        layout.addWidget(self._preview)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Close)
        save_button = buttons.button(QDialogButtonBox.StandardButton.Save)
        assert save_button is not None
        save_button.setText("保存区域规则")
        buttons.accepted.connect(self._save_rule)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _open_pdf(self) -> None:
        filename, _ = QFileDialog.getOpenFileName(self, "选择 PDF", "", "PDF 文件 (*.pdf)")
        if not filename:
            return
        try:
            import fitz
            with fitz.open(filename) as document:
                count = document.page_count
        except Exception as exc:
            QMessageBox.warning(self, "无法打开 PDF", str(exc))
            return
        self._pdf = Path(filename)
        self._path_label.setText(filename)
        self._page.setMaximum(max(1, count))
        self._page.setValue(1)
        self._render()

    def _render(self) -> None:
        if self._pdf is None:
            return
        import fitz
        with fitz.open(self._pdf) as document:
            page = document.load_page(self._page.value() - 1)
            pix = page.get_pixmap(matrix=fitz.Matrix(1.5, 1.5), alpha=False)
            self._page_width, self._page_height = page.rect.width, page.rect.height
            self._image_width, self._image_height = pix.width, pix.height
            image = QImage(pix.samples, pix.width, pix.height, pix.stride, QImage.Format.Format_RGB888).copy()
        self._canvas.setPixmap(QPixmap.fromImage(image))
        self._canvas.resize(image.size())

    def _pdf_rect(self, rect: tuple[int, int, int, int]) -> tuple[float, float, float, float]:
        sx = self._page_width / max(1, self._image_width)
        sy = self._page_height / max(1, self._image_height)
        return tuple(round(value * (sx if index % 2 == 0 else sy), 3) for index, value in enumerate(rect))  # type: ignore[return-value]

    def _region_selected(self, rect: tuple) -> None:
        if self._pdf is None:
            return
        self._selected_rect = tuple(int(value) for value in rect)  # type: ignore[assignment]
        assert self._selected_rect is not None
        try:
            rule = make_region_rule(self._pdf, self._field_name.text(), self._page.value() - 1, self._pdf_rect(self._selected_rect))
        except Exception as exc:
            self._preview.setText(str(exc))
        else:
            self._preview.setText(rule.sample_text or "该区域没有可复制文字，可在 PDF 流程中启用 OCR。")

    def _save_rule(self) -> None:
        if self._pdf is None or self._selected_rect is None:
            QMessageBox.information(self, "提示", "请先选择 PDF 并框选字段区域。")
            return
        rule = make_region_rule(
            self._pdf, self._field_name.text(), self._page.value() - 1, self._pdf_rect(self._selected_rect)
        )
        filename, _ = QFileDialog.getSaveFileName(self, "保存区域规则", "pdf_region_rule.yaml", "YAML (*.yaml)")
        if not filename:
            return
        Path(filename).write_text(
            yaml.safe_dump({"pdf_region_fields": [rule.to_dict()]}, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
        QMessageBox.information(self, "保存完成", filename)
