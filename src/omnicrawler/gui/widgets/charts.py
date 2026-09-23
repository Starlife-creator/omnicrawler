"""QtCharts 图表组件（V1 可视化补齐）—— 结果质量 / 任务趋势 / 差异概览共用。

依赖裁定（§9.4 #5 前置测量的**实测结论**）
------------------------------------------

《优化方案》§11.2 的动作里写着"gui 可选依赖组追加 QtCharts"，其**前置**是
"先按《审查记录》§9.4 #5 测量 QtCharts 对便携包体积与依赖闭包的影响"。实测：

======================  ====================================================
项                       实测量
======================  ====================================================
依赖闭包                  **零新增** —— `PySide6>=6.5,<7` 元包已连带安装
                          `PySide6-Addons`，QtCharts 就在其中（本地实测可 import）
运行库体积                `QtCharts.pyd` 0.78 MB + `Qt6Charts.dll` 1.65 MB
                          ≈ **2.4 MB**（未压缩口径）
打包 spec                 未 excludes `PySide6` Addons ⇒ PyInstaller 按需收集，
                          构建脚本无需改动
======================  ====================================================

⇒ 既然"控制体积"这个**意图**已被满足，再补一条冗余 pin（`PySide6-Addons`）只会
增加依赖面与锁文件维护成本，因此**不加依赖**；改为在运行期做**能力探测 + 降级**：
环境缺 QtCharts（例如只装了 `PySide6-Essentials`）时回退到既有进度条视图，
并给出**可行动**提示（缺哪个包、怎么装）。

约束（§11.2）
-------------

* 颜色一律走 `VisualTokens` 语义令牌（既有"禁裸色值"守卫覆盖本模块）；
* 本模块**不自己起线程**：数据由调用方按既有 `BackgroundWorker` 模式准备好后送入；
* 每个图表都有离屏渲染冒烟测试（见 `tests/unit/gui/test_charts.py`）。
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING

from PySide6.QtGui import QColor
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from ..design_system import ThemeManager
from ..i18n import _

if TYPE_CHECKING:  # QtCharts 是**可选**能力：类型层引用不产生运行期导入
    from PySide6.QtCharts import QBarCategoryAxis, QValueAxis

#: 缺 QtCharts 时给用户的**可行动**提示（§11.2：说清缺哪个 extra、怎么装）。
_MISSING_CHARTS_HINT = _(
    "当前环境缺少 QtCharts（PySide6 的 Addons 组件），图表已回退为进度条视图。"
    '安装：pip install "PySide6-Addons>=6.5,<7"（装 PySide6 元包时通常已自带）。'
)

_CHARTS_AVAILABLE: bool | None = None


def charts_available() -> bool:
    """QtCharts 是否可导入（结果缓存；只探测一次）。"""
    global _CHARTS_AVAILABLE
    if _CHARTS_AVAILABLE is None:
        try:
            from PySide6 import QtCharts  # noqa: F401
        except ImportError:
            _CHARTS_AVAILABLE = False
        else:
            _CHARTS_AVAILABLE = True
    return _CHARTS_AVAILABLE


def chart_unavailable_hint() -> str:
    """缺 QtCharts 时展示的提示文案（缺包时才有意义）。"""
    return _MISSING_CHARTS_HINT


def _token_color(token_name: str) -> str:
    """按语义令牌名取出颜色字符串（图表取色的**唯一**入口）。

    令牌不存在时**报错**而不是回退某个猜测色：静默回退会让"图表颜色没跟主题走"
    变成没人能发现的长期缺陷。
    """
    tokens = ThemeManager.instance().tokens
    if not hasattr(tokens, token_name):
        raise ValueError(_("未知颜色令牌：{0}").format(token_name))
    return str(getattr(tokens, token_name))


def _rgba_or_hex(value: str) -> QColor:
    if value.startswith("rgba("):
        from ..design_system import rgba_token_to_qcolor

        return rgba_token_to_qcolor(value)
    return QColor(value)


class BarChart(QWidget):
    """单系列条形图（``horizontal=True`` 为水平条形，用于排序类数据）。

    ★ **无障碍补偿**：图表是视觉信息，屏幕阅读器读不到内容。所以每次 ``set_data``
    都会把数据同时写进 ``setAccessibleDescription``（"字段A 92%、字段B 71%"），
    不让"补齐可视化"变成无障碍的净损失。
    """

    def __init__(self, *, horizontal: bool = True, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._horizontal = horizontal
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._chart_view: QWidget | None = None
        self._category_axis: QBarCategoryAxis | None = None
        self._value_axis: QValueAxis | None = None
        self._labels: tuple[str, ...] = ()
        self._values: tuple[float, ...] = ()
        self.setAccessibleName(_("条形图"))
        self.setAccessibleDescription(_("暂无数据"))

    # ── 数据 ──────────────────────────────────────────────
    @property
    def labels(self) -> tuple[str, ...]:
        return self._labels

    @property
    def values(self) -> tuple[float, ...]:
        return self._values

    def set_data(
        self,
        pairs: Sequence[tuple[str, float]],
        *,
        value_suffix: str = "",
        color_token: str = "primary",
        value_max: float | None = None,
    ) -> None:
        """设置数据并重绘。

        ``value_max`` 给定时用作坐标轴上限（例如百分比固定 100），否则取本组最大值
        （并在为 0 时退化为 1，避免坐标轴 0..0 把图挤成一条线）。
        """
        self._labels = tuple(str(label) for label, _ in pairs)
        self._values = tuple(float(value) for _, value in pairs)
        self.setAccessibleDescription(self._describe(value_suffix))
        self._rebuild(value_suffix=value_suffix, color_token=color_token, value_max=value_max)

    def clear(self) -> None:
        self._labels = ()
        self._values = ()
        self.setAccessibleDescription(_("暂无数据"))
        self._rebuild(value_suffix="", color_token="primary", value_max=None)

    # ── 内部 ──────────────────────────────────────────────
    def _describe(self, value_suffix: str) -> str:
        if not self._labels:
            return _("暂无数据")
        parts = [
            _("{0} {1}{2}").format(label, _format_value(value), value_suffix)
            for label, value in zip(self._labels, self._values, strict=True)
        ]
        return _("条形图数据：") + "、".join(parts)

    def _rebuild(self, *, value_suffix: str, color_token: str, value_max: float | None) -> None:
        if self._chart_view is not None:
            self._layout.removeWidget(self._chart_view)
            self._chart_view.deleteLater()
            self._chart_view = None
        self._category_axis = None
        self._value_axis = None
        if not self._labels:
            return
        from PySide6.QtCharts import (
            QBarCategoryAxis,
            QBarSeries,
            QBarSet,
            QChart,
            QChartView,
            QHorizontalBarSeries,
            QValueAxis,
        )
        from PySide6.QtCore import QMargins, Qt
        from PySide6.QtGui import QPainter

        # ★ 水平图的类目轴是**自下而上**追加的：不反转的话"输入的第一项"会画在最下面，
        #   与调用方（排序类数据）的直觉相反。这里统一成"第一项在最上"。
        labels = tuple(reversed(self._labels)) if self._horizontal else self._labels
        values = tuple(reversed(self._values)) if self._horizontal else self._values

        bar_set = QBarSet("")
        for value in values:
            bar_set.append(value)
        bar_set.setColor(_rgba_or_hex(_token_color(color_token)))
        bar_set.setBorderColor(_rgba_or_hex(_token_color("border")))

        series = QHorizontalBarSeries() if self._horizontal else QBarSeries()
        series.append(bar_set)

        chart = QChart()
        chart.addSeries(series)
        chart.legend().setVisible(False)
        chart.setBackgroundVisible(False)
        chart.setMargins(QMargins(0, 0, 0, 0))
        chart.setTitle("")

        category_axis = QBarCategoryAxis()
        category_axis.append(list(labels))
        category_axis.setLabelsColor(_rgba_or_hex(_token_color("muted")))
        category_axis.setGridLineVisible(False)

        value_axis = QValueAxis()
        upper = value_max if value_max is not None else max(values)
        value_axis.setRange(0, max(1.0, float(upper)))
        value_axis.setLabelFormat(f"%.0f{value_suffix}" if value_suffix else "%.0f")
        value_axis.setLabelsColor(_rgba_or_hex(_token_color("muted")))
        value_axis.setGridLineColor(_rgba_or_hex(_token_color("border")))

        if self._horizontal:
            chart.addAxis(category_axis, Qt.AlignmentFlag.AlignLeft)
            chart.addAxis(value_axis, Qt.AlignmentFlag.AlignBottom)
        else:
            chart.addAxis(category_axis, Qt.AlignmentFlag.AlignBottom)
            chart.addAxis(value_axis, Qt.AlignmentFlag.AlignLeft)
        series.attachAxis(category_axis)
        series.attachAxis(value_axis)

        self._category_axis = category_axis
        self._value_axis = value_axis

        chart_view = QChartView(chart)
        chart_view.setRenderHint(QPainter.RenderHint.Antialiasing)
        chart_view.setAccessibleName(self.accessibleName())
        chart_view.setMinimumHeight(max(120, 28 * len(self._labels)))
        self._layout.addWidget(chart_view)
        self._chart_view = chart_view

    def has_chart(self) -> bool:
        """当前是否真的渲染了图表（降级路径下为 False）。"""
        return self._chart_view is not None

    @property
    def rendered_categories(self) -> tuple[str, ...]:
        """**实际**送入类目轴的顺序（读真实 Qt 轴对象，不重算一遍逻辑）。

        存在的理由：QtCharts 的水平类目轴是**自下而上**追加的，于是"输入的第一项"
        会画在最下方 —— 本控件为对齐直觉做了反转。这个顺序是用户直接看到的东西，
        必须能被断言钉住。
        """
        if self._category_axis is None:
            return ()
        return tuple(str(item) for item in self._category_axis.categories())

    @property
    def value_axis_range(self) -> tuple[float, float] | None:
        """**实际**送入数值轴的范围（无图时为 ``None``）。"""
        if self._value_axis is None:
            return None
        return (float(self._value_axis.min()), float(self._value_axis.max()))


def _format_value(value: float) -> str:
    """数值显示：整数去掉小数点，其余保留一位。"""
    return str(int(value)) if float(value).is_integer() else f"{value:.1f}"


class ChartUnavailableNotice(QLabel):
    """缺 QtCharts 时的可行动提示（默认隐藏，由调用方按能力显隐）。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(chart_unavailable_hint(), parent)
        self.setWordWrap(True)
        self.setObjectName("muted")
        self.setAccessibleName(_("图表不可用提示"))
        self.setVisible(False)
