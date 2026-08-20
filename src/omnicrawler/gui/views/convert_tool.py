"""B-4：ConvertX GUI 面板 — 拖拽文件 + 任选目标格式 + 后台转换 + 进度条。

P3-2 `omnicrawler.convertx` 模块的 GUI 化入口；不影响 CLI 用法。
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QThread, Signal, Slot
from PySide6.QtGui import QDragEnterEvent, QDropEvent
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QStyle,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ..design_system import FONT_FAMILY_MONO
from ..i18n import _
from ..widgets.toast import ToastManager


def _repolish_widget(widget: QWidget) -> None:
    """按 QSS 动态属性刷新控件外观。"""
    style = widget.style()
    if isinstance(style, QStyle):
        style.unpolish(widget)
        style.polish(widget)
    widget.ensurePolished()


class _ConvertWorker(QThread):
    """后台线程：跑 convertx.convert(...)，把 P3-1 统一进度桥到 UI 信号。"""

    progress = Signal(int)
    unified_progress = Signal(object)  # TaskProgressEvent
    succeeded = Signal(object)
    failed = Signal(str)
    stage_started = Signal(str)

    def __init__(
        self,
        *,
        src_path: Path,
        dst_path: Path,
        src_format: str | None,
        dst_format: str,
        flat: bool,
        nested: bool,
        table: str,
        compression: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._src = Path(src_path)
        self._dst = Path(dst_path)
        self._src_fmt = src_format
        self._dst_fmt = dst_format
        self._flat = flat
        self._nested = nested
        self._table = table
        self._compression = compression
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    def run(self) -> None:  # noqa: D401
        try:
            from omnicrawler import convertx
            from omnicrawler.services.progress import (
                TaskProgressEvent,
                event_to_percent,
                format_eta,
            )

            active_stage: str = ""

            def _on_progress(ev: TaskProgressEvent) -> None:
                # 先发 unified（供将来扩展的精细消费方）
                self.unified_progress.emit(ev)
                # 再推整型 percent（兼容老的进度条信号）
                self.progress.emit(event_to_percent(ev))
                # 阶段名变化时广播 stage_started（带 ETA + 子项计数格式化）
                nonlocal active_stage
                stage_parts: list[str] = []
                if ev.display_stage:
                    stage_parts.append(ev.display_stage)
                if ev.item_total > 0:
                    stage_parts.append(f"{ev.item_current}/{ev.item_total}")
                if ev.eta_seconds > 0:
                    stage_parts.append(_("剩余 {0}").format(format_eta(ev.eta_seconds)))
                if ev.state == "failed":
                    stage_parts.append(_("失败"))
                elif ev.state == "finished":
                    stage_parts.append(_("完成"))
                label = " · ".join(stage_parts) if stage_parts else ev.stage or _("处理中")
                if label != active_stage:
                    active_stage = label
                    self.stage_started.emit(label)

            # ConvertX 内部的 ProgressTracker（权重 read 60% + write 40%）
            # 通过 on_progress 回调推送 TaskProgressEvent，这里只负责桥信号
            result = convertx.convert(
                self._src,
                self._dst,
                src_format=self._src_fmt,
                dst_format=self._dst_fmt,
                flat=self._flat,
                nested=self._nested,
                table=self._table,
                compression=self._compression,
                on_progress=_on_progress,
            )

            # 确保完成态覆盖一次（即使 tracker.finish() 已推 100%，这里保险）
            self.progress.emit(100)
            self.succeeded.emit(result)
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(str(exc))


class _DropZone(QFrame):
    """拖拽接收区。点击自身等价于「选择文件」。"""

    files_dropped = Signal(list)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("ConvertDropZone")
        self.setAcceptDrops(True)
        self.setMinimumHeight(160)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon = QLabel("\u21c5")
        icon.setObjectName("DropZoneIcon")
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title = QLabel(_("拖拽文件到此，或点击下方选择"))
        title.setObjectName("DropZoneTitle")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        sub = QLabel(_("支持：CSV、JSONL、NDJSON、XLSX、Parquet、DuckDB（.db/.duckdb）"))
        sub.setObjectName("DropZoneSub")
        sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        sub.setWordWrap(True)
        layout.addStretch(1)
        layout.addWidget(icon)
        layout.addSpacing(6)
        layout.addWidget(title)
        layout.addWidget(sub)
        layout.addStretch(1)

    def set_highlight(self, on: bool) -> None:
        self.setProperty("dragHover", "1" if on else "")
        _repolish_widget(self)

    def dragEnterEvent(self, ev: QDragEnterEvent | None) -> None:
        if ev is None:
            return
        mime = ev.mimeData()
        if mime is not None and mime.hasUrls():
            ev.acceptProposedAction()
            self.set_highlight(True)
        else:
            ev.ignore()

    def dragLeaveEvent(self, ev) -> None:
        self.set_highlight(False)

    def dropEvent(self, ev: QDropEvent | None) -> None:
        self.set_highlight(False)
        if ev is None:
            return
        mime = ev.mimeData()
        if mime is None or not mime.hasUrls():
            return
        paths: list[str] = []
        for u in mime.urls():
            if u.isLocalFile():
                p = u.toLocalFile()
                if Path(p).is_file():
                    paths.append(p)
        if paths:
            ev.acceptProposedAction()
            self.files_dropped.emit(paths)
        else:
            ev.ignore()

    def mousePressEvent(self, ev) -> None:
        if ev.button() == Qt.MouseButton.LeftButton:
            self.files_dropped.emit(["__PICK__"])
        super().mousePressEvent(ev)


class ConvertView(QWidget):
    """P3-2 ConvertX 的 GUI 入口。

    外部可连接 open_output_folder_requested 打开输出目录；home.py 可直接跳转此处。
    """

    open_output_folder_requested = Signal(str)

    _SUPPORTED_FORMATS: list[tuple[str, str, str]] = [
        (_("CSV (.csv)"),         "csv",     ".csv"),
        (_("JSONL / NDJSON"),     "jsonl",   ".jsonl"),
        (_("Excel (.xlsx)"),      "xlsx",    ".xlsx"),
        (_("Parquet (.parquet)"), "parquet", ".parquet"),
        (_("DuckDB (.duckdb)"),   "duckdb",  ".duckdb"),
    ]

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("ConvertToolView")
        self._src_path: Path | None = None
        self._src_format_sniffed: str | None = None
        self._worker: _ConvertWorker | None = None

        self._build_ui()
        self._apply_style_weak()
        self._update_runnable_state()

    def _toast(self) -> ToastManager:
        return ToastManager.instance()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(28, 24, 28, 24)
        root.setSpacing(16)

        title = QLabel(_("\U0001f4c2 格式互转（ConvertX）"))
        title.setObjectName("pageTitle")
        subtitle = QLabel(_("任意文件间双向转换：CSV <-> JSONL <-> Excel <-> Parquet <-> DuckDB；"
                              "文档可抽取为文本 / Markdown。大文件无需重跑 pipeline。"))
        subtitle.setObjectName("pageSubtitle")
        subtitle.setWordWrap(True)
        root.addWidget(title)
        root.addWidget(subtitle)

        self._tabs = QTabWidget()
        self._tabs.setObjectName("ConvertTabs")
        self._tabs.addTab(self._build_grid_tab(), _("格式互转"))
        self._tabs.addTab(_DocExtractTab(), _("文档抽取"))
        root.addWidget(self._tabs)

    def _build_grid_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(0, 8, 0, 8)
        layout.setSpacing(16)
        root = layout

        self._drop = _DropZone()
        self._drop.files_dropped.connect(self._on_files_dropped)
        root.addWidget(self._drop)

        info_box = QGroupBox(_("转换设置"))
        info_layout = QVBoxLayout(info_box)
        info_layout.setSpacing(10)

        src_row = QHBoxLayout()
        self._src_label = QLabel(_("源文件："))
        self._src_path_edit = QLineEdit()
        self._src_path_edit.setReadOnly(True)
        self._src_path_edit.setPlaceholderText(_("尚未选择源文件"))
        self._btn_pick = QPushButton(_("选择文件..."))
        self._btn_pick.clicked.connect(self._pick_source_file)
        self._src_fmt_label = QLabel("?")
        self._src_fmt_label.setObjectName("ConvertFormatBadge")
        src_row.addWidget(self._src_label)
        src_row.addWidget(self._src_path_edit, 1)
        src_row.addWidget(self._btn_pick)
        src_row.addWidget(self._src_fmt_label)
        info_layout.addLayout(src_row)

        opt_row = QHBoxLayout()
        opt_row.addWidget(QLabel(_("目标格式：")))
        self._fmt_combo = QComboBox()
        for label, key, _ext in self._SUPPORTED_FORMATS:
            self._fmt_combo.addItem(label, key)
        self._fmt_combo.setCurrentIndex(1)
        self._fmt_combo.currentIndexChanged.connect(self._update_runnable_state)
        opt_row.addWidget(self._fmt_combo)
        opt_row.addSpacing(20)

        self._chk_flat = QCheckBox(_("展开嵌套 JSON（flat 模式，默认）"))
        self._chk_flat.setChecked(True)
        self._chk_flat.toggled.connect(self._on_flat_nested_toggle)
        self._chk_nested = QCheckBox(_("保留完整嵌套（nested pipeline 原始格式）"))
        self._chk_nested.setChecked(False)
        self._chk_nested.toggled.connect(self._on_nested_toggled)
        opt_row.addWidget(self._chk_flat)
        opt_row.addWidget(self._chk_nested)
        opt_row.addStretch(1)
        info_layout.addLayout(opt_row)

        adv_row = QHBoxLayout()
        adv_row.addWidget(QLabel(_("DuckDB 表名：")))
        self._table_edit = QLineEdit("records")
        self._table_edit.setFixedWidth(160)
        adv_row.addWidget(self._table_edit)
        adv_row.addSpacing(18)
        adv_row.addWidget(QLabel(_("Parquet 压缩：")))
        self._comp_combo = QComboBox()
        for comp in ("zstd", "snappy", "gzip", "none"):
            self._comp_combo.addItem(comp.upper(), comp)
        self._comp_combo.setCurrentIndex(0)
        adv_row.addWidget(self._comp_combo)
        adv_row.addStretch(1)
        info_layout.addLayout(adv_row)

        dst_row = QHBoxLayout()
        dst_row.addWidget(QLabel(_("输出路径：")))
        self._dst_path_edit = QLineEdit()
        self._dst_path_edit.setPlaceholderText(_("点击开始转换后自动补默认路径；也可手动指定"))
        self._btn_pick_dst = QPushButton(_("另存为..."))
        self._btn_pick_dst.clicked.connect(self._pick_dst_file)
        dst_row.addWidget(self._dst_path_edit, 1)
        dst_row.addWidget(self._btn_pick_dst)
        info_layout.addLayout(dst_row)

        root.addWidget(info_box)

        prog_box = QGroupBox(_("进度"))
        prog_layout = QVBoxLayout(prog_box)
        self._stage_label = QLabel(_("尚未开始"))
        self._stage_label.setObjectName("ConvertStageLabel")
        self._progress = QProgressBar()
        self._progress.setRange(0, 100)
        self._progress.setValue(0)
        self._log = QTextEdit()
        self._log.setReadOnly(True)
        self._log.setFixedHeight(110)
        self._log.setFontFamily(FONT_FAMILY_MONO)
        prog_layout.addWidget(self._stage_label)
        prog_layout.addWidget(self._progress)
        prog_layout.addWidget(QLabel(_("日志：")))
        prog_layout.addWidget(self._log)
        root.addWidget(prog_box)

        actions = QHBoxLayout()
        actions.addStretch(1)
        self._btn_cancel = QPushButton(_("取消"))
        self._btn_cancel.setEnabled(False)
        self._btn_cancel.clicked.connect(self._cancel_conversion)
        self._btn_run = QPushButton(_("\u25b6 开始转换"))
        self._btn_run.setObjectName("primaryButton")
        self._btn_run.clicked.connect(self._start_conversion)
        actions.addWidget(self._btn_cancel)
        actions.addWidget(self._btn_run)
        root.addLayout(actions)
        return tab

    def _apply_style_weak(self) -> None:
        self._drop.setProperty("card", "1")
        self._src_fmt_label.setProperty("badge", "1")
        _repolish_widget(self._drop)
        _repolish_widget(self._src_fmt_label)

    def _pick_source_file(self) -> None:
        self._on_files_dropped(["__PICK__"])

    def _on_files_dropped(self, paths: list[str]) -> None:
        if not paths:
            return
        if paths == ["__PICK__"]:
            filters = (
                _("可读格式 (*.csv *.jsonl *.ndjson *.xlsx *.parquet *.duckdb *.db);;")
                + _("所有文件 (*.*)")
            )
            selected, _selected_filter = QFileDialog.getOpenFileName(
                self, _("选择源文件"), "", filters,
            )
            if not selected:
                return
            self._set_source(Path(selected))
            return
        if len(paths) > 1:
            self._toast().warning(_("暂只支持单文件转换；已取拖拽列表中第一个。"))
        self._set_source(Path(paths[0]))

    def _set_source(self, p: Path) -> None:
        try:
            p = p.resolve()
        except OSError:
            self._toast().error(_("无法解析文件路径：{0}").format(p))
            return
        if not p.is_file():
            self._toast().error(_("源路径不是文件：{0}").format(p))
            return
        self._src_path = p
        self._src_path_edit.setText(str(p))
        try:
            from omnicrawler import convertx
            sniffs = convertx.sniff_format(p)
        except Exception as exc:  # noqa: BLE001
            self._toast().error(_("源格式识别失败：{0}").format(exc))
            return
        self._src_format_sniffed = sniffs if sniffs else None
        self._src_fmt_label.setText(str(self._src_format_sniffed or _("未知")))
        self._auto_suggest_dst()
        self._update_runnable_state()
        self._log.append(_("\u2705 已加载源文件：{0}  （识别格式：{1}）").format(
            p.name, self._src_format_sniffed or _("未知"),
        ))

    def _pick_dst_file(self) -> None:
        key = self._fmt_combo.currentData()
        ext = next((e for _l, k, e in self._SUPPORTED_FORMATS if k == key), ".out")
        caption = _("另存为 {0} 文件").format(self._fmt_combo.currentText())
        filters = f"*{ext};;{_('所有文件 (*.*)')}"
        start_dir = str(self._src_path.parent) if self._src_path else ""
        selected, _selected_filter = QFileDialog.getSaveFileName(self, caption, start_dir, filters)
        if selected:
            self._dst_path_edit.setText(selected)

    def _auto_suggest_dst(self) -> None:
        if self._src_path is None:
            return
        key = self._fmt_combo.currentData()
        ext = next((e for _l, k, e in self._SUPPORTED_FORMATS if k == key), "")
        if not ext:
            suggested = self._src_path.with_suffix(".out")
        else:
            suggested = self._src_path.with_suffix(ext)
        i = 1
        while suggested.exists():
            stem = self._src_path.stem
            if ext:
                suggested = self._src_path.with_name(f"{stem}-{i}{ext}")
            else:
                suggested = self._src_path.with_name(f"{stem}-{i}.out")
            i += 1
        self._dst_path_edit.setText(str(suggested))

    def _on_flat_nested_toggle(self, flat_checked: bool) -> None:
        if flat_checked and self._chk_nested.isChecked():
            self._chk_nested.blockSignals(True)
            self._chk_nested.setChecked(False)
            self._chk_nested.blockSignals(False)
        elif not flat_checked and not self._chk_nested.isChecked():
            self._chk_flat.blockSignals(True)
            self._chk_flat.setChecked(True)
            self._chk_flat.blockSignals(False)

    def _on_nested_toggled(self, nested_checked: bool) -> None:
        if nested_checked and self._chk_flat.isChecked():
            self._chk_flat.blockSignals(True)
            self._chk_flat.setChecked(False)
            self._chk_flat.blockSignals(False)
        elif not nested_checked and not self._chk_flat.isChecked():
            self._chk_flat.blockSignals(True)
            self._chk_flat.setChecked(True)
            self._chk_flat.blockSignals(False)

    def _update_runnable_state(self) -> None:
        src_ok = self._src_path is not None
        running = self._worker is not None and self._worker.isRunning()
        self._btn_run.setEnabled(src_ok and not running)
        self._btn_cancel.setEnabled(running)
        if not running and src_ok and not self._dst_path_edit.text().strip():
            self._auto_suggest_dst()

    def _start_conversion(self) -> None:
        if self._src_path is None:
            self._toast().warning(_("请先选择源文件"))
            return
        dst_text = self._dst_path_edit.text().strip()
        if not dst_text:
            self._auto_suggest_dst()
            dst_text = self._dst_path_edit.text().strip()
        dst_path = Path(dst_text)
        try:
            dst_path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            self._toast().error(_("无法创建输出目录：{0}").format(exc))
            return
        try:
            if dst_path.resolve() == self._src_path.resolve():
                QMessageBox.warning(self, _("目标与源相同"),
                                    _("目标路径与源文件相同，为防止覆盖已取消。"))
                return
        except OSError:
            pass
        dst_fmt = self._fmt_combo.currentData()
        self._progress.setValue(0)
        self._stage_label.setText(_("准备中..."))
        self._log.clear()
        self._log.append(_("\u25b6 开始转换：{0} -> {1}（{2}）").format(
            self._src_path.name, dst_fmt, dst_path.name,
        ))
        worker = _ConvertWorker(
            src_path=self._src_path,
            dst_path=dst_path,
            src_format=self._src_format_sniffed,
            dst_format=dst_fmt,
            flat=self._chk_flat.isChecked() and not self._chk_nested.isChecked(),
            nested=self._chk_nested.isChecked(),
            table=self._table_edit.text().strip() or "records",
            compression=self._comp_combo.currentData() or "zstd",
            parent=self,
        )
        worker.progress.connect(self._on_progress)
        worker.stage_started.connect(self._stage_label.setText)
        worker.succeeded.connect(self._on_succeeded)
        worker.failed.connect(self._on_failed)

        def _finalize_worker() -> None:
            worker.deleteLater()
            self._clear_worker()

        worker.finished.connect(_finalize_worker)
        self._worker = worker
        self._update_runnable_state()
        worker.start()

    def _cancel_conversion(self) -> None:
        if self._worker is not None and self._worker.isRunning():
            self._worker.cancel()
            self._toast().info(_("已请求取消，等待当前阶段安全退出..."))

    def _clear_worker(self) -> None:
        self._worker = None
        self._update_runnable_state()

    @Slot(int)
    def _on_progress(self, pct: int) -> None:
        self._progress.setValue(max(0, min(100, int(pct))))

    @Slot(object)
    def _on_succeeded(self, result: object) -> None:
        rows = int(getattr(result, "rows", 0) or 0)
        extra = getattr(result, "extra", {}) or {}
        bytes_written = extra.get("bytes", 0) if isinstance(extra, dict) else 0
        src_fmt = str(getattr(result, "source_format", "") or "")
        dst_fmt = str(getattr(result, "target_format", "") or "")
        output_path = getattr(result, "output_path", None)
        dst_str = str(output_path) if output_path is not None else ""
        try:
            size_mb = round(float(bytes_written) / (1024 * 1024), 2) if bytes_written else None
        except (TypeError, ValueError):
            size_mb = None
        msg = _("\u2705 转换完成：{0} 行").format(rows)
        if size_mb is not None:
            msg += _("，输出大小 {0} MB").format(size_mb)
        self._stage_label.setText(msg)
        self._log.append(f"src_fmt: {src_fmt}")
        self._log.append(f"dst_fmt: {dst_fmt}")
        self._log.append(f"output:  {dst_str}")
        self._log.append(msg)
        warnings = getattr(result, "warnings", []) or []
        for w in warnings:
            self._log.append(f"warn:  {w}")
        self._progress.setValue(100)
        self._toast().success(msg)
        if dst_str:
            self.open_output_folder_requested.emit(str(Path(dst_str).parent))
        self._update_runnable_state()

    @Slot(str)
    def _on_failed(self, message: str) -> None:
        self._stage_label.setText(_("\u274c 转换失败"))
        self._log.append(_("\u274c 错误：{0}").format(message))
        self._toast().error(_("转换失败：{0}").format(message))
        self._update_runnable_state()


class _DocWorker(QThread):
    """后台线程：文档 → 文本/Markdown（复用 convertx.convert + 统一进度协议）。"""

    progress = Signal(int)
    stage_started = Signal(str)
    succeeded = Signal(object)
    failed = Signal(str)

    def __init__(
        self,
        *,
        src_path: Path,
        dst_path: Path,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._src = Path(src_path)
        self._dst = Path(dst_path)
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    def run(self) -> None:  # noqa: D401
        try:
            from omnicrawler import convertx
            from omnicrawler.services.progress import (
                TaskProgressEvent,
                event_to_percent,
            )

            active_stage: str = ""

            def _on_progress(ev: TaskProgressEvent) -> None:
                self.progress.emit(event_to_percent(ev))
                nonlocal active_stage
                parts: list[str] = []
                if ev.display_stage:
                    parts.append(ev.display_stage)
                if ev.state == "finished":
                    parts.append(_("完成"))
                elif ev.state == "failed":
                    parts.append(_("失败"))
                label = " · ".join(parts) if parts else ev.stage or _("处理中")
                if label != active_stage:
                    active_stage = label
                    self.stage_started.emit(label)

            result = convertx.convert(self._src, self._dst, on_progress=_on_progress)
            self.progress.emit(100)
            self.succeeded.emit(result)
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(str(exc))


class _DocExtractTab(QWidget):
    """ConvertX 面板「文档抽取」tab：任意文档 → 文本 / Markdown（document_ir 桥接）。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._src_path: Path | None = None
        self._worker: _DocWorker | None = None
        self._build_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 8, 0, 8)
        root.setSpacing(12)

        info_box = QGroupBox(_("文档来源"))
        info_layout = QVBoxLayout(info_box)
        src_row = QHBoxLayout()
        self._src_edit = QLineEdit()
        self._src_edit.setReadOnly(True)
        self._src_edit.setPlaceholderText(_("支持：.txt .html .eml .docx .pptx .odt .epub"))
        self._btn_pick = QPushButton(_("选择文档..."))
        self._btn_pick.clicked.connect(self._pick_document)
        src_row.addWidget(self._src_edit, 1)
        src_row.addWidget(self._btn_pick)
        info_layout.addLayout(src_row)

        opt_row = QHBoxLayout()
        opt_row.addWidget(QLabel(_("导出为：")))
        self._fmt_combo = QComboBox()
        self._fmt_combo.addItem(_("文本（.txt）"), ".txt")
        self._fmt_combo.addItem(_("Markdown（.md）"), ".md")
        opt_row.addWidget(self._fmt_combo)
        opt_row.addSpacing(20)
        opt_row.addWidget(QLabel(_("输出路径：")))
        self._dst_edit = QLineEdit()
        self._dst_edit.setPlaceholderText(_("留空则与源文件同目录同名"))
        opt_row.addWidget(self._dst_edit, 1)
        info_layout.addLayout(opt_row)
        root.addWidget(info_box)

        prog_box = QGroupBox(_("进度"))
        prog_layout = QVBoxLayout(prog_box)
        self._stage = QLabel(_("尚未开始"))
        self._stage.setObjectName("ConvertStageLabel")
        self._progress = QProgressBar()
        self._progress.setRange(0, 100)
        self._progress.setValue(0)
        self._log = QTextEdit()
        self._log.setReadOnly(True)
        self._log.setFixedHeight(110)
        self._log.setFontFamily(FONT_FAMILY_MONO)
        prog_layout.addWidget(self._stage)
        prog_layout.addWidget(self._progress)
        prog_layout.addWidget(QLabel(_("日志：")))
        prog_layout.addWidget(self._log)
        root.addWidget(prog_box)

        actions = QHBoxLayout()
        actions.addStretch(1)
        self._btn_cancel = QPushButton(_("取消"))
        self._btn_cancel.setEnabled(False)
        self._btn_cancel.clicked.connect(self._cancel_doc)
        self._btn_run = QPushButton(_("\u25b6 开始抽取"))
        self._btn_run.setObjectName("primaryButton")
        self._btn_run.clicked.connect(self._start_doc)
        actions.addWidget(self._btn_cancel)
        actions.addWidget(self._btn_run)
        root.addLayout(actions)

        self._update_runnable_state()

    def _toast(self) -> ToastManager:
        return ToastManager.instance()

    def _pick_document(self) -> None:
        path, _selected = QFileDialog.getOpenFileName(
            self, _("选择文档"), "",
            _("文档 (*.txt *.html *.htm *.eml *.docx *.pptx *.odt *.epub);;所有文件 (*)"),
        )
        if path:
            self._src_path = Path(path)
            self._src_edit.setText(str(self._src_path))
            self._stage.setText(_("已选择：{0}").format(self._src_path.name))
            self._progress.setValue(0)
            self._update_runnable_state()

    def _default_dst(self) -> Path:
        ext = str(self._fmt_combo.currentData() or ".txt")
        assert self._src_path is not None
        return self._src_path.with_suffix(ext)

    def _start_doc(self) -> None:
        if self._src_path is None:
            self._toast().error(_("请先选择文档"))
            return
        dst_text = self._dst_edit.text().strip()
        dst = Path(dst_text) if dst_text else self._default_dst()
        dst = dst.expanduser().resolve()
        try:
            dst.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            self._toast().error(_("无法创建输出目录：{0}").format(exc))
            return
        self._progress.setValue(0)
        self._stage.setText(_("准备中..."))
        self._log.clear()
        self._log.append(_("\u25b6 抽取：{0} -> {1}").format(self._src_path.name, dst.name))
        worker = _DocWorker(src_path=self._src_path, dst_path=dst, parent=self)
        worker.progress.connect(self._progress.setValue)
        worker.stage_started.connect(self._stage.setText)
        worker.succeeded.connect(self._on_doc_succeeded)
        worker.failed.connect(self._on_doc_failed)

        def _finalize_doc_worker() -> None:
            worker.deleteLater()
            self._clear_doc_worker()

        worker.finished.connect(_finalize_doc_worker)
        self._worker = worker
        self._update_runnable_state()
        worker.start()

    def _cancel_doc(self) -> None:
        if self._worker is not None and self._worker.isRunning():
            self._worker.cancel()
            self._toast().info(_("已请求取消，等待当前阶段安全退出..."))

    def _clear_doc_worker(self) -> None:
        self._worker = None
        self._update_runnable_state()

    def _update_runnable_state(self) -> None:
        running = self._worker is not None and self._worker.isRunning()
        self._btn_run.setEnabled(not running and self._src_path is not None)
        self._btn_cancel.setEnabled(running)

    @Slot(object)
    def _on_doc_succeeded(self, result: object) -> None:
        dst_fmt = str(getattr(result, "target_format", "") or "")
        output_path = getattr(result, "output_path", None)
        dst_str = str(output_path) if output_path is not None else ""
        extra = getattr(result, "extra", {}) or {}
        chars = int(extra.get("chars", 0)) if isinstance(extra, dict) else 0
        msg = _("\u2705 抽取完成：{0}，{1} 字符").format(dst_fmt, chars)
        self._stage.setText(msg)
        self._log.append(f"dst_fmt: {dst_fmt}")
        self._log.append(f"output:  {dst_str}")
        self._log.append(msg)
        self._progress.setValue(100)
        self._toast().success(msg)
        self._update_runnable_state()

    @Slot(str)
    def _on_doc_failed(self, message: str) -> None:
        self._stage.setText(_("\u274c 抽取失败"))
        self._log.append(_("\u274c 错误：{0}").format(message))
        self._toast().error(_("抽取失败：{0}").format(message))
        self._update_runnable_state()
