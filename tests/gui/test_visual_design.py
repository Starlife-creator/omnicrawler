from __future__ import annotations

import pytest

pytest.importorskip("PyQt6", reason="visual design tests require the optional PyQt6 dependency")
from omnicrawl.gui.design_system import (
    DARK,
    HIGH_CONTRAST,
    LIGHT,
    PageTransitionController,
    stylesheet,
    theme_tokens,
)


def _luminance(value: str) -> float:
    rgb = [int(value[index:index + 2], 16) / 255 for index in (1, 3, 5)]
    values = [item / 12.92 if item <= .04045 else ((item + .055) / 1.055) ** 2.4 for item in rgb]
    return values[0] * .2126 + values[1] * .7152 + values[2] * .0722


def _contrast(one: str, two: str) -> float:
    high, low = sorted((_luminance(one), _luminance(two)), reverse=True)
    return (high + .05) / (low + .05)


def test_visual_tokens_have_readable_text_focus_and_accessibility_variants():
    assert _contrast(LIGHT.text, LIGHT.canvas) >= 7
    assert _contrast(DARK.text, DARK.canvas) >= 7
    assert _contrast(HIGH_CONTRAST.text, HIGH_CONTRAST.canvas) >= 20
    qss = stylesheet(LIGHT)
    assert "QPushButton:focus" in qss
    assert "QLineEdit:focus" in qss
    assert "background: transparent" in qss
    color_blind = theme_tokens("light", color_blind_friendly=True)
    assert color_blind.primary == "#0072B2"
    assert theme_tokens("dark") == DARK


def test_visual_theme_home_transition_and_help_visibility(monkeypatch, tmp_path):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PyQt6.QtTest import QTest
    from PyQt6.QtWidgets import QApplication, QFrame, QGraphicsDropShadowEffect

    from omnicrawl.gui.home import AmbientHero
    from omnicrawl.gui.main import MainWindow

    monkeypatch.setattr(MainWindow, "_on_first_launch", lambda self: None)
    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    window.resize(1280, 760)
    window.show()
    QTest.qWait(220)
    assert window._stack.currentIndex() == 4
    assert window._help_center.isHidden()
    assert window._nav.objectName() == "mainNavigation"
    assert isinstance(window._home.findChild(AmbientHero), AmbientHero)
    card = window._home.findChild(QFrame, "quickTaskCard")
    assert isinstance(card.graphicsEffect(), QGraphicsDropShadowEffect)

    window._set_theme("light")
    before = app.palette().window().color().name()
    window._set_theme("dark")
    after = app.palette().window().color().name()
    assert before != after
    image = tmp_path / "visual-smoke.png"
    assert window.grab().save(str(image))
    assert image.stat().st_size > 1000
    # 在关闭窗口前处理待处理事件，避免 processEvents 时访问已析构的 widget
    app.processEvents()
    window.close()
    app.processEvents()


def test_page_transition_respects_reduced_motion(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PyQt6.QtWidgets import QApplication, QLabel, QStackedWidget

    app = QApplication.instance() or QApplication([])
    stack = QStackedWidget()
    stack.addWidget(QLabel("one"))
    stack.addWidget(QLabel("two"))
    controller = PageTransitionController(stack, reduced_motion=True)
    controller.show(1)
    assert stack.currentIndex() == 1
    assert stack.currentWidget().graphicsEffect() is None
    app.processEvents()


def test_page_transition_ignores_completion_after_its_page_is_deleted(monkeypatch):
    """Queued animation completion must not call into a Qt object after teardown."""
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PyQt6.QtTest import QTest
    from PyQt6.QtWidgets import QApplication, QLabel, QStackedWidget

    app = QApplication.instance() or QApplication([])
    stack = QStackedWidget()
    stack.addWidget(QLabel("one"))
    stack.addWidget(QLabel("two"))
    controller = PageTransitionController(stack)
    controller.show(1)
    stack.deleteLater()
    app.processEvents()
    QTest.qWait(200)
    app.processEvents()
