"""Short-lived Qt workers used by the GUI composition root."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, QThread, Signal, Slot


def _thread_interrupted() -> bool:
    thread = QThread.currentThread()
    return thread is not None and thread.isInterruptionRequested()


class SiteInspectionWorker(QObject):
    finished = Signal(object, str)
    failed = Signal(str)

    def __init__(
        self,
        url: str,
        intent: str = "",
        robots_fail_closed: bool = True,
        fetcher: Any | None = None,
    ) -> None:
        super().__init__()
        self.url = url
        self.intent = intent
        self.robots_fail_closed = robots_fail_closed
        self.fetcher = fetcher

    @Slot()
    def run(self) -> None:
        try:
            if _thread_interrupted():
                return
            from ..sources.site_inspector import inspect_url
            from ..templates.template_catalog import bundled_template_catalog

            report = inspect_url(
                self.url,
                bundled_template_catalog(),
                intent=self.intent,
                robots_fail_closed=self.robots_fail_closed,
                fetcher=self.fetcher,
            ).to_dict()
        except Exception as exc:  # noqa: BLE001 - worker errors are emitted to the UI
            if not _thread_interrupted():
                self.failed.emit(f"{type(exc).__name__}: {exc}")
        else:
            if not _thread_interrupted():
                self.finished.emit(report, self.url)


class ActionRecorderWorker(QObject):
    finished = Signal(dict)
    failed = Signal(str)

    def __init__(self, url: str, output: Path) -> None:
        super().__init__()
        self._url = url
        self._output = output

    @Slot()
    def run(self) -> None:
        try:
            from ..fetching.action_recorder import record_with_playwright

            if _thread_interrupted():
                return
            result = record_with_playwright(self._url, self._output)
            if not _thread_interrupted():
                self.finished.emit(result)
        except Exception as exc:  # noqa: BLE001 - worker errors are emitted to the UI
            if not _thread_interrupted():
                self.failed.emit(str(exc))

class SampleRunWorker(QObject):
    finished = Signal(dict)
    failed = Signal(str)

    def __init__(self, config_path: Path, pages: int = 3) -> None:
        super().__init__()
        self._config_path = config_path
        self._pages = pages

    @Slot()
    def run(self) -> None:
        try:
            if _thread_interrupted():
                return
            from ..core.config import load_config
            from ..pipeline_ops.preflight import run_sample

            result = run_sample(load_config(self._config_path), pages=self._pages)
            if not _thread_interrupted():
                self.finished.emit(result)
        except Exception as exc:  # noqa: BLE001 - worker errors are emitted to the UI
            if not _thread_interrupted():
                self.failed.emit(str(exc))
