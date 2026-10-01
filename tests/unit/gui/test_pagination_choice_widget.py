"""分页控件的**两种控件**（文本框 / 下拉）必须语义一致，且**绝不静默改写**用户的值。

背景（2026-09-30）：`pagination.location` 从 `editable=False` 改为可编辑 ⇒ 契约里的 `CHOICE`
字段第一次真正进表单。渲染器此前**一律建 `QLineEdit`** ⇒ 用户得手打 `query`/`body`（打错只在
运行时才炸）。现在按下表建控件，读/写各收敛到**一个助手**里：

| 契约 | 控件 |
|---|---|
| `CHOICE` | 下拉（选项＝契约的 `choices`） |
| 其余 | 文本框 |

★ 本文件最要紧的一条是"未知值"那条：下拉里装不下一个不在 `choices` 里的值，而**静默改成别的值**
是本仓明确反对的一类缺陷（"用户配置被悄悄改写"）。正解＝把它**作为一项插进去**再选中。
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication, QComboBox, QLineEdit  # noqa: E402

from omnicrawler.gui.views.task_canvas_draft import (  # noqa: E402
    _pagination_value,
    _set_pagination_value,
)


@pytest.fixture()
def qapp():
    return QApplication.instance() or QApplication([])


def test_line_edit_round_trips_a_plain_value(qapp) -> None:
    edit = QLineEdit()
    _set_pagination_value(edit, "page")
    assert _pagination_value(edit) == "page"


def test_combo_round_trips_a_listed_value(qapp) -> None:
    combo = QComboBox()
    for choice in ("query", "body"):
        combo.addItem(choice, choice)
    _set_pagination_value(combo, "body")
    assert _pagination_value(combo) == "body"


def test_combo_keeps_an_unlisted_value_instead_of_rewriting_it(qapp) -> None:
    """★★ 安全断言：不在下拉列表里的值 —— **保留它**，绝不悄悄改成第一个选项。

    这条不是"边界情况"：插件的分页配置可以带契约外的键/值，而"打开一个能用的配置、存一下
    就被改写"是本仓**已有过同类事故**的一类缺陷（见 `_load_pagination` 的 docstring）。
    """
    combo = QComboBox()
    for choice in ("query", "body"):
        combo.addItem(choice, choice)

    _set_pagination_value(combo, "cookie")   # 契约校验会拒它，但表单**不能**替用户改

    assert _pagination_value(combo) == "cookie"
    assert combo.findData("cookie") >= 0, "未知值应被插入为一项，而不是被丢弃"


def test_combo_empty_value_selects_the_blank_entry(qapp) -> None:
    """清空（换形状时会走这条）：选回空项，而不是插入一个空字符串项。"""
    combo = QComboBox()
    combo.addItem("", "")
    for choice in ("query", "body"):
        combo.addItem(choice, choice)
    _set_pagination_value(combo, "body")

    _set_pagination_value(combo, "")

    assert _pagination_value(combo) == ""
    assert combo.count() == 3, "清空不该往下拉里塞新项"


def test_combo_without_any_item_does_not_raise(qapp) -> None:
    """空下拉也不能抛（防御：契约若声明了 `choices=()` 的 CHOICE 字段）。"""
    combo = QComboBox()
    _set_pagination_value(combo, "")
    assert _pagination_value(combo) == ""
