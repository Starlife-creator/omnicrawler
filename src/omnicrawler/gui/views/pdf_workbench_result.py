"""PdfWorkbenchView 的「结果与收尾」域 —— 由 pdf_workbench.py 抽出的 Mixin（P1-3 第三批）。

涵盖流水线执行期间与结束后的全部回调：
- 进度：``_on_stage_started`` / ``_on_stage_finished`` / ``_on_document_progress`` / ``_on_warnings``
- 终态：``_on_done`` / ``_on_failed`` / ``_cancel``（取消编排）/ ``closeEvent``（关窗停线程）
- 输出与复位：``_open_output_dir`` / ``_open_excel`` / ``_reset``

以 Mixin 形式保留 ``self`` 语义，宿主属性与全部调用点不变。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import QUrl, Slot
from PySide6.QtGui import QCloseEvent, QDesktopServices

from ..i18n import _
from ..widgets.toast import ToastManager
from .pdf_workbench_logic import _collect_failures

if TYPE_CHECKING:
    from PySide6.QtWidgets import (
        QCheckBox,
        QComboBox,
        QGroupBox,
        QLabel,
        QProgressBar,
        QPushButton,
        QTextEdit,
        QWidget,
    )

    from .pdf_workbench_worker import _PdfPipelineWorker

    # 类型检查期把宿主视作 QWidget；运行期仍为 object，不改变 PdfWorkbenchView 的 MRO。
    _Base = QWidget
else:
    _Base = object


class PdfResultMixin(_Base):
    """结果与收尾域：进度回调、终态收尾、输出打开与界面复位。"""

    # ---- 宿主契约：实例属性 ----
    _state: str
    _pdf_files: list[Path]
    _temp_dir: str | None
    _worker: _PdfPipelineWorker | None
    _progress_bar: QProgressBar
    _stage_label: QLabel
    _result_group: QGroupBox
    _result_text: QTextEdit
    _template_combo: QComboBox
    _ocr_checkbox: QCheckBox
    _execute_btn: QPushButton
    _cancel_btn: QPushButton
    _scan_btn: QPushButton

    # ---- 宿主契约：方法（由执行编排域提供）----
    if TYPE_CHECKING:
        def _clear_injected_env(self) -> None: ...
    @Slot(str)
    def _on_stage_started(self, stage: str) -> None:
        self._stage_label.setText(f"⏳ {stage}...")

    @Slot(str, object)
    def _on_stage_finished(self, stage: str, result: object) -> None:
        del result  # 无信息量载荷，仅通知到达
        self._stage_label.setText(_(f"✓ {stage} 完成"))

    @Slot(int, int)
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

    @Slot(list)
    def _on_warnings(self, items: list) -> None:
        """D3：显示管线关键警告（AI Key 为空/OCR 未启用等），不再静默丢弃。"""
        for item in items:
            self._stage_label.setText(f"⚠ {item}")
        existing = self._result_text.toPlainText()
        block = "\n".join(f"⚠ {item}" for item in items)
        self._result_text.setText(_(f"{existing}\n\n[运行警告]\n{block}") if existing else _(f"[运行警告]\n{block}"))

    @Slot(object)
    def closeEvent(self, event: QCloseEvent) -> None:
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

    @Slot(str)
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

    @Slot()
    def _cancel(self) -> None:
        if self._worker and self._worker.isRunning():
            self._worker.cancel()
            self._stage_label.setText(_("正在取消..."))
            self._cancel_btn.setEnabled(False)
            # FINAL W-2：Qt 官方明确"UI 线程不应 wait()，应监听 finished 信号"——
            # 原 wait(5000) 最长冻结事件循环 5 秒。改为信号驱动异步收尾；
            # closeEvent 路径保留有界 wait（析构顺序需要确定性，属文档允许的例外）。
            def _on_cancel_finished() -> None:
                self._clear_injected_env()
                self._stage_label.setText(_("已取消"))
                self._cancel_btn.setEnabled(True)
                self._cancel_btn.setVisible(False)

            try:
                self._worker.finished.disconnect(_on_cancel_finished)
            except (RuntimeError, TypeError):
                pass  # 尚未连接/已销毁
            self._worker.finished.connect(_on_cancel_finished)

    @Slot()
    def _open_output_dir(self) -> None:
        if self._temp_dir:
            output = os.path.join(self._temp_dir, "output")
            if os.path.isdir(output):
                # A13/D65：跨平台打开结果目录（os.startfile 仅 Windows）
                QDesktopServices.openUrl(QUrl.fromLocalFile(str(output)))
                return
        toast = ToastManager.instance()
        toast.warning(_("输出目录不存在或已被清理"))

    @Slot()
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

    @Slot()
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
