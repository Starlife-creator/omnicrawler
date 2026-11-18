"""变更监控后台检查线程与静态页面探针线程。"""

from __future__ import annotations

import copy
from typing import Any

from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import QWidget

from omnicrawler.gui.core.background_worker import BackgroundWorker
from omnicrawler.gui.i18n import _

INTERVAL_OPTIONS = [
    (60, _("1 分钟")),
    (300, _("5 分钟")),
    (600, _("10 分钟")),
    (1800, _("30 分钟")),
    (3600, _("1 小时")),
    (7200, _("2 小时")),
    (21600, _("6 小时")),
    (43200, _("12 小时")),
    (86400, _("24 小时")),
]

CONDITION_OPTIONS = [
    ("changed", _("内容发生变化")),
    ("contains:{{text}}", _("包含指定文本")),
    ("regex:{{pattern}}", _("匹配正则表达式")),
    ("equals:{{value}}", _("精确等于某个值")),
]


# ── 后台检查线程 ────────────────────────────────────────────────────

class _CheckWorker(QThread):
    """后台执行 change_detector.check_all()，避免阻塞 GUI。"""

    checked = Signal(list)   # Results are separate from QThread.finished().
    error = Signal(str)

    def __init__(
        self,
        rules_json: list[dict],
        parent: QWidget | None = None,
        *,
        fetcher: Any = None,
        mode: str = "check",
    ) -> None:
        super().__init__(parent)
        self.mode = mode
        self._rules_json = copy.deepcopy(rules_json)
        self.report: dict[str, dict[str, Any]] = {}
        self.rules_after: list[dict[str, Any]] = []
        self._fetcher = fetcher
        # 仅在**工作线程内**赋值/清空；`cancel()` 从 GUI 线程读取它并转调 detector
        self._detector: Any = None
        self._cancelled_by_user = False

    def cancel(self) -> None:
        """请求取消（线程安全）：先记标志，再中转给正在运行的 detector。

        `requestInterruption()` 只置 Qt 侧标志，而抓取发生在 detector 内部，
        必须由本方法把取消传下去；`run()` 里也会在启动前检查一次中断标志，
        覆盖「start() 之前就被中断」的情形。
        """
        self._cancelled_by_user = True
        detector = self._detector
        if detector is not None:
            detector.cancel()

    def is_cancelled(self) -> bool:
        """是否已被请求取消（用户取消或 Qt 中断请求）。"""
        return self._cancelled_by_user or self.isInterruptionRequested()

    def run(self) -> None:
        import asyncio

        try:
            from omnicrawler.scheduling.change_detector import ChangeDetector, MonitorRule

            detector = ChangeDetector(fetcher=self._fetcher, durable_delivery=True)
            self._detector = detector
            for item in self._rules_json:
                detector.add_rule(MonitorRule.from_dict(item))
            if self.is_cancelled():
                detector.cancel()

            events: list[Any]
            if self.mode == "status":
                events = detector.delivery_report()
            elif self.mode == "retry":
                events = detector.pending_notifications(force=True)
            else:
                events = asyncio.run(detector.check_all())
            self.report = detector.check_report()
            self.rules_after = [rule.to_dict() for rule in detector.list_rules()]
            if self.mode != "status" and not self.is_cancelled():
                from omnicrawler.scheduling.webhook import dispatch_webhooks
                dispatch_webhooks(detector._store, detector.list_rules(), detector.network_broker(),
                                  force=self.mode == "retry", cancelled=self.is_cancelled)
            if self.is_cancelled():
                # 取消是正常终态：**不把（可能被截断的）结果当成功交付**，
                # 但仍要发一个终态信号，调用方才能复位「进行中」状态。
                self.checked.emit([])
            else:
                self.checked.emit(events)
        except Exception as exc:
            self.error.emit(str(exc))
        finally:
            self._detector = None


class _ProbeWorker(BackgroundWorker):
    def __init__(self, url: str, selector: str, fetcher: Any, parent: QWidget) -> None:
        super().__init__(parent)
        self.url, self.selector, self.fetcher = url, selector, fetcher

    def work(self) -> dict[str, Any]:
        from omnicrawler.extraction.extractors import decode_body
        from omnicrawler.fetching.page_probe import fetch_static_page
        from omnicrawler.scheduling.change_detector import ChangeDetector

        if self.isInterruptionRequested():
            return {}
        response = fetch_static_page(self.url, fetcher=self.fetcher)
        return {"status": response.status,
                "content": ChangeDetector._extract_content(decode_body(response), self.selector) if self.selector else ""}

