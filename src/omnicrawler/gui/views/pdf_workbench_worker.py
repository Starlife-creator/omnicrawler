"""PDF 工作台的后台流水线线程：扫描、解析、OCR、抽取一条龙执行与进度信号。

从 pdf_workbench.py 迁出（P1-3 第一批）。重依赖（pdfx.service / services.progress）
保持函数内懒加载；信号协议（stage_started / stage_finished / progress /
document_progress / unified_progress / warnings_received / all_done / failed）原样保留。
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import QWidget

from ..i18n import _

if TYPE_CHECKING:
    # 仅类型检查期导入：services.progress 属重依赖，运行期仍在函数内懒加载。
    from omnicrawler.services.progress import TaskProgressEvent


# ── 工作线程 ──────────────────────────────────────────────────────
class _PdfPipelineWorker(QThread):
    """后台线程：执行 PDF 处理流水线，通过信号报告进度。

    P2-4：内部由 ProgressTracker 驱动（阶段权重 + 子项展开 + EMA ETA），
    同时发出旧式 Signal，旧消费者代码无需修改。
    """

    stage_started = Signal(str)       # 阶段名（中文）
    stage_finished = Signal(str, object)  # 阶段名, 结果 dict
    warnings_received = Signal(list)  # D3：运行时警告（如“大模型已启用但 Key 空”）
    progress = Signal(int)            # 0-100
    all_done = Signal(object)         # 全部结果 dict
    failed = Signal(str)              # 错误消息
    document_progress = Signal(int, int)  # B12：已处理文档数, 总文档数（抽取阶段实时汇报）
    unified_progress = Signal(object)  # P2-4：TaskProgressEvent（新消费者可直接使用）

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
        # FINAL-W1：tracker 在 try 内的 import 之后才创建；若 import 失败，
        # except 块直接引用会抛 NameError 掩盖原始异常，故先置 None 再守卫。
        tracker = None
        try:
            from omnicrawler.pdfx.service import run_extraction
            from omnicrawler.services.progress import (
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

            def _bridge(ev: TaskProgressEvent) -> None:
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
            def _bridge_both(ev: TaskProgressEvent) -> None:
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
            if tracker is not None:
                try:
                    tracker.fail(str(exc))
                except Exception:  # noqa: BLE001 — tracker 失败不得吞没原始异常
                    pass
            self.failed.emit(f"{exc}\n{traceback.format_exc()}")
