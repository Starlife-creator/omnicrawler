"""视图三态（空 / 加载 / 错误）的一致性契约。

**为什么需要这组测试**：`chart_view` 早已实现四态（加载中/未加载/为空/失败），但全部用
同一种灰色小字，导致「加载失败」与「没有数据」在视觉上无法区分——违反 §1.2 的
「错误不伪装成没有数据」（也是人工走查清单第 7 步要验的那条）。

这组测试覆盖的是**用户可感的差异**（错误态必须与空态可区分），而不是实现细节。
"""

from __future__ import annotations

import importlib.util

import pytest

pytestmark = pytest.mark.skipif(
    importlib.util.find_spec("PySide6") is None,
    reason="GUI tests require PySide6",
)


@pytest.fixture(autouse=True)
def _offscreen(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def test_chart_view_error_state_is_visually_distinct_from_empty(_offscreen) -> None:
    """错误态用危险色，空/未加载态不带任何覆盖样式——两者必须可区分。"""
    from omnicrawler.gui.views.chart_view import ChartView

    view = ChartView()

    view._set_summary("尚未加载结果统计")
    assert view._summary.styleSheet() == "", "非错误态不应携带覆盖样式"

    view._set_summary("结果文件为空")
    assert view._summary.styleSheet() == "", "空态不应被当作错误态"

    view._set_summary("加载失败：boom", error=True)
    sheet = view._summary.styleSheet()
    assert sheet.startswith("color:"), f"错误态应有颜色覆盖，实际 {sheet!r}"
    assert sheet != view._summary.styleSheet().replace("color:", "", 1)  # 非空即有效

    # 从错误态恢复到正常态时，覆盖样式必须被清掉（否则错误色会残留）
    view._set_summary("正在加载结果统计…")
    assert view._summary.styleSheet() == "", "错误态恢复后样式未清空（会残留危险色）"


def test_chart_view_does_not_recurse_in_summary_helper(_offscreen) -> None:
    """`_set_summary` 内部必须写回 QLabel，而不是自我调用（否则无限递归）。

    背景：批量改写 `self._summary.setText(...)` 时，若把辅助方法体内的那行也一并改写，
    就会形成自我调用——静态检查与其它测试都不会发现，只有真跑这条路径才炸。
    """
    from omnicrawler.gui.views.chart_view import ChartView

    view = ChartView()
    view._set_summary("冒烟")  # 若发生自我递归，这里会 RecursionError
    assert view._summary.text() == "冒烟"
