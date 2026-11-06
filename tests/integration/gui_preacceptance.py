"""Automated GUI pre-acceptance coverage not supplied by existing suites.

Real worker cancellation/reaping/resume is covered by
``integration/test_gui_worker_local_task.py``; individual worker and widget
shutdown paths live under ``tests/gui``.  This file exercises the remaining
end-to-end keyboard boundary using real Qt shortcut dispatch.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QSettings, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QLineEdit, QMainWindow

from omnicrawler.gui.settings import AppSettings
from omnicrawler.gui.shortcuts import GlobalShortcutManager


@pytest.fixture(autouse=True)
def _isolated_shortcut_settings(tmp_path, monkeypatch):
    """Use documented defaults without reading or changing user shortcuts."""
    monkeypatch.setattr(
        "omnicrawler.gui.settings.make_qsettings",
        lambda *_args: QSettings(
            str(tmp_path / "settings.ini"), QSettings.Format.IniFormat
        ),
    )
    AppSettings.reset()
    yield
    AppSettings.reset()


@pytest.mark.parametrize(
    ("name", "key", "modifiers"),
    [
        ("save", Qt.Key.Key_S, Qt.KeyboardModifier.ControlModifier),
        ("run", Qt.Key.Key_R, Qt.KeyboardModifier.ControlModifier),
        (
            "stop",
            Qt.Key.Key_S,
            Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier,
        ),
        ("toggle_editor", Qt.Key.Key_E, Qt.KeyboardModifier.ControlModifier),
        ("open_templates", Qt.Key.Key_T, Qt.KeyboardModifier.ControlModifier),
        ("refresh", Qt.Key.Key_F5, Qt.KeyboardModifier.NoModifier),
        (
            "format_yaml",
            Qt.Key.Key_F,
            Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier,
        ),
        (
            "toggle_dnd",
            Qt.Key.Key_M,
            Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier,
        ),
    ],
)
def test_global_shortcut_reaches_action_while_child_editor_has_focus(
    name: str,
    key: Qt.Key,
    modifiers: Qt.KeyboardModifier,
) -> None:
    """Typing in a child editor must not disable application-wide commands."""
    window = QMainWindow()
    editor = QLineEdit(window)
    window.setCentralWidget(editor)
    window.show()
    window.activateWindow()
    editor.setFocus()
    QTest.qWaitForWindowActive(window)

    calls: list[str] = []
    manager = GlobalShortcutManager(window)
    manager.register_all({name: lambda: calls.append(name)})

    assert editor.hasFocus()
    QTest.keyClick(editor, key, modifiers)

    assert calls == [name]
    window.close()
