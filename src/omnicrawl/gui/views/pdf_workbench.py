"""PDF 工作台 — 扫描、解析、OCR、抽取一条龙。

Phase 2 落地：选目录 → 扫描 PDF → 选模板 → 异步执行全流程。
"""

from __future__ import annotations

import os
import tempfile
import urllib.parse
from pathlib import Path
from typing import Any

from PyQt6.QtCore import Qt, QThread, QUrl, pyqtSignal, pyqtSlot
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import (
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
    QMessageBox,
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
    """后台线程：执行 PDF 处理流水线，通过信号报告进度。

    P2-4：内部由 ProgressTracker 驱动（阶段权重 + 子项展开 + EMA ETA），
    同时发出旧式 pyqtSignal，旧消费者代码无需修改。
    """

    stage_started = pyqtSignal(str)       # 阶段名（中文）
    stage_finished = pyqtSignal(str, object)  # 阶段名, 结果 dict
    warnings_received = pyqtSignal(list)  # D3：运行时警告（如“大模型已启用但 Key 空”）
    progress = pyqtSignal(int)            # 0-100
    all_done = pyqtSignal(object)         # 全部结果 dict
    failed = pyqtSignal(str)              # 错误消息
    document_progress = pyqtSignal(int, int)  # B12：已处理文档数, 总文档数（抽取阶段实时汇报）
    unified_progress = pyqtSignal(object)  # P2-4：TaskProgressEvent（新消费者可直接使用）

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
            from omnicrawl.services.progress import (
                ProgressTracker,
                StageSpec,
                event_to_percent,
                event_to_stage_label,
            )

            stage_names: dict[str, str] = {
                "ingest_started": _("扫描 PDF 文件"),
                "parse_started": _("解析文字层"),
                "ocr_started": _("OCR 识别"),
                "text_export_started": _("导出文本"),
                "extract_started": _("结构化抽取"),
                "export_started": _("导出 Excel/CSV"),
            }
            # P2-4：阶段权重声明（抽取通常是耗时主阶段，分配较大权重）
            tracker = ProgressTracker(
                stages=[
                    StageSpec("ingest",      weight=1.0,  display_name=stage_names["ingest_started"],      has_items=True),
                    StageSpec("parse",       weight=2.0,  display_name=stage_names["parse_started"]),
                    StageSpec("ocr",         weight=3.5,  display_name=stage_names["ocr_started"]),
                    StageSpec("text_export", weight=0.5,  display_name=stage_names["text_export_started"]),
                    StageSpec("extract",     weight=4.0,  display_name=stage_names["extract_started"],      has_items=True),
                    StageSpec("export",      weight=1.0,  display_name=stage_names["export_started"]),
                ],
                on_event=lambda ev: self.unified_progress.emit(ev),
            )
            tracker.start()
            stage_order = ["ingest", "parse", "ocr", "text_export", "extract", "export"]
            active_stage: str = ""

            def _bridge(ev) -> None:
                """把统一事件同时映射到旧式信号，老消费者保持稳定。"""
                self.progress.emit(event_to_percent(ev))
                label = event_to_stage_label(ev)
                if label:
                    # 用 stage_started 承载中文标签（带 ETA、子项），消费方直接显示
                    # 仅当阶段/子项变化时才发，避免刷屏
                    nonlocal active_stage
                    if ev.display_stage and ev.display_stage != active_stage and ev.stage:
                        active_stage = ev.display_stage
                        self.stage_started.emit(ev.display_stage)

            # 统一事件同时桥接到旧式信号
            def _bridge_both(ev) -> None:
                _bridge(ev)
                self.unified_progress.emit(ev)

            tracker._on_event = _bridge_both

            def _callback(stage: str, result: dict[str, Any]) -> None:
                if self._cancelled:
                    return
                name = stage_names.get(stage, stage)
                if stage.endswith("_started"):
                    short = stage[:-8]  # 去掉 _started
                    if short in stage_order:
                        expected = 0
                        if isinstance(result, dict):
                            # ingest 阶段可从结果推断文档数；extract 后续由 _doc_callback 动态给出
                            expected = int(result.get("total", 0) or 0)
                        tracker.begin_stage(short, expected_items=expected)
                    else:
                        tracker.set_percent(tracker.last_event.percent if tracker.last_event else 0, message=name)
                elif stage == "warnings":
                    # D3：关键警告（“大模型已启用但 Key 空”“OCR 未启用”）必须对用户可见
                    items = result.get("items", []) if isinstance(result, dict) else []
                    if items:
                        self.warnings_received.emit(list(items))
                elif stage in stage_order:
                    self.stage_finished.emit(name, result)
                    tracker.end_stage(stage)

            def _should_stop() -> bool:
                return self._cancelled

            def _doc_callback(processed: int, total: int) -> None:
                # B12：抽取阶段每处理完一份文档实时汇报，避免大批量时进度条长期不动误以为卡死
                if self._cancelled:
                    return
                self.document_progress.emit(processed, total)
                tracker.set_item_progress(processed, total)

            result = run_extraction(
                self._config_path,
                auto_prepare=True,
                run_ocr=self._run_ocr,
                callback=_callback,
                should_stop=_should_stop,
                on_document=_doc_callback,
            )

            if self._cancelled:
                # S3.1.6：取消路径统一发 all_done（带 stopped 标志），UI 恢复可操作
                tracker.cancel()
                self.all_done.emit({
                    "status": {"documents": {}, "pages": {}, "records": {}},
                    "stopped": True,
                    "cancelled": True,
                })
                return

            tracker.finish()
            self.all_done.emit(result)

        except Exception as exc:
            import traceback
            try:
                tracker.fail(str(exc))
            except Exception:  # noqa: BLE001 — tracker 失败不得吞没原始异常
                pass
            self.failed.emit(f"{exc}\n{traceback.format_exc()}")


def _collect_failures(result: object) -> list[str]:
    """S2.3.4：递归收集所有阶段的 failed/stopped 标志（含 run_extraction 的 processing 嵌套）。"""
    failures: list[str] = []
    if not isinstance(result, dict):
        return failures
    for key, value in result.items():
        if key == "status":
            continue
        if isinstance(value, dict):
            if value.get("failed"):
                failures.append(str(value.get("error") or _(f"{key} 阶段失败")))
            else:
                failures.extend(_collect_failures(value))
    if result.get("stopped"):
        failures.append(_("管线已停止（用户取消或前序阶段失败）"))
    return failures


# ── PDF 模板定义 ────────────────────────────────────────────────
_PDF_TEMPLATES: list[dict[str, str]] = [
    {
        "id": "builtin:pdf/generic_template.yaml",
        "name": _("泛用 PDF 模板"),
        "desc": _("通用字段抽取模板，适合合同、年报、论文等"),
    },
    {
        "id": "builtin:pdf/announcement_fields.yaml",
        "name": _("公告 PDF 模板"),
        "desc": _("公告/公示类文档字段抽取，含标题、日期、正文等"),
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
        # P1-4：启用拖放，支持拖入 PDF 文件或目录直接进入批量处理流程
        self.setAcceptDrops(True)

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
        title = QLabel(_("PDF 工作台"))
        title.setObjectName("homeTitle")
        root.addWidget(title)

        subtitle = QLabel(_("选择 PDF 目录或拖入 PDF 文件，一键完成扫描 → 解析 → OCR → 抽取 → 导出全流程"))
        subtitle.setObjectName("sectionSubtitle")
        subtitle.setWordWrap(True)
        root.addWidget(subtitle)

        # ── 区域 1: 目录选择 ──
        dir_group = QGroupBox(_("1. 选择 PDF 目录"))
        dir_layout = QVBoxLayout(dir_group)
        dir_layout.setSpacing(8)

        dir_row = QHBoxLayout()
        self._dir_input = QLineEdit()
        self._dir_input.setPlaceholderText(_("例如：D:\\合同文件\\2024"))
        self._dir_input.setReadOnly(False)
        dir_row.addWidget(self._dir_input, 1)

        browse_btn = QPushButton(_("浏览..."))
        browse_btn.clicked.connect(self._browse_dir)
        dir_row.addWidget(browse_btn)

        # P1-4：直接添加 PDF 文件（不限于整目录），与拖放共用暂存逻辑
        add_files_btn = QPushButton(_("添加文件..."))
        add_files_btn.clicked.connect(self._add_files)
        dir_row.addWidget(add_files_btn)

        self._scan_btn = QPushButton(_("扫描 PDF 文件"))
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

        file_header = QLabel(_("PDF 文件列表"))
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

        cfg_header = QLabel(_("2. 选择处理模板"))
        cfg_header.setObjectName("sectionSubtitle")
        config_layout.addWidget(cfg_header)

        self._template_combo = QComboBox()
        for _i, t in enumerate(_PDF_TEMPLATES):
            self._template_combo.addItem(f"{t['name']} — {t['desc']}", t["id"])
        config_layout.addWidget(self._template_combo)

        config_layout.addSpacing(12)

        # OCR 选项
        self._ocr_checkbox = QCheckBox(_("启用 OCR（扫描件/图片 PDF 建议开启）"))
        self._ocr_checkbox.setChecked(True)
        config_layout.addWidget(self._ocr_checkbox)

        config_layout.addSpacing(16)

        # 执行按钮
        btn_row = QHBoxLayout()
        self._execute_btn = QPushButton(_("▶ 开始处理"))
        self._execute_btn.setProperty("primary", True)
        self._execute_btn.setMinimumHeight(36)
        self._execute_btn.clicked.connect(self._execute)
        self._execute_btn.setEnabled(False)
        btn_row.addWidget(self._execute_btn, 1)

        self._cancel_btn = QPushButton(_("取消"))
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
        self._result_group = QGroupBox(_("处理结果"))
        self._result_group.setVisible(False)
        result_layout = QVBoxLayout(self._result_group)

        self._result_text = QTextEdit()
        self._result_text.setReadOnly(True)
        self._result_text.setMaximumHeight(150)
        result_layout.addWidget(self._result_text)

        result_btn_row = QHBoxLayout()
        open_output_btn = QPushButton(_("打开输出目录"))
        open_output_btn.clicked.connect(self._open_output_dir)
        result_btn_row.addWidget(open_output_btn)

        open_excel_btn = QPushButton(_("打开 Excel"))
        open_excel_btn.clicked.connect(self._open_excel)
        result_btn_row.addWidget(open_excel_btn)

        reset_btn = QPushButton(_("重新开始"))
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
        path = QFileDialog.getExistingDirectory(self, _("选择 PDF 目录"))
        if path:
            self._dir_input.setText(path)
            self._scan_directory()

    # ── P1-4：添加文件与拖放 ─────────────────────────────────
    @pyqtSlot()
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

        staging = portable_data_root() / ".omnicrawl" / "pdf-workbench" / "dropped"
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

    # ── 拖放事件 ─────────────────────────────────────────────
    def dragEnterEvent(self, event) -> None:  # noqa: N802 — Qt 命名
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

    def dragMoveEvent(self, event) -> None:  # noqa: N802 — Qt 命名
        """dragEnter 已校验类型，此处统一放行以维持拖放视觉反馈。"""
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event) -> None:  # noqa: N802 — Qt 命名
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

    # ── 扫描目录 ──────────────────────────────────────────────
    @pyqtSlot()
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
            def __init__(self, root: Path, parent=None) -> None:
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

    # ── 执行 ───────────────────────────────────────────────────
    @pyqtSlot()
    def _execute(self) -> None:
        if self._state != "ready" or not self._pdf_files:
            return

        template_id = self._template_combo.currentData()
        run_ocr = self._ocr_checkbox.isChecked()
        input_dir = self._dir_input.text().strip()

        if run_ocr:
            # D14：OCR 勾选前检测 paddleocr 可用性，不可用则询问降级（避免整批 skipped 无解释）
            try:
                import paddleocr  # noqa: F401
            except ImportError:
                reply = QMessageBox.question(
                    self, _("OCR 依赖缺失"),
                    _("未检测到 PaddleOCR 依赖，OCR 功能不可用。\n" +

                      _("是否仍继续（本次仅规则抽取，不执行 OCR）？")),
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.Yes,
                )
                if reply != QMessageBox.StandardButton.Yes:
                    return
                run_ocr = False

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
        self._stage_label.setText(_("正在准备..."))

        # C50：PDF 正文外发第三方 AI 前一次性确认（目标域名 + 预计文本量）
        ai_egress_ok = self._confirm_pdf_ai_egress()
        if ai_egress_ok:
            # 用户本次放行：清除上一次拒绝留下的进程级禁用标记，强制按当前配置桥接
            os.environ.pop("PDFX_LLM_PROVIDER", None)
        # 将 GUI 已配置的 AI 密钥桥接为 PDF 子系统所需的 PDFX_LLM_* 变量
        self._inject_pdf_llm_env()
        if not ai_egress_ok:
            # 用户拒绝外发 → 本次强制关闭 LLM，仅规则抽取
            os.environ["PDFX_LLM_PROVIDER"] = "disabled"

        # D18：持久工作目录——不再用临时目录（重跑复用 sqlite 增量续跑，结果不随系统清理丢失）
        import hashlib

        from ...core.runtime_paths import portable_data_root

        persistent_root = portable_data_root() / ".omnicrawl" / "pdf-workbench"
        try:
            persistent_root.mkdir(parents=True, exist_ok=True)
            task_key = hashlib.sha1(input_dir.encode("utf-8")).hexdigest()[:10]
            self._temp_dir = str(persistent_root / f"task-{task_key}")
            os.makedirs(self._temp_dir, exist_ok=True)
        except OSError:
            # 持久目录不可写时回退临时目录（功能不中断）
            self._temp_dir = tempfile.mkdtemp(prefix="omnicrawl_pdf_")
        work_dir = os.path.join(self._temp_dir, "work")
        output_dir = os.path.join(self._temp_dir, "output")
        config_path = os.path.join(self._temp_dir, "project.yaml")

        try:
            from omnicrawl.pdfx.project import create_project_config

            create_project_config(
                template_path=template_id,
                destination=config_path,
                project_name=_("PDF工作台任务"),
                input_dir=input_dir,
                work_dir=work_dir,
                output_dir=output_dir,
                ocr_backend="paddle" if run_ocr else "none",
            )
        except Exception as exc:
            self._on_failed(_(f"创建项目配置失败: {exc}"))
            return

        self._worker = _PdfPipelineWorker(
            config_path, run_ocr=run_ocr, parent=self
        )
        self._worker.stage_started.connect(self._on_stage_started)
        self._worker.stage_finished.connect(self._on_stage_finished)
        self._worker.warnings_received.connect(self._on_warnings)
        self._worker.progress.connect(self._progress_bar.setValue)
        self._worker.document_progress.connect(self._on_document_progress)
        self._worker.all_done.connect(self._on_done)
        self._worker.failed.connect(self._on_failed)
        self._worker.start()

    def _confirm_pdf_ai_egress(self) -> bool:
        """C50：PDF 正文外发第三方 AI 前一次性确认。

        返回 True 表示放行（AI 未启用/本地模型/无密钥时无需确认直接放行）。
        """
        main = self.window()
        loader = getattr(main, "_load_ai_config_from_env", None)
        if loader is None:
            return True
        try:
            ai_config = loader()
        except Exception:
            return True
        provider = ai_config.get("providers", {}).get("default", {})
        if ai_config.get("mode") != "enabled" or not isinstance(provider, dict):
            return True  # AI 未启用无需确认
        # C37：隐私开关 — PDF 正文外发被禁用时直接拒绝 AI 外发，回退规则抽取
        try:
            from ...core.ai_env import load_ai_privacy

            privacy = load_ai_privacy(getattr(main, "_project_root", None))
        except Exception:
            privacy = {}
        if not privacy.get("allow_pdf_content", True):
            QMessageBox.information(
                self,
                _("PDF 内容 AI 已禁用"),
                _("按隐私设置，PDF 正文不会发送到任何 AI 服务。本次将仅使用本地规则抽取。"),
            )
            return False
        base_url = str(provider.get("base_url", "") or "")
        api_key = str(provider.get("api_key", "") or "")
        is_local = "127.0.0.1" in base_url or "localhost" in base_url
        if not api_key and not is_local:
            return True  # 无密钥不会实际外发
        char_count = sum(path.stat().st_size for path in self._pdf_files if path.is_file())
        host = urllib.parse.urlsplit(base_url).netloc or base_url
        reply = QMessageBox.question(
            self,
            _("PDF 内容外发确认"),
            _("将把 PDF 正文发送到外部 AI 服务：\n\n目标: {0}\n预计文本量: 约 {1} 字符\n\n" +

              _("仅在确认信任该服务后继续。拒绝后将使用规则抽取。")).format(host, char_count),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        return reply == QMessageBox.StandardButton.Yes

    def _inject_pdf_llm_env(self) -> None:
        """将 GUI 的 AI 配置桥接为 PDF 子系统期望的 PDFX_LLM_* 环境变量。

        根因：GUI 的 AI 服务中心写入 OMNICRAWL_AI_API_KEY，而 PDF 模板
        （generic_template.yaml 等）的 llm 段读取 PDFX_LLM_API_KEY /
        PDFX_LLM_PROVIDER / PDFX_LLM_BASE_URL / PDFX_LLM_MODEL。两者从未桥接，
        导致即便用户在 GUI 配置了密钥，PDF 抽取阶段的 LLM 也因
        provider 默认 disabled 且密钥为空而完全不生效。

        注：PDF 子系统的 llm.provider 仅接受 disabled / openai_compatible，
        因此这里把 GUI 的非 disabled 类型统一映射为 openai_compatible
        （兼容 OpenAI / Ollama / 自定义 OpenAI 风格端点）。
        """
        main = self.window()
        loader = getattr(main, "_load_ai_config_from_env", None)
        if loader is None:
            return
        try:
            ai_config = loader()
        except Exception:
            return
        provider = ai_config.get("providers", {}).get("default", {})
        if ai_config.get("mode") != "enabled" or not isinstance(provider, dict):
            return
        api_key = str(provider.get("api_key", "") or "")
        base_url = str(provider.get("base_url", "") or "")
        # C47：本地端点（Ollama 等）无需 API Key 也应桥接；云端缺 key 才跳过
        is_local = "127.0.0.1" in base_url or "localhost" in base_url
        if not api_key and not is_local:
            return

        gui_provider = str(provider.get("type", "openai_compatible"))
        pdf_provider = "openai_compatible" if gui_provider != "disabled" else "disabled"

        # D5：记录注入键，任务结束后从进程环境清除（减少密钥残留窗口）
        self._injected_keys = []
        injected: dict[str, str] = {}
        if not os.environ.get("PDFX_LLM_PROVIDER"):
            injected["PDFX_LLM_PROVIDER"] = pdf_provider
        if not os.environ.get("PDFX_LLM_API_KEY"):
            injected["PDFX_LLM_API_KEY"] = provider["api_key"]
        if not os.environ.get("PDFX_LLM_BASE_URL"):
            injected["PDFX_LLM_BASE_URL"] = provider.get("base_url", "")
        if not os.environ.get("PDFX_LLM_MODEL"):
            injected["PDFX_LLM_MODEL"] = provider.get("model", "")
        if not os.environ.get("PDFX_LLM_TIMEOUT"):
            injected["PDFX_LLM_TIMEOUT"] = str(provider.get("timeout_seconds", 60))
        for key, value in injected.items():
            os.environ[key] = value
            self._injected_keys.append(key)

    @pyqtSlot(str)
    def _on_stage_started(self, stage: str) -> None:
        self._stage_label.setText(f"⏳ {stage}...")

    @pyqtSlot(str, object)
    def _on_stage_finished(self, stage: str, result: object) -> None:
        del result  # 无信息量载荷，仅通知到达
        self._stage_label.setText(_(f"✓ {stage} 完成"))

    @pyqtSlot(int, int)
    def _on_document_progress(self, processed: int, total: int) -> None:
        """B12：抽取阶段实时把「已处理 X/Y 文档」混入进度条，避免大批量时卡死错觉。

        抽取阶段在 6 阶段流水线中排第 5（前 4 阶段完成后进度约 66%），
        故以其内部进度在 66%~83% 区间线性展开，使进度条持续前进。
        """
        if total <= 0:
            return
        base = 4 / 6 * 100          # 进入抽取阶段时的进度基线
        span = 1 / 6 * 100          # 抽取阶段占据的进度跨度
        pct = int(base + min(processed, total) / total * span)
        self._progress_bar.setValue(min(pct, 99))
        self._stage_label.setText(_(f"⏳ 抽取文档 {processed}/{total}..."))

    @pyqtSlot(list)
    def _on_warnings(self, items: list) -> None:
        """D3：显示管线关键警告（AI Key 为空/OCR 未启用等），不再静默丢弃。"""
        for item in items:
            self._stage_label.setText(f"⚠ {item}")
        existing = self._result_text.toPlainText()
        block = "\n".join(f"⚠ {item}" for item in items)
        self._result_text.setText(_(f"{existing}\n\n[运行警告]\n{block}") if existing else _(f"[运行警告]\n{block}"))

    @pyqtSlot(object)
    def closeEvent(self, event) -> None:
        """S1.1.5：关闭前取消并等待 PDF 后台线程，避免 QThread 销毁时仍在运行。"""
        worker = getattr(self, "_worker", None)
        if worker is not None and worker.isRunning():
            worker.cancel()
            worker.requestInterruption()
            worker.wait(5000)
            self._clear_injected_env()
        self._state = "idle"
        super().closeEvent(event)

    def _on_done(self, result: object) -> None:
        self._clear_injected_env()
        self._state = "done"
        self._progress_bar.setValue(100)
        # S2.3.4：部分阶段失败不得显示"全部完成"
        failures = _collect_failures(result)
        if failures:
            self._stage_label.setText(_("⚠ 部分阶段失败"))
        else:
            self._stage_label.setText(_("✓ 全部完成！"))
        self._cancel_btn.setVisible(False)
        self._execute_btn.setVisible(True)
        self._execute_btn.setText(_("重新执行"))
        self._execute_btn.setEnabled(True)
        self._scan_btn.setEnabled(True)
        self._template_combo.setEnabled(True)
        self._ocr_checkbox.setEnabled(True)

        result_data = result if isinstance(result, dict) else {}
        status = result_data.get("status", {})
        export_info = result_data.get("export", {}) if isinstance(result_data.get("export"), dict) else {}

        lines: list[str] = []
        lines.append(_("=== PDF 处理完成 ==="))
        if failures:
            lines.append("")
            lines.append(_(f"⚠ 部分阶段失败（{len(failures)} 处）："))
            for message in failures[:10]:
                lines.append(f"  ❌ {message}")
            lines.append("")
        docs = status.get("documents", {}) if isinstance(status, dict) else {}
        pages = status.get("pages", {}) if isinstance(status, dict) else {}
        records = status.get("records", {}) if isinstance(status, dict) else {}

        lines.append(_(f"文档: {docs}"))
        lines.append(_(f"页面: 共 {pages.get('total', '?')} 页, OCR {pages.get('ocr_done', '?')} 页"))
        lines.append(_(f"记录: 共 {records.get('total', '?')} 条, 需复核 {records.get('needs_review', '?')} 条"))

        # B13：字段级"AI vs 规则"来源分布，明示本次到底有没有真的用大模型
        extract = result_data.get("extract", {}) if isinstance(result_data.get("extract"), dict) else {}
        methods = extract.get("extraction_methods", {}) if isinstance(extract.get("extraction_methods"), dict) else {}
        if methods:
            ai_count = sum(int(n) for m, n in methods.items() if "llm" in str(m).lower())
            rule_count = sum(int(n) for m, n in methods.items() if "llm" not in str(m).lower())
            lines.append("")
            lines.append(_(f"抽取方式: 🤖 AI 大模型 {ai_count} 条, 📐 规则/启发式 {rule_count} 条"))
            detail = ", ".join(_(f"{m}={n}") for m, n in methods.items())
            lines.append(_(f"  明细: {detail}"))
            sel = extract.get("selected")
            if sel is not None:
                lines.append(_(f"  抽取统计: 选中文档 {sel}, 无数据 {extract.get('no_data', '?')}, 失败 {extract.get('failed', '?')}"))

        output_files = export_info.get("files", {}) if isinstance(export_info, dict) else {}
        output_paths = (
            [str(path) for path in output_files.values() if str(path).strip()]
            if isinstance(output_files, dict)
            else [str(item) for item in output_files]
        )
        if output_paths:
            lines.append(_(f"\n输出文件 ({len(output_paths)} 个):"))
            for f in output_paths:
                lines.append(f"  📄 {f}")

        self._result_text.setText("\n".join(lines))
        self._result_group.setVisible(True)

        # Toast
        toast = ToastManager.instance()
        if failures:
            toast.warning(_(f"PDF 处理完成但 {len(failures)} 处阶段失败，详见结果面板"))
        else:
            toast.success(_(f"PDF 处理完成！共处理 {docs.get('ingested', '?')} 份文档"))

    def _clear_injected_env(self) -> None:
        """D5：任务结束后清除本工作台注入的 PDFX_LLM_* 环境变量（减少密钥残留窗口）。"""
        for key in getattr(self, "_injected_keys", []):
            os.environ.pop(key, None)
        self._injected_keys = []

    @pyqtSlot(str)
    def _on_failed(self, msg: str) -> None:
        self._clear_injected_env()
        self._state = "idle"
        self._progress_bar.setVisible(False)
        self._stage_label.setVisible(False)
        self._cancel_btn.setVisible(False)
        self._execute_btn.setVisible(True)
        self._execute_btn.setText(_("▶ 开始处理"))
        self._execute_btn.setEnabled(True)
        self._scan_btn.setEnabled(True)
        self._template_combo.setEnabled(True)
        self._ocr_checkbox.setEnabled(True)

        self._result_text.setText(_(f"处理失败:\n{msg}"))
        self._result_group.setVisible(True)

        toast = ToastManager.instance()
        toast.error(_(f"PDF 处理失败: {msg.split(chr(10))[0]}"))

    # ── 取消 ───────────────────────────────────────────────────
    @pyqtSlot()
    def _cancel(self) -> None:
        if self._worker and self._worker.isRunning():
            self._worker.cancel()
            self._stage_label.setText(_("正在取消..."))
            # S3.1.6：等待线程结束并清理注入环境变量（PDFX_LLM_API_KEY 无残留）
            self._worker.wait(5000)
            self._clear_injected_env()

    # ── 打开结果 ──────────────────────────────────────────────
    @pyqtSlot()
    def _open_output_dir(self) -> None:
        if self._temp_dir:
            output = os.path.join(self._temp_dir, "output")
            if os.path.isdir(output):
                # A13/D65：跨平台打开结果目录（os.startfile 仅 Windows）
                QDesktopServices.openUrl(QUrl.fromLocalFile(str(output)))
                return
        toast = ToastManager.instance()
        toast.warning(_("输出目录不存在或已被清理"))

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
            # A13/D65：跨平台打开结果文件（os.startfile 仅 Windows）
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(files[0])))
        else:
            toast = ToastManager.instance()
            toast.warning(_("未找到 Excel/CSV 输出文件"))

    # ── 重置 ───────────────────────────────────────────────────
    @pyqtSlot()
    def _reset(self) -> None:
        self._state = "idle"
        self._result_group.setVisible(False)
        self._progress_bar.setVisible(False)
        self._progress_bar.setValue(0)
        self._stage_label.setVisible(False)
        self._stage_label.setText("")
        self._execute_btn.setText(_("▶ 开始处理"))
        self._execute_btn.setEnabled(bool(self._pdf_files))
        self._scan_btn.setEnabled(True)
        self._template_combo.setEnabled(True)
        self._ocr_checkbox.setEnabled(True)
