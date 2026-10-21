"""批量转换与按引擎并发闸门。

为什么需要按引擎限流：PDF 解析、OCR、Office 解析各自有独立的资源峰值，
把所有任务扔进一个线程池会让同一引擎并发打满（内存/文件句柄）。这里用
``EngineGate`` 给每类引擎一个独立的 ``Semaphore``，线程池只负责调度。

默认每个引擎并发 1；调用方可以按需调高。``convert_many`` 是库级 API，
CLI 的 watch 模式复用同一实现。
"""

from __future__ import annotations

import threading
from collections.abc import Iterable
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .formats import IMAGE_EXTENSIONS

_IMAGE_FORMATS = frozenset(IMAGE_EXTENSIONS)
_PDF_FORMATS = frozenset({".pdf"})
_DOCUMENT_FORMATS = frozenset(
    {".txt", ".md", ".html", ".htm", ".eml", ".docx", ".pptx", ".odt", ".epub"}
)
_HEAVY_TABLE_FORMATS = frozenset({".parquet", ".duckdb"})


def engine_key(src_fmt: str | None, dst_fmt: str | None) -> str:
    """把一次转换归到资源域；顺序：图片 > PDF > 文档 > 列式表格 > 普通表格。"""
    formats = {str(src_fmt or "").lower(), str(dst_fmt or "").lower()}
    if formats & _IMAGE_FORMATS:
        return "image"
    if formats & _PDF_FORMATS:
        return "pdf"
    if formats & _DOCUMENT_FORMATS:
        return "document"
    if formats & _HEAVY_TABLE_FORMATS:
        return "columnar"
    return "table"


class EngineGate:
    """按引擎键限流的信号量集合（线程安全）。"""

    def __init__(self, default_limit: int = 1) -> None:
        if default_limit < 1:
            raise ValueError("EngineGate.default_limit 必须 >= 1")
        self._default_limit = default_limit
        self._limits: dict[str, int] = {}
        self._semaphores: dict[str, threading.Semaphore] = {}
        self._lock = threading.Lock()

    def configure(self, key: str, limit: int) -> None:
        if limit < 1:
            raise ValueError("EngineGate 的 limit 必须 >= 1")
        with self._lock:
            self._limits[key] = limit
            self._semaphores[key] = threading.Semaphore(limit)

    def limit_for(self, key: str) -> int:
        return self._limits.get(key, self._default_limit)

    def _semaphore(self, key: str) -> threading.Semaphore:
        with self._lock:
            semaphore = self._semaphores.get(key)
            if semaphore is None:
                semaphore = threading.Semaphore(self._limits.get(key, self._default_limit))
                self._semaphores[key] = semaphore
            return semaphore

    @contextmanager
    def slot(self, key: str) -> Any:
        semaphore = self._semaphore(key)
        semaphore.acquire()
        try:
            yield
        finally:
            semaphore.release()


@dataclass(frozen=True, slots=True)
class BatchTask:
    source: Path
    target: Path
    src_format: str | None = None
    dst_format: str | None = None
    options: dict[str, Any] = field(default_factory=dict)
    output_rule: Any = None


@dataclass(slots=True)
class BatchOutcome:
    task: BatchTask
    status: str  # completed | skipped | failed | cancelled
    result: Any = None
    error: str = ""
    engine: str = ""


def _run_task(task: BatchTask, gate: EngineGate, should_stop: Any) -> BatchOutcome:
    from ._core import convert

    if should_stop is not None and should_stop():
        return BatchOutcome(task=task, status="cancelled", error="批量转换已取消", engine="")
    engine = engine_key(task.src_format, task.dst_format)
    try:
        with gate.slot(engine):
            result = convert(
                task.source,
                task.target,
                src_format=task.src_format,
                dst_format=task.dst_format,
                options=task.options,
                output_rule=task.output_rule,
                should_stop=should_stop,
            )
    except Exception as exc:  # noqa: BLE001 - 单个任务失败不得中断整批
        return BatchOutcome(task=task, status="failed", error=f"{type(exc).__name__}: {exc}", engine=engine)
    status = "skipped" if str(getattr(result, "status", "")) == "skipped" else "completed"
    return BatchOutcome(task=task, status=status, result=result, engine=engine)


def convert_many(
    tasks: Iterable[BatchTask],
    *,
    max_workers: int | None = None,
    gate: EngineGate | None = None,
    should_stop: Any = None,
) -> list[BatchOutcome]:
    """并行转换一批任务，保持输入顺序返回结果；单任务失败不影响其他任务。"""
    task_list = list(tasks)
    if not task_list:
        return []
    gate = gate or EngineGate()
    if max_workers is None:
        max_workers = min(4, len(task_list))
    if max_workers < 1:
        raise ValueError("max_workers 必须 >= 1")
    futures: dict[Future[BatchOutcome], int] = {}
    outcomes: list[BatchOutcome | None] = [None] * len(task_list)
    with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="convertx") as pool:
        for index, task in enumerate(task_list):
            futures[pool.submit(_run_task, task, gate, should_stop)] = index
        for future in as_completed(futures):
            index = futures[future]
            try:
                outcomes[index] = future.result()
            except Exception as exc:  # noqa: BLE001 - 调度层兜底
                outcomes[index] = BatchOutcome(
                    task=task_list[index],
                    status="failed",
                    error=f"{type(exc).__name__}: {exc}",
                )
    return [outcome for outcome in outcomes if outcome is not None]


__all__ = [
    "BatchOutcome",
    "BatchTask",
    "EngineGate",
    "convert_many",
    "engine_key",
]
