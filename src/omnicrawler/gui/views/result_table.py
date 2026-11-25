"""结果表格视图。

QTableView + 流式 CSV Model，支持分页、排序、Excel 导出。
CSV 索引（行计数）与 JSONL 证据查找均通过后台线程执行，避免大文件阻塞 UI。
"""

from __future__ import annotations

import csv
import io
import json
import logging
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from PySide6.QtCore import (
    QAbstractTableModel,
    QEasingCurve,
    QModelIndex,
    QPersistentModelIndex,
    QPoint,
    QPropertyAnimation,
    QRegularExpression,
    QSortFilterProxyModel,
    Qt,
    QThread,
    Signal,
)
from PySide6.QtGui import QAction, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QProgressDialog,
    QPushButton,
    QSpinBox,
    QSplitter,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from ...core.utils import excel_safe
from ..core.workers import CsvIndexWorker, JsonlSearchWorker
from ..design_system import repolish_widget
from ..i18n import _

ROWS_PER_PAGE = 1000

_logger = logging.getLogger(__name__)


class CsvStreamModel(QAbstractTableModel):
    """流式 CSV 数据模型。

    按需加载 CSV 行，避免启动时扫描全文。
    表头与行计数通过 CsvIndexWorker 异步获取，避免大文件阻塞 UI。
    """

    indexing_started = Signal()
    indexing_finished = Signal(int)  # total_rows
    indexing_failed = Signal(str)
    indexing_cancelled = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._filepath: Path | None = None
        self._headers: list[str] = []
        self._rows: list[list[str]] = []
        self._total_rows: int = 0
        self._file_size: int = 0
        self._avg_line_size: int = 200  # 估计平均行长
        self._line_offsets: list[int] = []
        #: 每页起始**字节偏移**（后台索引线程建；空列表表示不可用，回退逐行跳过）
        self._byte_offsets: list[int] = []
        self._index_worker: CsvIndexWorker | None = None
        self.last_error: str | None = None  # A17：最近一次翻页/加载错误（视图层展示）

    def load_file_async(self, filepath: Path) -> bool:
        """异步加载 CSV 文件。

        立即返回 True 表示已派发索引任务。
        表头和行计数通过 finished_indexing 信号通知；失败通过 failed。
        """
        # 取消尚未完成的旧索引任务
        if self._index_worker is not None and self._index_worker.isRunning():
            self._index_worker.requestInterruption()
            self._index_worker.quit()
            self._index_worker.wait(2000)

        self.beginResetModel()
        self._filepath = filepath
        try:
            self._file_size = filepath.stat().st_size
        except OSError:
            self._file_size = 0
        self._rows = []
        self._line_offsets = []
        self._byte_offsets = []
        self._headers = []
        self._total_rows = 0
        self.endResetModel()

        self.indexing_started.emit()

        self._index_worker = CsvIndexWorker(filepath, parent=self)
        self._index_worker.finished_indexing.connect(self._on_indexed)
        self._index_worker.failed.connect(self._on_index_failed)
        self._index_worker.interrupted.connect(self._on_index_worker_interrupted)
        self._index_worker.finished.connect(self._on_index_worker_finished)
        self._index_worker.start()
        return True

    def _on_indexed(
        self, headers: list, total_rows: int, file_size: float, offsets: list[int] | None = None
    ) -> None:
        """索引完成回调（主线程）。"""
        self.beginResetModel()
        self._headers = headers
        self._total_rows = total_rows
        self._file_size = int(file_size)
        # ★ 2026-11-25：`offsets` 是**字节偏移**（由 CsvIndexWorker 在后台线程建好）。
        # 有了它 `_load_page` 可以直接 seek，不再从文件头逐行跳过。
        self._byte_offsets = list(offsets) if offsets else [0]
        self._line_offsets = list(range(0, total_rows, ROWS_PER_PAGE)) or [0]
        self._load_page(0)
        self.endResetModel()
        self.indexing_finished.emit(total_rows)

    def _on_index_failed(self, message: str) -> None:
        """索引失败回调（主线程）。"""
        self.indexing_failed.emit(message)

    def _on_index_worker_interrupted(self) -> None:
        """索引任务被取消（主线程）。仅当前任务有效。"""
        if self.sender() is not self._index_worker:
            return  # 旧任务迟到的取消信号，已被新任务取代
        self.indexing_cancelled.emit()

    def _on_index_worker_finished(self) -> None:
        """线程结束后清理引用。"""
        sender = self.sender()
        if sender is self._index_worker:
            self._index_worker = None

    def load_file(self, filepath: Path) -> bool:
        """同步加载 CSV 文件（仅读表头和建立偏移索引）。

        保留同步入口以兼容已有调用；新代码应优先使用 load_file_async。
        """
        self.beginResetModel()
        try:
            self._filepath = filepath
            self._file_size = filepath.stat().st_size
            self._rows = []
            self._line_offsets = []
            self._byte_offsets = []

            # ★ 2026-11-25：同步路径也要建**字节偏移**表 —— 否则它和异步路径
            # 行为不一致（异步能 seek、同步只能逐行跳过）。
            # 用二进制读才能可靠 `tell()`；表头单独用 csv 解析。
            with open(filepath, "rb") as raw:
                header_bytes = raw.readline()
                if not header_bytes:
                    self.endResetModel()
                    return False
                self._headers = [
                    str(cell).strip()
                    for cell in next(
                        csv.reader([header_bytes.decode("utf-8-sig", errors="replace")])
                    )
                ]
                self._byte_offsets.append(raw.tell())
                # B9：完整计数（不再截断 10 万行）；内存 O(页数)。
                row_count = 0
                line_in_page = 0
                while True:
                    chunk = raw.readline()
                    if not chunk:
                        break
                    row_count += 1
                    line_in_page += 1
                    if line_in_page % ROWS_PER_PAGE == 0:
                        self._byte_offsets.append(raw.tell())
                self._total_rows = row_count
                # 行号偏移（保留原字段：其它调用方/测试仍在用）
                self._line_offsets = list(range(0, row_count, ROWS_PER_PAGE)) or [0]

            if self._total_rows == 0:
                self._total_rows = 0
            self._line_offsets = self._line_offsets or [0]

            self._load_page(0)
            self.endResetModel()
            return True
        except Exception:
            self.endResetModel()
            return False

    def _load_page(self, page: int) -> None:
        """加载指定页的数据。"""
        if self._filepath is None:
            return

        start = page * ROWS_PER_PAGE
        if self._total_rows > 0 and start >= self._total_rows:
            self._rows = []
            return

        self._rows = []
        try:
            # ★ 2026-11-25：优先用**字节偏移**直接 seek。
            #
            # 原实现每次都从文件头 `for _ in range(start): next(reader)` 逐行跳过，
            # 翻到第 N 页就要解析 N×1000 行 —— 且跑在 GUI 主线程：
            #
            #     第 10 页  3.8 ms
            #     第 50 页 18.0 ms
            #     第 99 页 35.7 ms     <- 与页码线性相关
            #
            # seek 后 0.6 ms 且与页码无关（内容逐字节相同，测试有比对）。
            offset = None
            if page < len(self._byte_offsets):
                offset = self._byte_offsets[page]
            if offset is not None:
                with open(self._filepath, "rb") as raw:
                    raw.seek(offset)
                    text = io.TextIOWrapper(raw, encoding="utf-8-sig", newline="")
                    reader = csv.reader(text)
                    count = 0
                    for row in reader:
                        self._rows.append([str(c).strip() for c in row])
                        count += 1
                        if count >= ROWS_PER_PAGE:
                            break
            else:
                # 兜底：偏移表不可用（旧调用方 / 同步 load_file 路径）。
                with open(self._filepath, encoding="utf-8-sig") as f:
                    reader = csv.reader(f)
                    next(reader, None)  # skip header
                    for _ in range(start):
                        if next(reader, None) is None:
                            break
                    count = 0
                    for row in reader:
                        self._rows.append([str(c).strip() for c in row])
                        count += 1
                        if count >= ROWS_PER_PAGE:
                            break
        except Exception as exc:
            _logger.warning("Failed to load page %d: %s", page, exc)
            self.last_error = str(exc)

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # type: ignore[override]  # noqa: B008
        return len(self._rows)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:  # type: ignore[override]  # noqa: B008
        return len(self._headers)

    def data(
        self,
        index: QModelIndex | QPersistentModelIndex,
        role: int = Qt.ItemDataRole.DisplayRole,
    ) -> Any:
        if not index.isValid():
            return None
        if role == Qt.ItemDataRole.DisplayRole:
            row = index.row()
            col = index.column()
            try:
                return self._rows[row][col]
            except (IndexError, TypeError):
                return ""
        return None

    def headerData(
        self, section: int, orientation: Qt.Orientation, role: int = Qt.ItemDataRole.DisplayRole
    ) -> Any:
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            if section < len(self._headers):
                return self._headers[section]
        return super().headerData(section, orientation, role)

    def canFetchMore(self, index: QModelIndex) -> bool:  # type: ignore[override]
        return False

    @property
    def total_rows(self) -> int:
        return self._total_rows

    @property
    def total_pages(self) -> int:
        if self._total_rows == 0:
            return 0
        return max(1, (self._total_rows + ROWS_PER_PAGE - 1) // ROWS_PER_PAGE)

    @property
    def headers(self) -> list[str]:
        return list(self._headers)

    @property
    def page_rows(self) -> list[list[str]]:
        """当前已加载分页的数据行快照（供字段完整度统计使用）。"""
        return [list(row) for row in self._rows]

    def go_to_page(self, page: int) -> None:
        """跳转到指定页码。"""
        self.last_error = None
        total = self.total_pages
        if total == 0:
            return
        page = max(0, min(page, total - 1))
        self.beginResetModel()
        self._load_page(page)
        self.endResetModel()


class ExportThread(QThread):
    """Excel 导出后台线程。

    P2-4：通过 ProgressTracker 阶段权重 + 子项计数驱动进度，
    保持旧式 progress(int) 信号发出（旧消费者不受影响）。
    """

    progress = Signal(int)
    finished_signal = Signal(bool, str)
    unified_progress = Signal(object)  # P2-4：TaskProgressEvent

    def __init__(self, filepath: Path, output_path: Path) -> None:
        super().__init__()
        self._filepath = filepath
        self._output_path = output_path

    def run(self) -> None:
        try:
            from omnicrawler.services.progress import ProgressTracker, StageSpec

            if self.isInterruptionRequested():
                return
            import openpyxl

            # P2-4：只做"估算总行数→三阶段进度"接入，保持流式读写，不将 CSV 全量加载进内存
            total_rows = 0
            try:
                with open(self._filepath, encoding="utf-8-sig") as fh:
                    for _line in fh:
                        total_rows += 1
                if total_rows > 1:
                    total_rows -= 1  # 去掉表头
            except OSError:
                total_rows = 0

            # P2-4：旧式 progress(int) 保持原节奏（每 10000 行一次，百分 = total//1000 clamp 99）
            # 以兼容旧消费者的精确断言；新式 unified_progress 走 ProgressTracker 阶段权重 + ETA。
            tracker = ProgressTracker(
                stages=[
                    StageSpec("process", weight=8.0, display_name=_("写入 Excel"), has_items=True),
                    StageSpec("save_file", weight=2.0, display_name=_("保存文件")),
                ],
                on_event=self.unified_progress.emit,
            )
            tracker.start()

            wb = openpyxl.Workbook()
            ws = wb.active
            ws.title = "Sheet1"
            sheet_row = 1
            max_rows_per_sheet = 500000
            total = 0

            tracker.begin_stage("process", expected_items=total_rows or 1)
            with open(self._filepath, encoding="utf-8-sig") as f:
                reader = csv.reader(f)
                headers = next(reader, None)
                if headers:
                    for col, h in enumerate(headers, 1):
                        ws.cell(row=1, column=col, value=h)
                    sheet_row = 2

                for row in reader:
                    if self.isInterruptionRequested():
                        tracker.cancel()
                        return
                    total += 1
                    if sheet_row > max_rows_per_sheet:
                        sheet_name = f"Sheet{len(wb.sheetnames) + 1}"
                        ws = wb.create_sheet(sheet_name)
                        sheet_row = 1
                        assert headers is not None
                        for col, h in enumerate(headers, 1):
                            ws.cell(row=1, column=col, value=h)
                        sheet_row = 2
                    for col, val in enumerate(row, 1):
                        ws.cell(row=sheet_row, column=col, value=excel_safe(val))
                    sheet_row += 1
                    if total % 1000 == 0:
                        tracker.set_item_progress(total)
                    # P2-4：旧式 progress(int) 按 10000 行粒度发出（兼容旧消费方精确断言）
                    if total % 10000 == 0:
                        self.progress.emit(min(total // 1000, 99))
            tracker.end_stage("process")

            if self.isInterruptionRequested():
                tracker.cancel()
                return
            tracker.begin_stage("save_file")
            wb.save(str(self._output_path))
            tracker.finish()
            if not self.isInterruptionRequested():
                self.finished_signal.emit(True, str(self._output_path))
        except Exception as e:  # noqa: BLE001
            if not self.isInterruptionRequested():
                self.finished_signal.emit(False, str(e))


def compute_page_completeness(
    headers: Sequence[str], rows: Sequence[Sequence[str]]
) -> dict[str, Any]:
    """计算当前页各列的非空填充率，立即高亮空值列以暴露失效选择器。"""
    row_total = len(rows)
    col_total = len(headers)
    if row_total == 0 or col_total == 0:
        return {
            "row_count": row_total,
            "column_count": col_total,
            "overall_pct": 0.0,
            "columns": [],
            "incomplete_columns": [],
            "zero_fill_columns": [],
        }

    columns: list[dict[str, Any]] = []
    incomplete: list[dict[str, Any]] = []
    zero_fill: list[str] = []
    filled_cells = 0

    for col_idx, raw_header in enumerate(headers):
        col_name = str(raw_header).strip() or f"col_{col_idx + 1}"
        filled = 0
        for row in rows:
            cell = str(row[col_idx]).strip() if col_idx < len(row) else ""
            if cell != "":
                filled += 1
        empty = row_total - filled
        filled_cells += filled
        pct = round((filled / row_total) * 100.0, 1)
        info = {
            "header": col_name,
            "filled": filled,
            "empty": empty,
            "fill_pct": pct,
        }
        columns.append(info)
        if empty > 0:
            incomplete.append(info)
        if filled == 0:
            zero_fill.append(col_name)

    total_cells = row_total * col_total
    overall_pct = round((filled_cells / total_cells) * 100.0, 1)
    return {
        "row_count": row_total,
        "column_count": col_total,
        "overall_pct": overall_pct,
        "columns": columns,
        "incomplete_columns": incomplete,
        "zero_fill_columns": zero_fill,
    }


def format_completeness_summary(stats: Mapping[str, Any]) -> tuple[str, str]:
    """将完整度统计格式化为 (摘要文案, 状态语义名)。"""
    row_total = int(stats.get("row_count", 0))
    col_total = int(stats.get("column_count", 0))
    if row_total == 0 or col_total == 0:
        return _("字段完整度：暂无当前页数据"), "muted"

    overall_pct = float(stats.get("overall_pct", 0.0))
    incomplete = list(stats.get("incomplete_columns") or [])
    zero_fill = list(stats.get("zero_fill_columns") or [])
    if not incomplete:
        text = _("字段完整度：100%（当前页 {0} 行 · {1} 列全部非空）").format(
            row_total, col_total
        )
        return text, "success"

    details: list[str] = []
    for item in incomplete[:4]:
        header = str(item.get("header", ""))
        filled = int(item.get("filled", 0))
        details.append(f"{header} ({filled}/{row_total})")
    joined = "、".join(details)
    if len(incomplete) > 4:
        joined += _(" 等 {0} 列").format(len(incomplete))
    status = "danger" if zero_fill else "warning"
    text = _("字段完整度：{0}% · 含空值列：{1}").format(overall_pct, joined)
    return text, status


def build_row_dict(headers: Sequence[str], row_values: Sequence[str]) -> dict[str, str]:
    """把表头与单行单元格拼成有序字典（供一键复制 JSON 与结构化预览使用）。"""
    result: dict[str, str] = {}
    for idx, raw_header in enumerate(headers):
        key = str(raw_header).strip() or f"col_{idx + 1}"
        val = str(row_values[idx]) if idx < len(row_values) else ""
        result[key] = val
    return result


def format_row_inspector_text(
    row_dict: Mapping[str, str], evidence_text: str = ""
) -> str:
    """将选中行渲染为清晰的多行键值检查文本，并附带原始证据信息。"""
    lines: list[str] = [_("【当前行字段检查】")]
    empty_marker = _("（空）")
    for key, value in row_dict.items():
        if value == "":
            lines.append(f"• {key}: {empty_marker}")
        elif "\n" in value:
            indented = "\n    ".join(value.splitlines())
            lines.append(f"• {key}:\n    {indented}")
        else:
            lines.append(f"• {key}: {value}")
    if evidence_text.strip():
        lines.append("")
        lines.append(_("【关联证据 / 原始记录】"))
        lines.append(evidence_text.strip())
    return "\n".join(lines)


class ResultTable(QWidget):
    """结果查看页 — CSV 表格 + 分页 + 导出。

    CSV 索引与 JSONL 证据查找均通过后台线程执行，避免大文件阻塞 UI。
    """

    # 用户请求在证据查看器中打开某条记录
    record_selected_for_review = Signal(object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAccessibleName(_("结果查看"))
        self._model = CsvStreamModel(self)
        self._model.indexing_started.connect(self._on_indexing_started)
        self._model.indexing_finished.connect(self._on_indexing_finished)
        self._model.indexing_failed.connect(self._on_indexing_failed)
        self._model.indexing_cancelled.connect(self._on_indexing_cancelled)

        self._proxy = QSortFilterProxyModel(self)
        self._proxy.setSourceModel(self._model)
        self._proxy.setFilterCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self._proxy.setFilterKeyColumn(-1)
        self._current_page = 0
        #: 列宽是否已按当前文件量过（翻页不重复量，见 _fit_columns_once）
        self._columns_fitted = False
        self._export_thread: ExportThread | None = None
        self._evidence_worker: JsonlSearchWorker | None = None
        self._current_evidence_record: dict | None = None  # 当前选中的 JSONL 记录

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)

        # 信息栏
        info_layout = QHBoxLayout()
        self._info_label = QLabel(_("未加载数据"))
        info_layout.addWidget(self._info_label)
        info_layout.addStretch()

        self._search = QLineEdit()
        self._search.setPlaceholderText(_("搜索当前页全部字段…"))
        self._search.setClearButtonEnabled(True)
        self._search.textChanged.connect(self._apply_filter)
        info_layout.addWidget(self._search)

        # 刷新按钮
        refresh_btn = QPushButton(_("手动刷新"))
        refresh_btn.clicked.connect(self.refresh)
        info_layout.addWidget(refresh_btn)

        # 导出按钮
        export_btn = QPushButton(_("导出 Excel"))
        export_btn.clicked.connect(self._export_excel)
        info_layout.addWidget(export_btn)

        export_md_btn = QPushButton(_("导出 Markdown"))
        export_md_btn.setToolTip(_("将完整抓取结果导出为 Markdown 文件"))
        export_md_btn.clicked.connect(self._export_markdown)
        info_layout.addWidget(export_md_btn)

        export_filtered_btn = QPushButton(_("导出筛选结果"))
        export_filtered_btn.clicked.connect(self._export_filtered_csv)
        info_layout.addWidget(export_filtered_btn)

        self._export_selected_btn = QPushButton(_("导出选中行"))
        self._export_selected_btn.setAccessibleName(_("导出当前选中的表格行"))
        self._export_selected_btn.setToolTip(_("将选中行导出为 CSV（快捷键 Ctrl+Shift+E）"))
        self._export_selected_btn.clicked.connect(lambda: self.export_selected_rows_csv())
        info_layout.addWidget(self._export_selected_btn)

        self._export_selected_shortcut = QShortcut(QKeySequence("Ctrl+Shift+E"), self)
        self._export_selected_shortcut.activated.connect(lambda: self.export_selected_rows_csv())

        # 打开文件夹按钮
        open_folder_btn = QPushButton(_("打开文件夹"))
        open_folder_btn.clicked.connect(self._open_folder)
        info_layout.addWidget(open_folder_btn)

        layout.addLayout(info_layout)

        # 字段完整度摘要条（当前页列填充率检查）
        self._completeness_label = QLabel(_("字段完整度：暂无当前页数据"))
        self._completeness_label.setObjectName("completenessSummary")
        self._completeness_label.setAccessibleName(_("字段完整度摘要"))
        self._completeness_label.setProperty("status", "muted")
        self._completeness_label.setWordWrap(True)
        layout.addWidget(self._completeness_label)

        # 异步加载进度条
        self._loading_bar = QProgressBar()
        self._loading_bar.setRange(0, 0)  # 不确定模式
        self._loading_bar.setTextVisible(False)
        self._loading_bar.setVisible(False)
        self._loading_bar.setMaximumHeight(4)
        layout.addWidget(self._loading_bar)

        # 表格
        self._table = QTableView()
        self._table.setModel(self._proxy)
        header = self._table.horizontalHeader()
        assert header is not None
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setSectionsMovable(True)
        header.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        header.customContextMenuRequested.connect(self._show_header_column_menu)
        self._table.setAlternatingRowColors(True)
        self._table.setSortingEnabled(False)  # 流式加载不支持全局排序
        self._evidence = QPlainTextEdit()
        self._evidence.setReadOnly(True)
        self._evidence.setPlaceholderText(_("选择一条记录后，这里显示原始数据、字段证据和质量信息。"))

        # 证据面板容器：证据文本 + 快捷复制动作 + 打开按钮
        evidence_container = QWidget()
        evidence_container_layout = QVBoxLayout(evidence_container)
        evidence_container_layout.setContentsMargins(0, 0, 0, 0)
        evidence_container_layout.setSpacing(6)
        evidence_container_layout.addWidget(self._evidence, 1)

        copy_actions_layout = QHBoxLayout()
        self._copy_row_json_btn = QPushButton(_("复制当前行 JSON"))
        self._copy_row_json_btn.setAccessibleName(_("复制当前选中行的 JSON 数据"))
        self._copy_row_json_btn.setEnabled(False)
        self._copy_row_json_btn.clicked.connect(lambda: self.copy_selected_row_json())
        copy_actions_layout.addWidget(self._copy_row_json_btn)

        self._copy_cell_btn = QPushButton(_("复制选中单元格"))
        self._copy_cell_btn.setAccessibleName(_("复制当前选中单元格内容"))
        self._copy_cell_btn.setEnabled(False)
        self._copy_cell_btn.clicked.connect(lambda: self.copy_selected_cell())
        copy_actions_layout.addWidget(self._copy_cell_btn)
        evidence_container_layout.addLayout(copy_actions_layout)

        self._open_evidence_btn = QPushButton(_("在证据查看器中打开 →"))
        self._open_evidence_btn.setEnabled(False)
        self._open_evidence_btn.clicked.connect(self._open_in_evidence_view)
        evidence_container_layout.addWidget(self._open_evidence_btn)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self._table)
        splitter.addWidget(evidence_container)
        splitter.setStretchFactor(0, 4)
        splitter.setStretchFactor(1, 2)
        layout.addWidget(splitter)
        selection_model = self._table.selectionModel()
        assert selection_model is not None
        selection_model.currentRowChanged.connect(self._show_evidence)
        selection_model.currentChanged.connect(self._on_current_cell_changed)

        # 分页控件
        pagination_layout = QHBoxLayout()

        self._first_btn = QPushButton("<<")
        self._first_btn.clicked.connect(self._go_first)
        pagination_layout.addWidget(self._first_btn)

        self._prev_btn = QPushButton("<")
        self._prev_btn.clicked.connect(self._go_prev)
        pagination_layout.addWidget(self._prev_btn)

        self._page_input = QSpinBox()
        self._page_input.setMinimum(1)
        self._page_input.setMaximum(1)
        self._page_input.valueChanged.connect(self._go_to_page)
        pagination_layout.addWidget(self._page_input)

        self._total_label = QLabel("/ 1")
        pagination_layout.addWidget(self._total_label)

        self._next_btn = QPushButton(">")
        self._next_btn.clicked.connect(self._go_next)
        pagination_layout.addWidget(self._next_btn)

        self._last_btn = QPushButton(">>")
        self._last_btn.clicked.connect(self._go_last)
        pagination_layout.addWidget(self._last_btn)

        pagination_layout.addStretch()
        layout.addLayout(pagination_layout)

        self._filepath: Path | None = None
        self._evidence_cache: dict[str, dict] = {}
        self._showing_data = False
        self._selected_row_dict: dict[str, str] | None = None

    # ---- 公共 API ----

    @property
    def current_path(self) -> Path | None:
        """当前已加载的 CSV 路径（无则 None）——供主窗口刷新结果页使用（§A-34）。"""
        return self._filepath

    def update_completeness_summary(self) -> dict[str, Any]:
        """重算当前页各列非空填充率并更新摘要条。"""
        stats = compute_page_completeness(self._model.headers, self._model.page_rows)
        text, status = format_completeness_summary(stats)
        self._completeness_label.setText(text)
        if self._completeness_label.property("status") != status:
            self._completeness_label.setProperty("status", status)
            repolish_widget(self._completeness_label)
        return stats

    def selected_row_dict(self) -> dict[str, str] | None:
        """返回当前选中行的键值字典；未选中时返回 None。"""
        proxy_index = self._table.currentIndex()
        if proxy_index.isValid():
            source_index = self._proxy.mapToSource(proxy_index)
            return self._row_dict_for_source_row(source_index.row())
        if self._selected_row_dict is not None:
            return dict(self._selected_row_dict)
        return None

    def _row_dict_for_source_row(self, source_row: int) -> dict[str, str] | None:
        headers = self._model.headers
        if source_row < 0 or source_row >= self._model.rowCount() or not headers:
            return None
        values = [
            str(self._model.data(self._model.index(source_row, col)) or "")
            for col in range(len(headers))
        ]
        return build_row_dict(headers, values)

    def copy_selected_row_json(self) -> str:
        """复制当前选中行为格式化 JSON 字符串并返回（未选中返回空串）。"""
        row_data = self.selected_row_dict()
        if row_data is None:
            return ""
        payload = json.dumps(row_data, ensure_ascii=False, indent=2)
        clipboard = QApplication.clipboard()
        if clipboard is not None:
            clipboard.setText(payload)
        self._info_label.setText(_("已复制当前行 JSON 到剪贴板"))
        return payload

    def copy_selected_cell(self) -> str:
        """复制当前选中单元格内容并返回（未选中返回空串）。"""
        proxy_index = self._table.currentIndex()
        if not proxy_index.isValid():
            return ""
        value = str(self._proxy.data(proxy_index) or "")
        clipboard = QApplication.clipboard()
        if clipboard is not None:
            clipboard.setText(value)
        self._info_label.setText(_("已复制选中单元格内容到剪贴板"))
        return value

    def _on_current_cell_changed(
        self, current: QModelIndex, _previous: QModelIndex
    ) -> None:
        valid = current.isValid()
        self._copy_cell_btn.setEnabled(valid)
        self._copy_row_json_btn.setEnabled(valid)
        if valid and self._model.headers and 0 <= current.column() < len(self._model.headers):
            col_idx = current.column()
            col_name = self._model.headers[col_idx]
            vals = [
                str(row[col_idx]).strip()
                for row in self._model.page_rows
                if col_idx < len(row) and str(row[col_idx]).strip()
            ]
            total = len(self._model.page_rows)
            unique_cnt = len(set(vals))
            self._info_label.setText(
                _("列「{0}」：非空 {1}/{2} · 唯一值 {3} 个").format(
                    col_name, len(vals), total, unique_cnt
                )
            )

    def set_column_hidden(self, column: int, hidden: bool) -> bool:
        """切换单列显隐（至少保留 1 列可见，避免整表被隐藏成空白）。"""
        total_cols = len(self._model.headers)
        if column < 0 or column >= total_cols:
            return False
        if hidden:
            visible_count = sum(
                1 for idx in range(total_cols) if not self._table.isColumnHidden(idx)
            )
            if visible_count <= 1:
                return False
        self._table.setColumnHidden(column, hidden)
        return True

    def show_all_columns(self) -> None:
        """恢复全部列为可见状态。"""
        for idx in range(len(self._model.headers)):
            self._table.setColumnHidden(idx, False)

    def _show_header_column_menu(self, pos: QPoint) -> None:
        headers = self._model.headers
        if not headers:
            return
        menu = QMenu(self)
        menu.setAccessibleName(_("表头列显隐菜单"))
        for idx, name in enumerate(headers):
            act = QAction(name, menu)
            act.setCheckable(True)
            act.setChecked(not self._table.isColumnHidden(idx))
            act.toggled.connect(lambda checked, c=idx: self.set_column_hidden(c, not checked))
            menu.addAction(act)
        menu.addSeparator()
        reset_act = QAction(_("显示全部列"), menu)
        reset_act.triggered.connect(self.show_all_columns)
        menu.addAction(reset_act)
        header = self._table.horizontalHeader()
        if header is not None:
            menu.exec(header.mapToGlobal(pos))

    def export_selected_rows_csv(self, output_path: Path | None = None) -> int:
        """将当前选中的表格行导出为 CSV（带公式注入防护），返回写出的数据行数。"""
        headers = self._model.headers
        if not headers:
            if output_path is None:
                QMessageBox.information(self, _("提示"), _("当前没有可导出的表格数据"))
            return 0
        sel_model = self._table.selectionModel()
        proxy_rows: list[int] = []
        if sel_model is not None:
            proxy_rows = sorted({idx.row() for idx in sel_model.selectedIndexes()})
        if not proxy_rows and self._table.currentIndex().isValid():
            proxy_rows = [self._table.currentIndex().row()]
        if not proxy_rows:
            if output_path is None:
                QMessageBox.information(self, _("提示"), _("请先在结果表中选中至少一行记录"))
            return 0

        if output_path is None:
            default_stem = (self._filepath.stem if self._filepath else "records") + "_selected.csv"
            chosen, _flt = QFileDialog.getSaveFileName(
                self, _("导出选中行"), default_stem, _("CSV 文件 (*.csv)")
            )
            if not chosen:
                return 0
            target = Path(chosen)
        else:
            target = Path(output_path)

        visible_cols = [
            idx for idx in range(len(headers)) if not self._table.isColumnHidden(idx)
        ] or list(range(len(headers)))

        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow([excel_safe(headers[c]) for c in visible_cols])
            for p_row in proxy_rows:
                writer.writerow(
                    [
                        excel_safe(str(self._proxy.data(self._proxy.index(p_row, c)) or ""))
                        for c in visible_cols
                    ]
                )
        if output_path is None:
            QMessageBox.information(
                self,
                _("导出成功"),
                _("已导出 {0} 行选中记录到：{1}").format(len(proxy_rows), target),
            )
        return len(proxy_rows)

    def load_csv(self, filepath: Path) -> bool:
        """异步加载 CSV 文件。

        立即返回 True 表示已派发索引任务。
        表头与行计数到位后通过 indexing_finished 信号回调更新 UI。
        """
        self._filepath = filepath
        if not filepath.is_file():
            self._info_label.setText(_("未找到结果文件，请确认爬虫已正确输出。点击【手动刷新】重试。"))
            return False

        self._current_page = 0
        self._info_label.setText(_("正在加载结果…"))
        self._model.load_file_async(filepath)
        return True

    # ---- 异步回调（在主线程执行） ----

    def _on_indexing_started(self) -> None:
        """索引任务开始：显示加载指示。"""
        self._loading_bar.setVisible(True)
        self._info_label.setText(_("正在索引 CSV 行…"))
        self._showing_data = False

    def _on_indexing_finished(self, total_rows: int) -> None:
        """索引任务完成：更新信息栏与分页控件。"""
        self._loading_bar.setVisible(False)
        total = self._model.total_rows
        self._info_label.setText(
            _("总行数（近似）: {:,} 行").format(total)
        )
        self._update_pagination()
        # ★ 2026-11-25：这里是**新文件载入后**的唯一一次量列宽。
        # 复位再量：第二份文件进来时重新按它的内容算宽度；
        # 量完置位，后续翻页不再重复（见 `_fit_columns_once`）。
        self._reset_columns_fitted()
        self._fit_columns_once()
        if total_rows > 0 and not self._showing_data:
            self._fade_in_widget(self._table)
            self._showing_data = True

    def _fade_in_widget(self, widget: QWidget) -> None:
        """对指定控件播放 200ms 淡入动画。"""
        effect = QGraphicsOpacityEffect(widget)
        effect.setOpacity(0.0)
        widget.setGraphicsEffect(effect)
        anim = QPropertyAnimation(effect, b"opacity", widget)
        anim.setDuration(200)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        anim.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)

    def _on_indexing_failed(self, message: str) -> None:
        """索引任务失败：提示错误。"""
        self._loading_bar.setVisible(False)
        self._info_label.setText(_("加载失败：{0}").format(message))

    def _on_indexing_cancelled(self) -> None:
        """索引任务被取消：复位加载指示。"""
        self._loading_bar.setVisible(False)
        self._info_label.setText(_("索引已取消"))

    def _apply_filter(self, text: str) -> None:
        self._proxy.setFilterRegularExpression(QRegularExpression(QRegularExpression.escape(text)))
        self._info_label.setText(
            _("当前页显示 {0} / {1} 行").format(self._proxy.rowCount(), self._model.rowCount())
        )

    def _render_inspector(self, evidence_text: str = "") -> None:
        if self._selected_row_dict is not None:
            self._evidence.setPlainText(
                format_row_inspector_text(self._selected_row_dict, evidence_text)
            )
        else:
            self._evidence.setPlainText(evidence_text)

    def _show_evidence(self, proxy_index: QModelIndex, _previous: QModelIndex) -> None:
        if not proxy_index.isValid():
            self._selected_row_dict = None
            self._copy_row_json_btn.setEnabled(False)
            self._copy_cell_btn.setEnabled(False)
            return
        source_index = self._proxy.mapToSource(proxy_index)
        self._selected_row_dict = self._row_dict_for_source_row(source_index.row())
        self._copy_row_json_btn.setEnabled(self._selected_row_dict is not None)
        self._copy_cell_btn.setEnabled(True)
        if not self._filepath:
            self._render_inspector()
            self._open_evidence_btn.setEnabled(False)
            self._current_evidence_record = None
            return
        try:
            record_column = self._model.headers.index("record_id")
        except ValueError:
            self._render_inspector(_("当前 CSV 不包含 record_id，无法关联字段证据。"))
            self._open_evidence_btn.setEnabled(False)
            self._current_evidence_record = None
            return
        record_id = str(self._model.data(self._model.index(source_index.row(), record_column)) or "")
        if not record_id:
            self._render_inspector()
            self._open_evidence_btn.setEnabled(False)
            self._current_evidence_record = None
            return

        # 命中缓存则立即展示
        record = self._evidence_cache.get(record_id)
        if record is not None:
            self._render_inspector(json.dumps(record, ensure_ascii=False, indent=2, default=str))
            self._current_evidence_record = record
            self._open_evidence_btn.setEnabled(True)
            return

        # 异步查找时暂时禁用按钮，但立即显示当前行结构化预览
        self._open_evidence_btn.setEnabled(False)
        self._current_evidence_record = None

        jsonl = self._filepath.with_name("records.jsonl")
        if not jsonl.is_file():
            self._render_inspector(_(f"记录 {record_id}\n未找到配套 records.jsonl 证据文件。"))
            return

        # 异步查找证据，避免大 JSONL 阻塞 UI
        self._render_inspector(_(f"正在查找记录 {record_id} 的证据…"))
        self._evidence.setPlaceholderText(_(f"正在查找记录 {record_id} 的证据…"))
        if self._evidence_worker is not None and self._evidence_worker.isRunning():
            self._evidence_worker.requestInterruption()
            self._evidence_worker.quit()
            self._evidence_worker.wait(1000)

        self._evidence_worker = JsonlSearchWorker(jsonl, record_id, parent=self)
        self._evidence_worker.found.connect(self._on_evidence_found)
        self._evidence_worker.not_found.connect(self._on_evidence_not_found)
        self._evidence_worker.failed.connect(self._on_evidence_failed)
        self._evidence_worker.interrupted.connect(self._on_evidence_interrupted)
        self._evidence_worker.finished.connect(self._on_evidence_worker_finished)
        self._evidence_worker.start()

    def _on_evidence_found(self, record_id: str, record: dict) -> None:
        """证据查找成功回调（主线程）。"""
        self._evidence_cache[record_id] = record
        self._current_evidence_record = record
        self._evidence.setPlaceholderText(_("选择一条记录后，这里显示原始数据、字段证据和质量信息。"))
        self._render_inspector(json.dumps(record, ensure_ascii=False, indent=2, default=str))
        self._open_evidence_btn.setEnabled(True)

    def _on_evidence_not_found(self, record_id: str) -> None:
        """证据查找未命中回调（主线程）。"""
        self._current_evidence_record = None
        self._open_evidence_btn.setEnabled(False)
        self._evidence.setPlaceholderText(_("选择一条记录后，这里显示原始数据、字段证据和质量信息。"))
        self._render_inspector(_(f"记录 {record_id}\n未找到配套 records.jsonl 证据文件。"))

    def _on_evidence_failed(self, message: str) -> None:
        """证据查找失败回调（主线程）。"""
        self._current_evidence_record = None
        self._open_evidence_btn.setEnabled(False)
        self._evidence.setPlaceholderText(_("选择一条记录后，这里显示原始数据、字段证据和质量信息。"))
        self._render_inspector(_(f"证据加载失败：{message}"))

    def _on_evidence_interrupted(self) -> None:
        """证据查找被取消（主线程）。仅当前任务有效。"""
        if self.sender() is not self._evidence_worker:
            return  # 旧任务迟到的取消信号，已被新任务取代
        self._current_evidence_record = None
        self._open_evidence_btn.setEnabled(False)
        self._evidence.setPlaceholderText(_("选择一条记录后，这里显示原始数据、字段证据和质量信息。"))
        self._render_inspector(_("证据查找已取消"))

    def _on_evidence_worker_finished(self) -> None:
        """线程结束后清理引用。"""
        sender = self.sender()
        if sender is self._evidence_worker:
            self._evidence_worker = None

    def _open_in_evidence_view(self) -> None:
        """触发信号：在证据查看器中打开当前记录。"""
        if self._current_evidence_record is not None:
            self.record_selected_for_review.emit(self._current_evidence_record)

    def _export_filtered_csv(self) -> None:
        if self._proxy.rowCount() == 0:
            QMessageBox.information(self, _("导出筛选结果"), _("当前没有可导出的筛选结果。"))
            return
        output_path, _selected_filter = QFileDialog.getSaveFileName(
            self, _("导出筛选结果"), "filtered_records.csv", _("CSV 文件 (*.csv)")
        )
        if not output_path:
            return
        with Path(output_path).open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(self._model.headers)
            for row in range(self._proxy.rowCount()):
                writer.writerow(
                    [self._proxy.data(self._proxy.index(row, column)) for column in range(self._proxy.columnCount())]
                )
        QMessageBox.information(self, _("导出完成"), _(f"已导出 {self._proxy.rowCount()} 行到：\n{output_path}"))

    def _export_markdown(self) -> None:
        """导出完整结果为 Markdown 文件。"""
        if not self._filepath or not self._filepath.is_file():
            QMessageBox.information(self, _("提示"), _("请先加载 CSV 文件"))
            return

        output_path, _selected_filter = QFileDialog.getSaveFileName(
            self, _("导出 Markdown"), self._filepath.stem + ".md",
            _("Markdown 文件 (*.md)"),
        )
        if not output_path:
            return

        # S3.1.1：Markdown 导出移入后台线程（大表不冻结界面）
        from ..core.background_worker import BackgroundWorker, run_worker

        filepath = self._filepath
        jsonl = filepath.with_name("records.jsonl")
        target = Path(output_path)

        class _MarkdownExportWorker(BackgroundWorker):
            def work(self) -> str:
                from omnicrawler.services.markdown_export import export_results_markdown

                export_results_markdown(
                    csv_path=filepath,
                    jsonl_path=jsonl if jsonl.is_file() else None,
                    output_path=target,
                    include_evidence=True,
                )
                return str(target)

        run_worker(
            _MarkdownExportWorker(),
            on_succeeded=lambda path: QMessageBox.information(
                self, _("导出成功"), _(f"已导出到: {path}")
            ),
            on_failed=lambda error: QMessageBox.critical(self, _("导出失败"), error),
        )

    def refresh(self) -> None:
        """手动刷新。"""
        if self._filepath and self._filepath.is_file():
            self.load_csv(self._filepath)

    # ---- 分页 ----

    def _update_pagination(self) -> None:
        """更新分页控件状态。"""
        total_pages = self._model.total_pages
        current = self._current_page + 1  # 1-based display

        self._page_input.blockSignals(True)
        self._page_input.setMinimum(1)
        self._page_input.setMaximum(max(1, total_pages))
        self._page_input.setValue(current)
        self._page_input.blockSignals(False)

        self._total_label.setText(f"/ {max(1, total_pages)}")

        self._first_btn.setEnabled(self._current_page > 0)
        self._prev_btn.setEnabled(self._current_page > 0)
        self._next_btn.setEnabled(self._current_page < total_pages - 1)
        self._last_btn.setEnabled(self._current_page < total_pages - 1)
        self.update_completeness_summary()

    def _warn_if_page_error(self) -> None:
        """A17：翻页加载失败时给用户可见提示（不再静默）。"""
        if self._model.last_error:
            from ..widgets.toast import ToastManager
            ToastManager.instance().warning(_("翻页失败: {0}").format(self._model.last_error))
            self._model.last_error = None

    def _fit_columns_once(self) -> None:
        """按当前页内容自适应列宽 —— **每份文件只做一次**。

        ★ 2026-11-25：原先 5 个翻页入口（首/上/下/末/跳转）各自调
        `resizeColumnsToContents()`，而它要扫描当前页全部 1000 行算宽度。
        实测 **67.4 ms/次**，占整次翻页的 93%（翻页总耗时 72 ms）。

        列宽与「第几页」无关 —— 同一份 CSV 的列语义不变，用户翻到第 2 页
        不需要重新量一遍。所以只在新文件载入后做一次；翻页不再做。

        代价：如果某一页出现特别长的值，该列不会自动变宽（用户可手动拖）。
        这是刻意取舍 —— 翻页从 72 ms 降到 5 ms，而「某页有超长值」是少数情况。
        """
        if self._columns_fitted:
            return
        self._columns_fitted = True
        self._table.resizeColumnsToContents()

    def _reset_columns_fitted(self) -> None:
        """新文件载入时复位，让下一份文件重新量一次列宽。"""
        self._columns_fitted = False

    def _go_first(self) -> None:
        if self._current_page > 0:
            self._current_page = 0
            self._model.go_to_page(self._current_page)
            self._update_pagination()
            self._fit_columns_once()
            self._warn_if_page_error()

    def _go_prev(self) -> None:
        if self._current_page > 0:
            self._current_page -= 1
            self._model.go_to_page(self._current_page)
            self._update_pagination()
            self._fit_columns_once()
            self._warn_if_page_error()

    def _go_next(self) -> None:
        if self._current_page < self._model.total_pages - 1:
            self._current_page += 1
            self._model.go_to_page(self._current_page)
            self._update_pagination()
            self._fit_columns_once()
            self._warn_if_page_error()

    def _go_last(self) -> None:
        last = max(0, self._model.total_pages - 1)
        if self._current_page != last:
            self._current_page = last
            self._model.go_to_page(self._current_page)
            self._update_pagination()
            self._fit_columns_once()
            self._warn_if_page_error()

    def _go_to_page(self, page: int) -> None:
        """页码输入跳转。"""
        # 已通过 setMaximum 限制范围
        actual = page - 1  # 转为 0-based
        if actual != self._current_page and 0 <= actual < self._model.total_pages:
            self._current_page = actual
            self._model.go_to_page(self._current_page)
            self._update_pagination()
            self._fit_columns_once()

    # ---- 导出 ----

    def _export_excel(self) -> None:
        """导出为 Excel。"""
        if not self._filepath or not self._filepath.is_file():
            QMessageBox.information(self, _("提示"), _("请先加载 CSV 文件"))
            return

        output_path, _selected_filter = QFileDialog.getSaveFileName(
            self, _("导出 Excel"), self._filepath.stem + ".xlsx",
            _("Excel 文件 (*.xlsx)"),
        )
        if not output_path:
            return

        progress = QProgressDialog(_("正在导出 Excel..."), _("取消"), 0, 0, self)
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.show()

        self._export_thread = ExportThread(self._filepath, Path(output_path))
        self._export_thread.setParent(self)
        # S3.1.9：取消按钮真正中断导出线程
        progress.canceled.connect(self._export_thread.requestInterruption)
        self._export_thread.finished_signal.connect(
            lambda ok, msg: self._on_export_finished(ok, msg, progress)
        )
        self._export_thread.start()

    def _on_export_finished(self, ok: bool, msg: str, progress: QProgressDialog) -> None:
        """导出完成处理。"""
        progress.close()
        if ok:
            QMessageBox.information(self, _("导出成功"), _(f"已导出到: {msg}"))
        else:
            QMessageBox.critical(self, _("导出失败"), msg)

    def _open_folder(self) -> None:
        """打开结果文件夹。"""
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices

        if self._filepath and self._filepath.parent.is_dir():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._filepath.parent)))
        else:
            QMessageBox.information(self, _("提示"), _("未找到结果目录"))
