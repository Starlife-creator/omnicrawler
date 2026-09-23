"""V1 可视化补齐验收（《优化方案》§11.2 的图表项）。

覆盖三处接线与两条路径：
  * `widgets/charts.BarChart` —— 数据/顺序/取色/无障碍描述；两条朝向；
  * `chart_view` —— **主路径**（QtCharts）与**降级路径**（缺 QtCharts 时的进度条 + 可行动提示）；
  * `task_history` —— 任务耗时趋势（★ 未完成的条目必须**显式排除并计数**，不得画成 0）；
  * `change_monitor` —— 差异概览（★ 没做过检查时不画图：未知 ≠ 0）。

依赖裁定见 `widgets/charts.py` 的模块 docstring：`PySide6` 元包已含 Addons，
**不新增依赖**，但保留能力探测 + 降级。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from PySide6.QtWidgets import QApplication, QWidget

from omnicrawler.gui.views import chart_view as chart_view_module
from omnicrawler.gui.views.change_monitor import ChangeMonitorView
from omnicrawler.gui.views.task_history import TREND_POINTS, TaskHistory
from omnicrawler.gui.widgets.charts import (
    BarChart,
    chart_unavailable_hint,
    charts_available,
)


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


# ── BarChart 本体 ────────────────────────────────────────


def test_charts_are_available_in_this_environment() -> None:
    """本环境依赖 `PySide6` 元包 ⇒ Addons 自带 QtCharts（§9.4 #5 实测结论）。"""
    assert charts_available() is True


def test_unavailable_hint_is_actionable() -> None:
    hint = chart_unavailable_hint()
    assert "QtCharts" in hint
    assert "PySide6-Addons" in hint  # 缺哪个包
    assert "pip install" in hint  # 怎么装


def test_chart_exposes_data_and_accessibility(qapp: QApplication) -> None:
    chart = BarChart(horizontal=True)
    chart.set_data([("title", 98.0), ("price", 71.5)], value_suffix="%", value_max=100)

    assert chart.labels == ("title", "price")
    assert chart.values == (98.0, 71.5)
    assert chart.has_chart() is True
    # ★ 无障碍补偿：图表是视觉信息，屏幕阅读器读不到 ⇒ 数据必须进 accessibleDescription
    description = chart.accessibleDescription()
    assert "title" in description and "98%" in description
    assert "price" in description and "71.5%" in description


def test_horizontal_chart_puts_first_item_at_top(qapp: QApplication) -> None:
    """QtCharts 水平类目轴自下而上追加 ⇒ 控件要反转一次，让输入第一项在最上方。"""
    chart = BarChart(horizontal=True)
    chart.set_data([("first", 1.0), ("second", 2.0), ("third", 3.0)])

    assert chart.rendered_categories == ("third", "second", "first")


def test_vertical_chart_keeps_input_order(qapp: QApplication) -> None:
    chart = BarChart(horizontal=False)
    chart.set_data([("first", 1.0), ("second", 2.0), ("third", 3.0)])

    assert chart.rendered_categories == ("first", "second", "third")


def test_value_axis_honours_value_max(qapp: QApplication) -> None:
    chart = BarChart(horizontal=True)
    chart.set_data([("a", 12.0)], value_suffix="%", value_max=100)
    assert chart.value_axis_range == (0.0, 100.0)

    chart.set_data([("a", 12.0)])
    assert chart.value_axis_range == (0.0, 12.0)  # 未给上限 ⇒ 取本组最大值


def test_all_zero_values_do_not_collapse_axis(qapp: QApplication) -> None:
    """全 0 时坐标轴上限退化为 1，否则 0..0 会把图挤成一条线。"""
    chart = BarChart(horizontal=False)
    chart.set_data([("a", 0.0), ("b", 0.0)])
    assert chart.value_axis_range == (0.0, 1.0)


def test_empty_data_renders_nothing(qapp: QApplication) -> None:
    chart = BarChart(horizontal=True)
    chart.set_data([("a", 1.0)])
    chart.clear()

    assert chart.has_chart() is False
    assert chart.rendered_categories == ()
    assert chart.value_axis_range is None
    assert chart.accessibleDescription()


def test_unknown_colour_token_is_rejected(qapp: QApplication) -> None:
    """未知令牌要报错：静默回退某个猜测色会让"颜色没跟主题走"变成隐性缺陷。"""
    chart = BarChart(horizontal=True)
    with pytest.raises(ValueError):
        chart.set_data([("a", 1.0)], color_token="definitely-not-a-token")


# ── chart_view：两条路径 ─────────────────────────────────


def _loaded_view(qapp: QApplication) -> Any:
    view = chart_view_module.ChartView()
    view._on_loaded(
        ["title", "price", "author", "tags"],
        [],
        {"title": 98, "price": 71, "author": 12, "tags": 4},
        100,
    )
    return view


def test_chart_view_uses_chart_when_available(qapp: QApplication) -> None:
    view = _loaded_view(qapp)

    assert view._chart.has_chart() is True
    # 完整率最低的排最前（最需要关注的先看到）
    assert view._chart.labels == ("tags", "author", "price", "title")
    assert view._bars == []  # 主路径不产生进度条
    assert view._charts_hint.isHidden() is True
    assert view._summary.text()


