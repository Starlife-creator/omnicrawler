"""PDF 后处理阶段的唯一定义点（阶段名、启用判据、指标与检查点）。

原先这段编排写在 ``pipeline/_run.py`` 的 ``run()`` 里：读 ``processors.pdf.enabled``、
决定是否跑、逐个 gauge、存 checkpoint、记阶段耗时。这些都属于「PDF 阶段是什么」，
不属于「抓取主循环」，按 AGENTS.md 原则 3 收进这里，编排层只留一次调用。

对外语义保持不变（含 ``run_pdf`` 覆盖优先级：显式 True/False 优先于配置）。
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from ..core.config import AppConfig
from ..state import StateStore
from .pdf_integration import EventCallback, run_pdf_pipeline

#: 从 PDF 结果里抬成 gauge 的计数字段——加新字段只改这里。
PDF_GAUGE_KEYS: tuple[str, ...] = ("processed", "succeeded", "failed", "ocr_pages", "pages")

#: PDF 阶段不跑的终态。取消时不出网也不读盘，其余终态（含失败、部分成功）照跑，
#: 因为已落盘的 PDF 附件仍值得被处理。
_SKIPPED_STATUSES = frozenset({"cancelled"})


def pdf_stage_enabled(config: AppConfig, override: bool | None) -> bool:
    """``processors.pdf.enabled`` 为默认值；``override`` 显式给出时以它为准。"""
    configured = bool(config.section("processors").get("pdf", {}).get("enabled", False))
    return configured if override is None else bool(override)


def _prefixed_callback(
    callback: Callable[[str, Any], None],
) -> EventCallback:
    """把 PDF 子阶段名加上 ``pdf_`` 前缀，避免与抓取阶段事件重名。"""

    def forward(stage: str, value: dict[str, Any]) -> None:
        callback(f"pdf_{stage}", value)

    return forward


def run_pdf_stage(
    config: AppConfig,
    state: StateStore,
    *,
    run_id: str,
    run_pdf: bool | None = None,
    status: str = "succeeded",
    callback: Callable[[str, Any], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
    metrics: Any | None = None,
) -> dict[str, Any] | None:
    """跑 PDF 阶段并落指标/检查点；未启用或被取消时返回 ``None``。

    ``metrics`` 只要求有 ``gauge(name, value)`` 与 ``record_stage(name, seconds)``
    两个方法（``services.metrics.RunMetrics`` 满足），不反向依赖 services 包。
    """
    if not pdf_stage_enabled(config, run_pdf) or status in _SKIPPED_STATUSES:
        return None

    stage_callback: EventCallback | None = None
    if callback is not None:
        stage_callback = _prefixed_callback(callback)

    started = time.monotonic()
    summary = run_pdf_pipeline(
        config,
        state,
        run_id=run_id,
        callback=stage_callback,
        should_stop=should_stop,
    )
    if metrics is not None:
        metrics.gauge("omnicrawler_pdf_documents", float(summary.get("documents", 0)))
        result_summary = summary.get("result", {})
        if isinstance(result_summary, dict):
            for key in PDF_GAUGE_KEYS:
                value = result_summary.get(key)
                if isinstance(value, (int, float)):
                    metrics.gauge(f"omnicrawler_pdf_{key}", float(value))
    state.save_checkpoint(run_id, "pdf", "pdf", summary)
    if metrics is not None:
        metrics.record_stage("pdf", time.monotonic() - started)
    return summary
