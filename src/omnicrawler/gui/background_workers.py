"""Short-lived Qt workers used by the GUI composition root.

统一接入 :class:`~omnicrawler.gui.core.background_worker.BackgroundWorker`
（QThread 子类 + ``succeeded``/``failed`` 信号 + ``requestInterruption``
取消 + ``finished`` 自动清理），与 views 层共用同一后台任务模式（S3.1.1）。
原先基于 ``QObject`` + ``moveToThread`` 的三套样板已收敛到本模块的薄封装。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .core.background_worker import BackgroundWorker


class SiteInspectionWorker(BackgroundWorker):
    """站点智能识别后台任务。

    ``succeeded`` 载荷为 ``(report_dict, url)`` 元组；``failed`` 载荷为错误文本。
    """

    def __init__(
        self,
        url: str,
        intent: str = "",
        robots_fail_closed: bool = True,
        fetcher: Any | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.url = url
        self.intent = intent
        self.robots_fail_closed = robots_fail_closed
        self.fetcher = fetcher

    def work(self) -> Any:
        from ..sources.site_inspector import inspect_url
        from ..templates.template_catalog import bundled_template_catalog

        report = inspect_url(
            self.url,
            bundled_template_catalog(),
            intent=self.intent,
            robots_fail_closed=self.robots_fail_closed,
            fetcher=self.fetcher,
        ).to_dict()
        return report, self.url


class ActionRecorderWorker(BackgroundWorker):
    """网页操作录制后台任务。"""

    def __init__(self, url: str, output: Path, parent=None) -> None:
        super().__init__(parent)
        self._url = url
        self._output = output

    def work(self) -> Any:
        from ..fetching.action_recorder import record_with_playwright

        return record_with_playwright(self._url, self._output)


class SampleRunWorker(BackgroundWorker):
    """小样本试跑后台任务（独立工作区，不改变正式任务断点）。"""

    def __init__(self, config_path: Path, pages: int = 3, parent=None) -> None:
        super().__init__(parent)
        self._config_path = config_path
        self._pages = pages

    def work(self) -> Any:
        from ..core.config import load_config
        from ..pipeline_ops.preflight import run_sample

        return run_sample(load_config(self._config_path), pages=self._pages)