def test_chart_view_degrades_to_progress_bars(
    qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """缺 QtCharts ⇒ 回退进度条 + **可行动**提示，功能不丢。"""
    monkeypatch.setattr(chart_view_module, "charts_available", lambda: False)
    view = _loaded_view(qapp)

    assert view._chart.has_chart() is False
    assert len(view._bars) == 4
    assert view._charts_hint.isHidden() is False
    assert "PySide6-Addons" in view._charts_hint.text()


def test_chart_view_clear_removes_both_paths(
    qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    view = _loaded_view(qapp)
    view.clear()
    assert view._chart.has_chart() is False

    monkeypatch.setattr(chart_view_module, "charts_available", lambda: False)
    degraded = _loaded_view(qapp)
    degraded.clear()
    assert degraded._bars == []
    assert degraded._charts_hint.isHidden() is True


def test_chart_view_empty_result_renders_no_chart(qapp: QApplication) -> None:
    view = chart_view_module.ChartView()
    view._on_loaded([], [], {}, 0)

    assert view._chart.has_chart() is False
    assert view._bars == []


# ── task_history：耗时趋势 ───────────────────────────────


def _history(tmp_path: Path, qapp: QApplication) -> tuple[TaskHistory, QWidget]:
    """构造历史面板。

    ★ 容器必须**由调用方持有引用**：`build_ui` 把列表控件挂到这个容器上，
    临时 `QWidget()` 会被 GC 带走 ⇒ 子控件 C++ 对象随即失效
    （`RuntimeError: Internal C++ object already deleted`）。这不是产品缺陷。
    """
    container = QWidget()
    history = TaskHistory(tmp_path)
    history.build_ui(container)
    return history, container


def _record(started: str, finished: str | None) -> dict[str, Any]:
    return {
        "task_id": started,
        "project_name": "p",
        "status": "finished",
        "started_at": started,
        "finished_at": finished,
    }


def test_task_trend_uses_completed_durations(tmp_path: Path, qapp: QApplication) -> None:
    history, _container = _history(tmp_path, qapp)
    history._apply_records(
        [
            _record("2026-09-23T10:00:00", "2026-09-23T10:00:30"),
            _record("2026-09-23T11:00:00", "2026-09-23T11:02:00"),
        ]
    )

    assert history._trend_chart.has_chart() is True
    # 最早的排最前（时间自左向右读）
    assert history._trend_chart.values == (30.0, 120.0)
    assert "2 次已完成" in history._trend_note.text()


def test_task_trend_excludes_unfinished_and_says_so(tmp_path: Path, qapp: QApplication) -> None:
    """★ 未完成的任务**不得**画成 0（那会伪造一个"极快"），必须排除并计数。"""
    history, _container = _history(tmp_path, qapp)
    history._apply_records(
        [
            _record("2026-09-23T10:00:00", "2026-09-23T10:00:30"),
            _record("2026-09-23T11:00:00", None),
            _record("2026-09-23T12:00:00", "not-a-timestamp"),
        ]
    )

    assert history._trend_chart.values == (30.0,)
    note = history._trend_note.text()
    assert "1 次已完成" in note
    assert "2 次尚未结束" in note


def test_task_trend_hidden_when_nothing_completed(tmp_path: Path, qapp: QApplication) -> None:
    history, _container = _history(tmp_path, qapp)
    history._apply_records([_record("2026-09-23T11:00:00", None)])

    assert history._trend_chart.has_chart() is False
    assert "还没有已完成的任务" in history._trend_note.text()


def test_task_trend_caps_points(tmp_path: Path, qapp: QApplication) -> None:
    history, _container = _history(tmp_path, qapp)
    records = [
        _record(f"2026-09-23T{h:02d}:00:00", f"2026-09-23T{h:02d}:00:10") for h in range(10)
    ]
    history._apply_records(records)

    assert len(history._trend_chart.values) == TREND_POINTS


# ── change_monitor：差异概览 ─────────────────────────────


def _monitor() -> ChangeMonitorView:
    return ChangeMonitorView()


def _rule(rule_id: str, *, enabled: bool = True) -> dict[str, Any]:
    return {"rule_id": rule_id, "name": rule_id, "url": "https://x/", "enabled": enabled}


def test_change_overview_is_hidden_before_any_check(qapp: QApplication) -> None:
    """★ 没做过检查时"有变化/无变化"是未知，不是 0 —— 不许画。"""
    monitor = _monitor()
    monitor._rules_data = [_rule("a"), _rule("b", enabled=False)]
    monitor._refresh_list()

    assert monitor._overview_chart.has_chart() is False
    assert monitor._overview_note.isHidden() is True


def test_change_overview_counts_changed_and_unchanged(qapp: QApplication) -> None:
    monitor = _monitor()
    monitor._rules_data = [_rule("a"), _rule("b"), _rule("c", enabled=False)]

    monitor._refresh_overview([{"rule_id": "a"}])

    assert monitor._overview_chart.values == (1.0, 1.0)  # 有变化 1 / 无变化 1
    note = monitor._overview_note.text()
    assert "已启用 2 条规则" in note
    assert "1 条有变化" in note
    assert "1 条已停用" in note


def test_change_overview_handles_no_enabled_rules(qapp: QApplication) -> None:
    monitor = _monitor()
    monitor._rules_data = [_rule("a", enabled=False)]

    monitor._refresh_overview([])

    assert monitor._overview_chart.has_chart() is False
    assert "没有已启用的规则" in monitor._overview_note.text()


def test_change_overview_is_invalidated_when_rules_change(qapp: QApplication) -> None:
    """规则集一变（增删启停），上次检查的结论就对不上了 ⇒ 必须清掉。"""
    monitor = _monitor()
    monitor._rules_data = [_rule("a")]
    monitor._refresh_overview([{"rule_id": "a"}])
    assert monitor._overview_chart.has_chart() is True

    monitor._refresh_list()

    assert monitor._overview_chart.has_chart() is False
    assert monitor._overview_note.isHidden() is True
