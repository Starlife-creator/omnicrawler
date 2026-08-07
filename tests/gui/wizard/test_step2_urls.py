from __future__ import annotations

import importlib.util

import pytest

pytestmark = pytest.mark.skipif(
    importlib.util.find_spec("PyQt6") is None,
    reason="需要 PyQt6",
)


@pytest.fixture()
def page(monkeypatch):
    """离屏构造 Step2UrlsPage。"""
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PyQt6.QtWidgets import QApplication

    from omnicrawl.gui.core.config_model import CrawlConfig
    from omnicrawl.gui.wizard.step2_urls import Step2UrlsPage

    app = QApplication.instance() or QApplication([])
    widget = Step2UrlsPage(CrawlConfig())
    yield widget
    widget.deleteLater()
    app.processEvents()


def test_placeholder_prompt_offers_return_path(page, monkeypatch) -> None:
    """B16：占位符未替换时，"否"应留在本页并定位到占位符供替换。"""
    from PyQt6.QtWidgets import QMessageBox

    prompts: list[str] = []

    def fake_question(*args, **kwargs):
        prompts.append(args[2])
        return QMessageBox.StandardButton.No

    monkeypatch.setattr(QMessageBox, "question", fake_question)

    page._url_edit.setPlainText("https://example.com/{{category}}/list")
    assert page.validatePage() is False

    assert prompts and "返回本页替换占位符" in prompts[0]
    # 光标已选中第一个占位符，用户可直接改写
    assert page._url_edit.textCursor().selectedText() == "{{category}}"
    assert page._url_edit.property("validation") == "error"
    assert page._placeholder_hint.isVisibleTo(page)


def test_placeholder_prompt_still_allows_continue(page, monkeypatch) -> None:
    """B16 回归：明确选择继续时不阻断既有流程。"""
    from PyQt6.QtWidgets import QMessageBox

    monkeypatch.setattr(
        QMessageBox, "question", lambda *args, **kwargs: QMessageBox.StandardButton.Yes
    )

    page._url_edit.setPlainText("https://example.com/{{category}}/list")
    assert page.validatePage() is True
    assert page._config.seed_urls == ["https://example.com/{{category}}/list"]


def test_focus_first_placeholder_returns_false_without_placeholder(page) -> None:
    page._url_edit.setPlainText("https://example.com/news")
    assert page._focus_first_placeholder() is False


def test_validate_page_without_placeholder_passes(page, monkeypatch) -> None:
    from PyQt6.QtWidgets import QMessageBox

    def fail_question(*args, **kwargs):  # pragma: no cover - 不应被调用
        raise AssertionError("无占位符时不应弹出确认框")

    monkeypatch.setattr(QMessageBox, "question", fail_question)
    page._url_edit.setPlainText("https://example.com/news")
    assert page.validatePage() is True
