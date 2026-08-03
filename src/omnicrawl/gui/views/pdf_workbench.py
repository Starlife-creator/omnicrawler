"""PDF 工作台 — 扫描、解析、OCR、抽取一条龙。

Phase 2 落地：选目录 → 扫描 PDF → 选模板 → 异步执行全流程。
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any

from PyQt6.QtCore import Qt, QThread, pyqtSignal, pyqtSlot
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QProgressBar,
    QPushButton,
    QSplitter,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ..design_system import FONT_FAMILY_MONO, FONT_SIZE, RADIUS, ThemeManager
from ..i18n import _
from ..widgets.toast import ToastManager


# ── 工作线程 ──────────────────────────────────────────────────────
class _PdfPipelineWorker(QThread):
    """后台线程：执行 PDF 处理流水线，通过信号报告进度。"""

    stage_started = pyqtSignal(str)       # 阶段名（中文）
    stage_finished = pyqtSignal(str, object)  # 阶段名, 结果 dict
    progress = pyqtSignal(int)            # 0-100
    all_done = pyqtSignal(object)         # 全部结果 dict
    failed = pyqtSignal(str)              # 错误消息

    def __init__(
        self,
        config_path: str,
        *,
        run_ocr: bool = True,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._config_path = config_path
        self._run_ocr = run_ocr
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    def run(self) -> None:
        try:
            from omnicrawl.pdfx.service import run_extraction

            stage_names: dict[str, str] = {
                "ingest_started": "扫描 PDF 文件",
                "parse_started": "解析文字层",
                "ocr_started": "OCR 识别",
                "text_export_started": "导出文本",
                "extract_started": "结构化抽取",
                "export_started": "导出 Excel/CSV",
            }
            stage_order = [
                "ingest", "parse", "ocr", "text_export",
                "extract", "export",
            ]
            total_stages = len(stage_order)

            def _callback(stage: str, result: dict[str, Any]) -> None:
                if self._cancelled:
                    return
                name = stage_names.get(stage, stage)
                if stage.endswith("_started"):
                    self.stage_started.emit(name)
                elif stage in stage_order:
                    self.stage_finished.emit(name, result)
                    idx = stage_order.index(stage) + 1
                    self.progress.emit(int(idx / total_stages * 100))

            def _should_stop() -> bool:
                return self._cancelled

            result = run_extraction(
                self._config_path,
                auto_prepare=True,
                run_ocr=self._run_ocr,
                callback=_callback,
                should_stop=_should_stop,
            )

            if self._cancelled:
                return

            self.all_done.emit(result)

        except Exception as exc:
            import traceback
            self.failed.emit(f"{exc}\n{traceback.format_exc()}")


# ── PDF 模板定义 ────────────────────────────────────────────────
_PDF_TEMPLATES: list[dict[str, str]] = [
    {
        "id": "builtin:pdf/generic_template.yaml",
        "name": "泛用 PDF 模板",
        "desc": "通用字段抽取模板，适合合同、年报、论文等",
    },
    {
        "id": "builtin:pdf/announcement_fields.yaml",
        "name": "公告 PDF 模板",
        "desc": "公告/公示类文档字段抽取，含标题、日期、正文等",
    },
]


# ── 视图 ──────────────────────────────────────────────────────────
class PdfWorkbenchView(QWidget):
    """PDF 批量处理工作台。

    状态机: idle → scanning → ready → running → done
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("pdfWorkbench")
        self.setAccessibleName(_("PDF 工作台"))

        self._state = "idle"          # idle | scanning | ready | running | done
        self._pdf_files: list[Path] = []
        self._worker: _PdfPipelineWorker | None = None
        self._temp_dir: str | None = None

        self._setup_ui()
        self._apply_style()
        ThemeManager.instance().theme_changed.connect(self._apply_style)

    # ── UI 搭建 ────────────────────────────────────────────────
    def _setup_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(16)

        # 标题
        title = QLabel("PDF 工作台")
        title.setObjectName("homeTitle")
        root.addWidget(title)

        subtitle = QLabel("选择 PDF 目录和模板，一键完成扫描 → 解析 → OCR → 抽取 → 导出全流程")
        subtitle.setObjectName("sectionSubtitle")
        subtitle.setWordWrap(True)
        root.addWidget(subtitle)

        # ── 区域 1: 目录选择 ──
        dir_group = QGroupBox("1. 选择 PDF 目录")
        dir_layout = QVBoxLayout(dir_group)
        dir_layout.setSpacing(8)

        dir_row = QHBoxLayout()
        self._dir_input = QLineEdit()
        self._dir_input.setPlaceholderText("例如：D:\\合同文件\\2024")
        self._dir_input.setReadOnly(False)
        dir_row.addWidget(self._dir_input, 1)

        browse_btn = QPushButton("浏览...")
        browse_btn.clicked.connect(self._browse_dir)
        dir_row.addWidget(browse_btn)

        self._scan_btn = QPushButton("扫描 PDF 文件")
        self._scan_btn.setProperty("primary", True)
        self._scan_btn.clicked.connect(self._scan_directory)
        dir_row.addWidget(self._scan_btn)

        dir_layout.addLayout(dir_row)
        self._scan_status = QLabel("")
        self._scan_status.setObjectName("mutedLabel")
        dir_layout.addWidget(self._scan_status)
        root.addWidget(dir_group)

        # ── 区域 2: PDF 列表 + 模板选择 (QSplitter) ──
        content_splitter = QSplitter()
        content_splitter.setOrientation(Qt.Orientation.Horizontal)

        # 左栏: PDF 文件列表
        file_panel = QFrame()
        file_panel.setProperty("card", True)
        file_layout = QVBoxLayout(file_panel)
        file_layout.setContentsMargins(12, 12, 12, 12)

        file_header = QLabel("PDF 文件列表")
        file_header.setObjectName("sectionSubtitle")
        file_layout.addWidget(file_header)

        self._file_list = QListWidget()
        self._file_list.setAlternatingRowColors(True)
        self._file_list.setMinimumWidth(250)
        file_layout.addWidget(self._file_list, 1)

        self._file_count_label = QLabel("")
        self._file_count_label.setObjectName("mutedLabel")
        file_layout.addWidget(self._file_count_label)

        content_splitter.addWidget(file_panel)

        # 右栏: 模板 + 选项
        config_panel = QFrame()
        config_panel.setProperty("card", True)
        config_layout = QVBoxLayout(config_panel)
        config_layout.setContentsMargins(12, 12, 12, 12)

        cfg_header = QLabel("2. 选择处理模板")
        cfg_header.setObjectName("sectionSubtitle")
        config_layout.addWidget(cfg_header)

        self._template_combo = QComboBox()
        for _i, t in enumerate(_PDF_TEMPLATES):
            self._template_combo.addItem(f"{t['name']} — {t['desc']}", t["id"])
        config_layout.addWidget(self._template_combo)

        config_layout.addSpacing(12)

        # OCR 选项
        self._ocr_checkbox = QCheckBox("启用 OCR（扫描件/图片 PDF 建议开启）")
        self._ocr_checkbox.setChecked(True)
        config_layout.addWidget(self._ocr_checkbox)

        config_layout.addSpacing(16)

        # 执行按钮
        btn_row = QHBoxLayout()
        self._execute_btn = QPushButton("▶ 开始处理")
        self._execute_btn.setProperty("primary", True)
        self._execute_btn.setMinimumHeight(36)
        self._execute_btn.clicked.connect(self._execute)
        self._execute_btn.setEnabled(False)
        btn_row.addWidget(self._execute_btn, 1)

        self._cancel_btn = QPushButton("取消")
        self._cancel_btn.clicked.connect(self._cancel)
        self._cancel_btn.setVisible(False)
        btn_row.addWidget(self._cancel_btn)

        config_layout.addLayout(btn_row)

        # 进度
        self._progress_bar = QProgressBar()
        self._progress_bar.setRange(0, 100)
        self._progress_bar.setValue(0)
        self._progress_bar.setVisible(False)
        config_layout.addWidget(self._progress_bar)

        self._stage_label = QLabel("")
        self._stage_label.setObjectName("mutedLabel")
        self._stage_label.setVisible(False)
        config_layout.addWidget(self._stage_label)

        config_layout.addStretch()

        content_splitter.addWidget(config_panel)
        content_splitter.setStretchFactor(0, 3)
        content_splitter.setStretchFactor(1, 2)
        root.addWidget(content_splitter, 1)

        # ── 区域 3: 结果输出 ──
        self._result_group = QGroupBox("处理结果")
        self._result_group.setVisible(False)
        result_layout = QVBoxLayout(self._result_group)

        self._result_text = QTextEdit()
        self._result_text.setReadOnly(True)
        self._result_text.setMaximumHeight(150)
        result_layout.addWidget(self._result_text)

        result_btn_row = QHBoxLayout()
        open_output_btn = QPushButton("打开输出目录")
        open_output_btn.clicked.connect(self._open_output_dir)
        result_btn_row.addWidget(open_output_btn)

        open_excel_btn = QPushButton("打开 Excel")
        open_excel_btn.clicked.connect(self._open_excel)
        result_btn_row.addWidget(open_excel_btn)

        reset_btn = QPushButton("重新开始")
        reset_btn.clicked.connect(self._reset)
        result_btn_row.addWidget(reset_btn)
        result_btn_row.addStretch()
        result_layout.addLayout(result_btn_row)

        root.addWidget(self._result_group)

    # ── 样式 ───────────────────────────────────────────────────
    def _apply_style(self, *_args: Any) -> None:
        t = ThemeManager.instance().tokens
        self.setStyleSheet(f"""
            QLabel#homeTitle {{
                font-size: {FONT_SIZE["heading"]}px;
                font-weight: 700;
                color: {t.text};
            }}
            QLabel#sectionSubtitle {{
                font-size: {FONT_SIZE["body"]}px;
                color: {t.text};
                font-weight: 600;
            }}
            QLabel#mutedLabel {{
                font-size: {FONT_SIZE["small"]}px;
                color: {t.muted};
            }}
            QListWidget {{
                border: 1px solid {t.border};
                border-radius: {RADIUS["sm"]}px;
                padding: 4px;
                background: {t.surface};
            }}
            QListWidget::item {{
                padding: 4px 8px;
                border-radius: 4px;
            }}
            QListWidget::item:selected {{
                background: {t.primary}22;
                color: {t.text};
            }}
            QTextEdit {{
                border: 1px solid {t.border};
                border-radius: {RADIUS["sm"]}px;
                padding: 8px;
                background: {t.surface};
                font-family: {FONT_FAMILY_MONO};
                font-size: {FONT_SIZE["small"]}px;
            }}
        """)

    # ── 目录浏览 ──────────────────────────────────────────────
    @pyqtSlot()
    def _browse_dir(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "选择 PDF 目录")
        if path:
            self._dir_input.setText(path)
            self._scan_directory()

    # ── 扫描目录 ──────────────────────────────────────────────
    @pyqtSlot()
    def _scan_directory(self) -> None:
        dir_path = self._dir_input.text().strip()
        if not dir_path:
            self._scan_status.setText("请先选择或输入目录路径")
            return

        p = Path(dir_path).expanduser()
        if not p.is_dir():
            self._scan_status.setText(f"目录不存在: {dir_path}")
            return

        self._state = "scanning"
        self._scan_btn.setEnabled(False)
        self._scan_status.setText("正在扫描...")
        QApplication.processEvents()  # type: ignore[assignment]

        try:
            pdfs = sorted(p.rglob("*.pdf"))
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
            self._file_count_label.setText(
                f"共 {count} 个 PDF 文件，总计 {size_str}"
            )

            if count == 0:
                self._scan_status.setText("未找到 PDF 文件，请检查目录路径")
                self._execute_btn.setEnabled(False)
                self._state = "idle"
            else:
                self._scan_status.setText(
                    f"扫描完成 — {count} 个 PDF 文件已就绪"
                )
                self._execute_btn.setEnabled(True)
                self._state = "ready"

        except Exception as exc:
            self._scan_status.setText(f"扫描失败: {exc}")
            self._state = "idle"
        finally:
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

    # ── 执行 ───────────────────────────────────────────────────
    @pyqtSlot()
    def _execute(self) -> None:
        if self._state != "ready" or not self._pdf_files:
            return

        template_id = self._template_combo.currentData()
        run_ocr = self._ocr_checkbox.isChecked()
        input_dir = self._dir_input.text().strip()

        self._state = "running"
        self._execute_btn.setVisible(False)
        self._cancel_btn.setVisible(True)
        self._scan_btn.setEnabled(False)
        self._template_combo.setEnabled(False)
        self._ocr_checkbox.setEnabled(False)
        self._result_group.setVisible(False)

        self._progress_bar.setVisible(True)
        self._progress_bar.setValue(0)
        self._stage_label.setVisible(True)
        self._stage_label.setText("正在准备...")

        # 创建临时项目目录
        self._temp_dir = tempfile.mkdtemp(prefix="omnicrawl_pdf_")
        work_dir = os.path.join(self._temp_dir, "work")
        output_dir = os.path.join(self._temp_dir, "output")
        config_path = os.path.join(self._temp_dir, "project.yaml")

        try:
            from omnicrawl.pdfx.project import create_project_config

            create_project_config(
                template_path=template_id,
                destination=config_path,
                project_name="PDF工作台任务",
                input_dir=input_dir,
                work_dir=work_dir,
                output_dir=output_dir,
                ocr_backend="paddle" if run_ocr else "none",
            )
        except Exception as exc:
            self._on_failed(f"创建项目配置失败: {exc}")
            return

        self._worker = _PdfPipelineWorker(
            config_path, run_ocr=run_ocr, parent=self
        )
        self._worker.stage_started.connect(self._on_stage_started)
        self._worker.stage_finished.connect(self._on_stage_finished)
        self._worker.progress.connect(self._progress_bar.setValue)
        self._worker.all_done.connect(self._on_done)
        self._worker.failed.connect(self._on_failed)
        self._worker.start()

    @pyqtSlot(str)
    def _on_stage_started(self, stage: str) -> None:
        self._stage_label.setText(f"⏳ {stage}...")

    @pyqtSlot(str, object)
    def _on_stage_finished(self, stage: str, result: object) -> None:
        _ = result
        self._stage_label.setText(f"✓ {stage} 完成")

    @pyqtSlot(object)
    def _on_done(self, result: object) -> None:
        self._state = "done"
        self._progress_bar.setValue(100)
        self._stage_label.setText("✓ 全部完成！")
        self._cancel_btn.setVisible(False)
        self._execute_btn.setVisible(True)
        self._execute_btn.setText("重新执行")
        self._execute_btn.setEnabled(True)
        self._scan_btn.setEnabled(True)
        self._template_combo.setEnabled(True)
        self._ocr_checkbox.setEnabled(True)

        result_data = result if isinstance(result, dict) else {}
        status = result_data.get("status", {})
        export_info = result_data.get("export", {}) if isinstance(result_data.get("export"), dict) else {}

        lines: list[str] = []
        lines.append("=== PDF 处理完成 ===")
        docs = status.get("documents", {}) if isinstance(status, dict) else {}
        pages = status.get("pages", {}) if isinstance(status, dict) else {}
        records = status.get("records", {}) if isinstance(status, dict) else {}

        lines.append(f"文档: {docs}")
        lines.append(f"页面: 共 {pages.get('total', '?')} 页, OCR {pages.get('ocr_done', '?')} 页")
        lines.append(f"记录: 共 {records.get('total', '?')} 条, 需复核 {records.get('needs_review', '?')} 条")

        output_files = export_info.get("files", []) if isinstance(export_info, dict) else []
        if output_files:
            lines.append(f"\n输出文件 ({len(output_files)} 个):")
            for f in output_files:
                lines.append(f"  📄 {f}")

        self._result_text.setText("\n".join(lines))
        self._result_group.setVisible(True)

        # Toast
        toast = ToastManager.instance()
        toast.success(f"PDF 处理完成！共处理 {docs.get('ingested', '?')} 份文档")

    @pyqtSlot(str)
    def _on_failed(self, msg: str) -> None:
        self._state = "idle"
        self._progress_bar.setVisible(False)
        self._stage_label.setVisible(False)
        self._cancel_btn.setVisible(False)
        self._execute_btn.setVisible(True)
        self._execute_btn.setText("▶ 开始处理")
        self._execute_btn.setEnabled(True)
        self._scan_btn.setEnabled(True)
        self._template_combo.setEnabled(True)
        self._ocr_checkbox.setEnabled(True)

        self._result_text.setText(f"处理失败:\n{msg}")
        self._result_group.setVisible(True)

        toast = ToastManager.instance()
        toast.error(f"PDF 处理失败: {msg.split(chr(10))[0]}")

    # ── 取消 ───────────────────────────────────────────────────
    @pyqtSlot()
    def _cancel(self) -> None:
        if self._worker and self._worker.isRunning():
            self._worker.cancel()
            self._stage_label.setText("正在取消...")

    # ── 打开结果 ──────────────────────────────────────────────
    @pyqtSlot()
    def _open_output_dir(self) -> None:
        if self._temp_dir:
            output = os.path.join(self._temp_dir, "output")
            if os.path.isdir(output):
                os.startfile(output)  # type: ignore[attr-defined]
                return
        toast = ToastManager.instance()
        toast.warning("输出目录不存在或已被清理")

    @pyqtSlot()
    def _open_excel(self) -> None:
        if not self._temp_dir:
            return
        import glob
        output = os.path.join(self._temp_dir, "output")
        xlsx_files = glob.glob(os.path.join(output, "*.xlsx"))
        csv_files = glob.glob(os.path.join(output, "*.csv"))
        files = xlsx_files + csv_files
        if files:
            os.startfile(files[0])  # type: ignore[attr-defined]
        else:
            toast = ToastManager.instance()
            toast.warning("未找到 Excel/CSV 输出文件")

    # ── 重置 ───────────────────────────────────────────────────
    @pyqtSlot()
    def _reset(self) -> None:
        self._state = "idle"
        self._result_group.setVisible(False)
        self._progress_bar.setVisible(False)
        self._progress_bar.setValue(0)
        self._stage_label.setVisible(False)
        self._stage_label.setText("")
        self._execute_btn.setText("▶ 开始处理")
        self._execute_btn.setEnabled(bool(self._pdf_files))
        self._scan_btn.setEnabled(True)
        self._template_combo.setEnabled(True)
        self._ocr_checkbox.setEnabled(True)
