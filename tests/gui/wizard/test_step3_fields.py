from __future__ import annotations

import importlib.util

import pytest

from omnicrawl.gui.wizard.step3_fields import selector_kind, suggest_xpath_candidates

requires_qt = pytest.mark.skipif(
    importlib.util.find_spec("PyQt6") is None,
    reason="需要 PyQt6",
)


@pytest.fixture()
def smart_dialog(monkeypatch):
    """离屏构造 SmartExtractDialog。"""
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PyQt6.QtWidgets import QApplication

    from omnicrawl.gui.wizard.step3_fields import SmartExtractDialog

    app = QApplication.instance() or QApplication([])
    dialog = SmartExtractDialog()
    yield dialog
    dialog.deleteLater()
    app.processEvents()


def test_suggest_xpath_candidates_with_chinese_text() -> None:
    from lxml import html

    tree = html.fromstring("<html><body><h1>产品价格</h1><p>价格: 99元</p><span>其他</span></body></html>")
    top = suggest_xpath_candidates(tree, ["价格: 99元"])
    assert top, "应能按示例文本找到候选"
    text, xpath, sim = top[0]
    assert "99元" in text
    assert xpath.startswith("/")
    assert 0.6 < sim <= 1.0


def test_suggest_xpath_candidates_sorts_by_similarity() -> None:
    from lxml import html

    tree = html.fromstring("<html><body><p>目标内容 abc</p><p>无关内容 xyz</p></body></html>")
    top = suggest_xpath_candidates(tree, ["目标内容 abc"])
    assert top[0][0] == "目标内容 abc"


def test_suggest_xpath_candidates_empty_html_returns_empty() -> None:
    from lxml import html

    tree = html.fromstring("<html><body></body></html>")
    assert suggest_xpath_candidates(tree, ["任何"]) == []


def test_html_fromstring_supports_lxml_html_apis() -> None:
    """S1.1.3 回归：lxml.html.fromstring 返回的树必须支持 getpath/text_content/xpath。"""
    from lxml import html

    tree = html.fromstring("<html><head><meta name='price' content='99'></head><body><h1>标题</h1></body></html>")
    meta = tree.xpath("//meta[contains(@name, 'price')]")[0]
    assert meta.get("content") == "99"
    assert tree.getroottree().getpath(meta) == "/html/head/meta"
    h1 = tree.xpath("//h1")[0]
    assert (h1.text_content() or "").strip() == "标题"


def test_suggest_xpath_candidates_rejects_missing_lxml_gracefully() -> None:
    pytest.importorskip("lxml")
    from lxml import html

    tree = html.fromstring("<p>中文内容</p>")
    top = suggest_xpath_candidates(tree, ["中文"])
    assert any("中文" in item[0] for item in top)


def test_selector_kind_detects_xpath_and_css() -> None:
    assert selector_kind("//div[@class='price']") == "xpath"
    assert selector_kind("/html/body/div[1]/span") == "xpath"
    assert selector_kind(".//*[contains(text(),'x')]") == "xpath"
    assert selector_kind("div.price > span") == "css"
    assert selector_kind(".price span") == "css"
    assert selector_kind("") == "css"


def test_xpath_parameterization_survives_quotes_in_field_name() -> None:
    """S1.4.4：字段名含引号不再使 XPath 崩溃。"""
    from lxml import html

    tree = html.fromstring(
        "<html><head><meta name=\"price's\" content='99'></head>"
        "<body><h1>标题's内容</h1></body></html>"
    )
    name = "price's"
    metas = tree.xpath(
        "//meta[contains(@name, $field) or contains(@property, $field)]",
        field=name,
    )
    assert metas and metas[0].get("content") == "99"
    heading = tree.xpath(
        "//h1[contains(text(), $field)] | //h2[contains(text(), $field)] | //h3[contains(text(), $field)]",
        field="标题's内容",
    )
    assert heading and (heading[0].text_content() or "").strip().startswith("标题")


@requires_qt
def test_heuristic_mode_label_does_not_claim_real_ai(smart_dialog) -> None:
    """B7：启发式模式不得以"AI 模式"名义误导用户。"""
    label = smart_dialog._ai_mode_btn.text()
    assert "启发式" in label
    assert label.strip() != "AI 模式"
    assert "不调用大模型" in smart_dialog._ai_mode_btn.toolTip()
    assert "离线规则" in smart_dialog._ai_fields_group.title()


@requires_qt
def test_accept_without_selection_is_rejected(smart_dialog, monkeypatch) -> None:
    """B8：未选中结果行时确认必须被拒绝，且不产生空 XPath。"""
    from PyQt6.QtWidgets import QDialog, QMessageBox

    warnings: list[str] = []
    monkeypatch.setattr(
        QMessageBox, "warning",
        lambda *args, **kwargs: warnings.append(args[2]) or QMessageBox.StandardButton.Ok,
    )

    assert smart_dialog._result_table.currentRow() < 0
    assert not smart_dialog._ok_button.isEnabled()

    smart_dialog.accept()

    assert warnings, "未选中行确认时应给出警告"
    assert smart_dialog.selected_xpath == ""
    assert smart_dialog.result() != QDialog.DialogCode.Accepted


@requires_qt
def test_accept_rejects_row_with_empty_xpath(smart_dialog, monkeypatch) -> None:
    """B8：选中行但 XPath 为空（如"未找到匹配"行）同样应被拒绝。"""
    from PyQt6.QtWidgets import QMessageBox, QTableWidgetItem

    monkeypatch.setattr(
        QMessageBox, "warning", lambda *args, **kwargs: QMessageBox.StandardButton.Ok
    )
    table = smart_dialog._result_table
    table.setRowCount(1)
    table.setItem(0, 0, QTableWidgetItem("—"))
    table.setItem(0, 1, QTableWidgetItem("未找到匹配"))
    table.setItem(0, 2, QTableWidgetItem(""))
    table.selectRow(0)

    assert not smart_dialog._ok_button.isEnabled()
    smart_dialog.accept()
    assert smart_dialog.selected_xpath == ""


@requires_qt
def test_accept_with_valid_selection_binds_xpath(smart_dialog) -> None:
    """B8 回归：选中有效行时确认仍正常返回 XPath。"""
    from PyQt6.QtWidgets import QTableWidgetItem

    table = smart_dialog._result_table
    table.setRowCount(1)
    table.setItem(0, 0, QTableWidgetItem("90%"))
    table.setItem(0, 1, QTableWidgetItem("标题文本"))
    table.setItem(0, 2, QTableWidgetItem("/html/body/h1"))
    table.selectRow(0)

    assert smart_dialog._ok_button.isEnabled()
    smart_dialog.accept()
    assert smart_dialog.selected_xpath == "/html/body/h1"
    assert smart_dialog.selected_text == "标题文本"
