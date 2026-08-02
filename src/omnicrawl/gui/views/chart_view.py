"""Lightweight result-quality visualization without a plotting dependency.

结果质量可视化：以原生进度条展示各字段非空率。
所有 CSV 读取均通过 CsvLoadWorker 在后台线程执行，避免阻塞 UI。
"""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import QEasingCurve, QPropertyAnimation, Qt
from PyQt6.QtWidgets import (
    QGraphicsOpacityEffect,
    QLabel,
    QProgressBar,
    QVBoxLayout,
    QWidget,
)

from ..async_workers import AsyncWorkerManager, CsvLoadWorker
from ..i18n import _


class ChartView(QWidget):
    """Show sampled field completeness as accessible native progress bars.

    通过 AsyncWorkerManager 异步加载 CSV，加载期间显示进度提示。
    可重复调用 load_csv，旧任务会被自动清理。
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._layout = QVBoxLayout(self)
        self._summary = QLabel(_("尚未加载结果统计"))
        self._summary.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._summary.setObjectName("muted")
        self._layout.addWidget(self._summary)

        # 异步加载进度条（不确定模式）
        self._loading_bar = QProgressBar()
        self._loading_bar.setRange(0, 0)  # 不确定模式
        self._loading_bar.setTextVisible(False)
        self._loading_bar.setVisible(False)
        self._loading_bar.setMaximumHeight(4)
        self._layout.addWidget(self._loading_bar)

        self._bars: list[tuple[QLabel, QProgressBar]] = []
        self._worker: CsvLoadWorker | None = None
        self._async = AsyncWorkerManager()
        self._filepath: Path | None = None
        self._showing_data = False
        self.setVisible(False)

    # ------------------------------------------------------------------
    # 公共 API
    # ------------------------------------------------------------------

    def load_csv(self, path: Path, *, sample_limit: int = 50_000) -> bool:
        """异步加载 CSV 文件并绘制字段完整率条形图。

        保持原同步签名的兼容入口：立即返回 True 表示已派发加载任务。
        真正的结果通过 finished_loading 信号回到主线程后渲染。
        """
        path = Path(path)
        if not path.is_file():
            self.clear()
            return False

        self._filepath = path

        # 取消尚未完成的旧任务，避免竞态覆盖最新结果
        if self._worker is not None and self._worker.isRunning():
            self._worker.requestInterruption()
            self._worker.quit()
            self._worker.wait(2000)

        # 进入加载态
        self._remove_bars()
        self._loading_bar.setVisible(True)
        self._summary.setText(_("正在加载结果统计…"))
        self._showing_data = False
        self.setVisible(True)

        self._worker = CsvLoadWorker(path, sample_limit=sample_limit, parent=self)
        self._worker.finished_loading.connect(self._on_loaded)
        self._worker.failed.connect(self._on_failed)
        # 线程结束时自动清理引用
        self._worker.finished.connect(self._on_worker_finished)
        self._worker.start()
        return True

    def clear(self) -> None:
        """清空图表并回到初始状态。"""
        if self._worker is not None and self._worker.isRunning():
            self._worker.requestInterruption()
            self._worker.quit()
            self._worker.wait(1000)
        self._remove_bars()
        self._loading_bar.setVisible(False)
        self._summary.setText(_("尚未加载结果统计"))
        self._filepath = None
        self.setVisible(False)

    # ------------------------------------------------------------------
    # 异步回调（在主线程执行）
    # ------------------------------------------------------------------

    def _on_loaded(
        self, headers: list, sample_rows: list, present: dict, total_rows: int,
    ) -> None:
        """CSV 加载完成回调。"""
        self._loading_bar.setVisible(False)

        rows = total_rows
        if not headers or rows == 0:
            self._summary.setText(_("结果文件为空"))
            self.setVisible(False)
            self._showing_data = False
            return

        sampled = rows > len(sample_rows)
        suffix = _("（抽样）") if sampled else ""
        self._summary.setText(
            _("字段完整率：{0} 行，{1} 列{2}").format(rows, len(headers), suffix)
        )

        # 选取完整率最低的 8 个字段展示
        ranking = sorted(
            headers,
            key=lambda name: (present.get(name, 0) / max(1, rows), name),
        )[:8]
        for name in ranking:
            percentage = round(present.get(name, 0) * 100 / max(1, rows))
            label = QLabel(str(name))
            bar = QProgressBar()
            bar.setRange(0, 100)
            bar.setValue(percentage)
            bar.setFormat(_("%p% 非空"))
            self._layout.addWidget(label)
            self._layout.addWidget(bar)
            self._bars.append((label, bar))
        self.setVisible(True)
        if not self._showing_data:
            self._fade_in_widget(self)
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

    def _on_failed(self, message: str) -> None:
        """CSV 加载失败回调。"""
        self._loading_bar.setVisible(False)
        self._remove_bars()
        self._summary.setText(_("加载失败：{0}").format(message))
        self.setVisible(True)

    def _on_worker_finished(self) -> None:
        """线程结束后清理引用。"""
        sender = self.sender()
        if sender is self._worker:
            self._worker = None

    # ------------------------------------------------------------------
    # 内部辅助
    # ------------------------------------------------------------------

    def _remove_bars(self) -> None:
        for label, bar in self._bars:
            self._layout.removeWidget(label)
            self._layout.removeWidget(bar)
            label.deleteLater()
            bar.deleteLater()
        self._bars.clear()
