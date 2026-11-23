"""`gui/widgets/calendar_popup.py` 的守卫：日期选择弹窗的**契约行为**。

## 为什么这个模块值得测（此前 0% 覆盖）

它是 GUI 组件，容易被「GUI 靠人工点」的惯性跳过。但它的核心是**纯逻辑**：

* `set_date()` 必须把 `"YYYY-MM-DD"` 字符串同步到日历（否则打开时高亮错日期）；
* 用户选中日期 ⇒ 发 `date_selected`，**载荷是 `"YYYY-MM-DD"` 字符串**；
* 「清除」⇒ 发**空串**（调用方据此把字段清空，语义不同于「取消」）；
* 「取消」⇒ **不发信号**。

后两条是真正的事故源：把「取消」当成「清除」会**悄悄清掉用户原有的值**。

## 边界

offscreen 下不验外观与像素。只验信号契约与状态同步 —— 那恰好是调用方
真正依赖的部分。真实点击由 Qt 的事件循环投递，这里直接调用槽函数，
因为槽函数就是「点击之后会发生什么」的唯一真源。
"""
from __future__ import annotations

import datetime

import pytest

pytest.importorskip("PySide6.QtWidgets", reason="需要 PySide6（gui extra）")

from PySide6.QtCore import QDate  # noqa: E402
from PySide6.QtWidgets import QPushButton  # noqa: E402

from omnicrawler.gui.widgets.calendar_popup import CalendarPopup  # noqa: E402


@pytest.fixture
def popup(qapp) -> CalendarPopup:
    widget = CalendarPopup()
    yield widget
    widget.close()
    widget.deleteLater()


def test_set_date_syncs_the_calendar(popup: CalendarPopup) -> None:
    """`set_date("YYYY-MM-DD")` 必须真的同步到日历控件。"""
    popup.set_date("2026-03-15")
    actual = popup._calendar.selectedDate().toString("yyyy-MM-dd")
    assert actual == "2026-03-15", f"set_date 未生效：实际 {actual}"


def test_set_date_tolerates_invalid_input(popup: CalendarPopup) -> None:
    """非法日期 / None / 空串都**不得抛错**（弹窗打开时字段可能为空）。"""
    before = popup._calendar.selectedDate().toString("yyyy-MM-dd")
    for bad in (None, "", "not-a-date", "2026-13-45"):
        popup.set_date(bad)
    # 非法输入应保持原值，而不是把日历置为无效
    after = popup._calendar.selectedDate().toString("yyyy-MM-dd")
    assert after == before, f"非法输入改变了选中日期：{before} -> {after}"


def test_clicking_a_date_emits_iso_string(qapp) -> None:
    """点日期 ⇒ 发 `date_selected`，载荷是 `YYYY-MM-DD` 字符串。"""
    widget = CalendarPopup()
    received: list[str] = []
    widget.date_selected.connect(received.append)
    try:
        widget._on_date_clicked(QDate(2026, 7, 1))
        assert received == ["2026-07-01"], f"信号载荷不对：{received}"
    finally:
        widget.close()
        widget.deleteLater()


def test_today_button_emits_todays_date(qapp) -> None:
    """「今天」按钮必须发**当天**的 ISO 串。"""
    widget = CalendarPopup()
    received: list[str] = []
    widget.date_selected.connect(received.append)
    try:
        widget._select_today()
        today = datetime.date.today().isoformat()
        assert received == [today], f"期望 {today}，实际 {received}"
    finally:
        widget.close()
        widget.deleteLater()


def test_clear_emits_empty_string(qapp) -> None:
    """「清除」必须发**空串** —— 这是「把字段清空」的信号，与「取消」不同。"""
    widget = CalendarPopup()
    received: list[str] = []
    widget.date_selected.connect(received.append)
    try:
        widget._clear()
        assert received == [""], f"清除应发空串，实际 {received}"
    finally:
        widget.close()
        widget.deleteLater()


def test_cancel_button_emits_nothing(qapp) -> None:
    """**反向断言**：点「取消」按钮不得发信号。

    若把取消按钮接到 `_clear`（或任何发信号的槽），用户点「取消」就会
    **悄悄清掉原有日期** —— 这是本组件最危险的误用。

    ★ 必须**点真实的按钮**而不是直接调 `reject()`：
    早先我写的是 `widget.reject()`，那样只验了 `reject` 本身不发信号，
    而**按钮接线错了它照样绿**。`reverse_assert` 当场抓住了这个假守卫
    （把 `connect(self.reject)` 改成 `connect(self._clear)` 后测试仍通过）。
    """
    widget = CalendarPopup()
    received: list[str] = []
    widget.date_selected.connect(received.append)
    try:
        widget.set_date("2026-07-01")
        # 用 i18n 的取词函数（与实现同一来源），避免写死语言
        from omnicrawler.i18n import _ as tr

        cancel = next(
            btn for btn in widget.findChildren(QPushButton)
            if btn.text() == tr("取消")
        )
        cancel.click()          # 走真实信号链
        assert not received, f"点取消仍发了信号：{received}"
    finally:
        widget.close()
        widget.deleteLater()


def test_every_other_button_emits_as_expected(qapp) -> None:
    """「今天」与「清除」也必须**点真实按钮**验证，而不是直接调槽函数。

    同 `test_cancel_button_emits_nothing` 的教训：直接调槽函数只验了槽本身，
    验不了「按钮接到了哪个槽」。
    """
    from omnicrawler.i18n import _ as tr

    for label, expected in (
        (tr("今天"), datetime.date.today().isoformat()),
        (tr("清除"), ""),
    ):
        widget = CalendarPopup()
        received: list[str] = []
        widget.date_selected.connect(received.append)
        try:
            button = next(
                btn for btn in widget.findChildren(QPushButton) if btn.text() == label
            )
            button.click()
            assert received == [expected], (
                f"点「{label}」应发 {expected!r}，实际 {received}"
            )
        finally:
            widget.close()
            widget.deleteLater()
