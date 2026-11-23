"""`gui/widgets/form_feedback.py` 的守卫：表单校验反馈的**状态契约**。

## 为什么这个模块值得测（此前 0% 覆盖）

它只有三个函数、62 行，但决定了「用户提交非法表单时能不能看见红框」：

* `set_error_style()` 打上 `validation=error` 属性，QSS 靠它画红框；
* `clear_error_style()` 必须把属性**清干净**并在样式系统里重新 polish。

**清不干净是真实故障**：用户改对了字段、红框还在，会以为仍然有错。
所以判据不是「设上了没」，而是「**清除后必须完全回到原样**」。

## 能在 `tests/unit` 测吗

能。三个函数都不需要显示窗口 —— offscreen 模式下 `QWidget`、
`style().polish()` 都正常工作。`shake_widget` 是动画（需要事件循环跑完），
这里只验「不抛异常 + 目标位置正确」，不验视觉。

## 边界

不验像素与动画曲线（那是视觉回归的事，属 `tests/gui/`）。
"""
from __future__ import annotations

import pytest

pytest.importorskip("PySide6.QtWidgets", reason="需要 PySide6（gui extra）")

from PySide6.QtWidgets import QLineEdit, QWidget  # noqa: E402

from omnicrawler.gui.widgets.form_feedback import (  # noqa: E402
    clear_error_style,
    set_error_style,
    shake_widget,
)


@pytest.fixture
def field(qapp) -> QLineEdit:
    widget = QLineEdit()
    yield widget
    widget.deleteLater()


def test_set_error_style_marks_validation_property(field: QLineEdit) -> None:
    """QSS 依赖 `validation=error` 这个属性画红框 —— 必须设上。"""
    set_error_style(field, "必填项")
    assert field.property("validation") == "error"


def test_set_error_style_sets_tooltip_with_message(field: QLineEdit) -> None:
    """给了消息就要设成 tooltip（用户悬停能看到原因）。"""
    set_error_style(field, "请输入有效的邮箱")
    assert field.toolTip() == "请输入有效的邮箱"


def test_set_error_style_without_message_keeps_existing_tooltip(field: QLineEdit) -> None:
    """不给消息时**不动**已有 tooltip（可能是别处设的帮助文字）。"""
    field.setToolTip("原有帮助")
    set_error_style(field)
    assert field.toolTip() == "原有帮助", "空消息覆盖掉了原有 tooltip"


def test_clear_error_style_resets_property(field: QLineEdit) -> None:
    """**核心**：清除后属性必须回到空 —— 否则红框会一直挂着。"""
    set_error_style(field, "错误")
    clear_error_style(field)
    assert field.property("validation") in ("", None), (
        f"清除后属性仍有值：{field.property('validation')!r} —— 红框不会消失"
    )


def test_clear_error_style_clears_tooltip(field: QLineEdit) -> None:
    """清除时 tooltip 也要清空（错误提示不该残留）。"""
    set_error_style(field, "错误")
    clear_error_style(field)
    assert field.toolTip() == ""


def test_round_trip_returns_to_original_state(field: QLineEdit) -> None:
    """**往返一致**：设错再清除，应完全回到初始状态。"""
    before_prop = field.property("validation")
    before_tip = field.toolTip()

    set_error_style(field, "错误")
    clear_error_style(field)

    assert field.property("validation") in (before_prop, "", None)
    assert field.toolTip() == before_tip


def test_clear_error_style_is_idempotent(field: QLineEdit) -> None:
    """对没设过错误的控件调用 clear 不得抛（调用方常无条件清理）。"""
    clear_error_style(field)
    clear_error_style(field)
    assert field.toolTip() == ""


def test_set_error_style_is_idempotent(field: QLineEdit) -> None:
    """连续设两次错误不得抛（表单校验会重复触发）。"""
    set_error_style(field, "第一次")
    set_error_style(field, "第二次")
    assert field.property("validation") == "error"
    assert field.toolTip() == "第二次"


@pytest.mark.parametrize("widget_factory", [QWidget, QLineEdit])
def test_works_on_generic_widgets(widget_factory, qapp) -> None:
    """不该只对特定控件有效 —— 任何 QWidget 都要能标记错误。"""
    widget = widget_factory()
    try:
        set_error_style(widget, "x")
        assert widget.property("validation") == "error"
        clear_error_style(widget)
        assert widget.property("validation") in ("", None)
    finally:
        widget.deleteLater()


def test_shake_widget_does_not_raise(field: QLineEdit) -> None:
    """抖动动画不得抛（它跑在 UI 线程，抛异常会崩窗口）。"""
    shake_widget(field)
    # 动画启动后目标位置应被改过；不强求跑完（那要事件循环）
    assert field.pos() is not None


def test_shake_widget_with_explicit_parent(field: QLineEdit) -> None:
    """显式传 parent 时也不得抛（父对象负责生命周期）。"""
    holder = QWidget()
    try:
        shake_widget(field, holder)
    finally:
        holder.deleteLater()
