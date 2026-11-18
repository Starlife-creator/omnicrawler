"""Ctrl+K 全局命令面板与快速跳转单测（能力过滤 + 多词检索 + 键盘交互 + 快捷键契约）。"""

from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtCore import QSettings, Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QMainWindow  # noqa: E402

from omnicrawler.gui.command_palette import (  # noqa: E402
    CommandPaletteDialog,
    build_palette_commands,
    filter_palette_commands,
)
from omnicrawler.gui.navigation import NavKey  # noqa: E402
from omnicrawler.gui.settings import AppSettings  # noqa: E402
from omnicrawler.gui.shortcuts import GlobalShortcutManager  # noqa: E402

_APP: QApplication | None = None


@pytest.fixture(autouse=True)
def _ensure_qt_app(tmp_path, monkeypatch: pytest.MonkeyPatch) -> QApplication:
    global _APP
    _APP = QApplication.instance() or QApplication([])  # type: ignore[assignment]
    assert _APP is not None
    monkeypatch.setattr(
        "omnicrawler.gui.settings.make_qsettings",
        lambda *_args: QSettings(
            str(tmp_path / "settings.ini"), QSettings.Format.IniFormat
        ),
    )
    AppSettings.reset()
    yield _APP
    AppSettings.reset()


def _make_stub_window() -> SimpleNamespace:
    calls: list[str] = []
    settings = SimpleNamespace(
        theme="light",
        shortcuts=AppSettings.instance().shortcuts,
    )
    return SimpleNamespace(
        calls=calls,
        _settings=settings,
        _dnd_mode=False,
        _nav_row_by_key={NavKey.HOME: 1, NavKey.WORKSPACE: 2, NavKey.YAML_EDITOR: 14},
        _nav=SimpleNamespace(
            item=lambda _row: None,
            setCurrentRow=lambda row: calls.append(f"nav_row:{row}"),
        ),
        _request_run=lambda: calls.append("run"),
        _request_trial_run=lambda: calls.append("trial_run"),
        _run_delegate=SimpleNamespace(stop_task=lambda: calls.append("stop")),
        _config_delegate=SimpleNamespace(save_config=lambda: calls.append("save")),
        _toggle_workspace_editor=lambda: calls.append("toggle_editor"),
        _show_template_library=lambda: calls.append("open_templates"),
        _refresh_results_page=lambda: calls.append("refresh"),
        _format_yaml_from_shortcut=lambda: calls.append("format_yaml"),
        _theme_manager=SimpleNamespace(
            toggle_dnd=lambda enabled: calls.append(f"dnd:{enabled}"),
            set_theme=lambda theme: calls.append(f"theme:{theme}"),
        ),
        _help_center=SimpleNamespace(
            show=lambda: calls.append("help_show"),
            raise_=lambda: calls.append("help_raise"),
        ),
    )


def test_palette_respects_capability_gating_no_dead_entries() -> None:
    """反向断言：未安装 pdf/qml 能力时，面板中绝不出现对应死页面入口；就绪时完整出现。"""
    stub = _make_stub_window()
    minimal_cmds = build_palette_commands(stub, capabilities={"pdf": False, "qml": False})
    minimal_nav_keys = {cmd.nav_key for cmd in minimal_cmds if cmd.nav_key is not None}

    assert NavKey.PDF_WORKBENCH not in minimal_nav_keys
    assert NavKey.CONVERT_TOOL not in minimal_nav_keys
    assert NavKey.QML_SHOWCASE not in minimal_nav_keys
    assert NavKey.HOME in minimal_nav_keys
    assert NavKey.PLUGIN_MARKET in minimal_nav_keys

    full_cmds = build_palette_commands(stub, capabilities={"pdf": True, "qml": True})
    full_nav_keys = {cmd.nav_key for cmd in full_cmds if cmd.nav_key is not None}
    assert NavKey.PDF_WORKBENCH in full_nav_keys
    assert NavKey.CONVERT_TOOL in full_nav_keys
    assert NavKey.QML_SHOWCASE in full_nav_keys


def test_palette_includes_all_core_global_actions_and_shortcuts() -> None:
    stub = _make_stub_window()
    cmds = build_palette_commands(stub, capabilities={"pdf": False, "qml": False})
    by_id = {cmd.command_id: cmd for cmd in cmds}

    expected_actions = {
        "action:run",
        "action:stop",
        "action:save",
        "action:toggle_editor",
        "action:open_templates",
        "action:refresh",
        "action:format_yaml",
        "action:toggle_dnd",
        "action:toggle_theme",
        "action:help_center",
    }
    assert expected_actions <= set(by_id)
    assert by_id["action:save"].shortcut == "Ctrl+S"
    assert by_id["action:run"].shortcut == "Ctrl+R"
    assert by_id["action:refresh"].shortcut == "F5"

    by_id["action:toggle_theme"].callback()  # type: ignore[misc]
    by_id["action:help_center"].callback()  # type: ignore[misc]
    assert "theme:dark" in stub.calls
    assert "help_show" in stub.calls


def test_multi_keyword_and_pinyin_filtering() -> None:
    stub = _make_stub_window()
    cmds = build_palette_commands(stub, capabilities={"pdf": True, "qml": True})

    market_hits = filter_palette_commands(cmds, "market")
    assert market_hits
    assert market_hits[0].command_id == f"nav:{NavKey.PLUGIN_MARKET}"

    pinyin_hits = filter_palette_commands(cmds, "bianjiqi")
    assert any(cmd.nav_key == NavKey.YAML_EDITOR for cmd in pinyin_hits)

    multi_hits = filter_palette_commands(cmds, "yaml format")
    assert [cmd.command_id for cmd in multi_hits] == ["action:format_yaml"]

    assert filter_palette_commands(cmds, "nonexistent_zzz_token") == []


def test_dialog_keyboard_navigation_execute_and_escape() -> None:
    stub = _make_stub_window()
    dialog = CommandPaletteDialog(stub, capabilities={"pdf": False, "qml": False})
    assert dialog.accessibleName() != ""
    assert dialog.result_list.count() == len(dialog.filtered_commands)

    dialog.search_input.setText("yaml")
    assert len(dialog.filtered_commands) >= 2
    first_id = dialog.filtered_commands[0].command_id
    second_id = dialog.filtered_commands[1].command_id

    QTest.keyClick(dialog.search_input, Qt.Key.Key_Down)
    assert dialog.result_list.currentRow() == 1
    QTest.keyClick(dialog.search_input, Qt.Key.Key_Up)
    assert dialog.result_list.currentRow() == 0
    assert dialog.filtered_commands[dialog.result_list.currentRow()].command_id == first_id
    assert second_id != first_id

    QTest.keyClick(dialog.search_input, Qt.Key.Key_Down)
    QTest.keyClick(dialog.search_input, Qt.Key.Key_Return)
    assert dialog.executed_command_id == second_id

    dialog2 = CommandPaletteDialog(stub, capabilities={"pdf": False, "qml": False})
    dialog2.search_input.setText("zzzz_nothing_matches")
    assert dialog2.execute_button.isEnabled() is False
    assert dialog2.execute_selected() is False
    QTest.keyClick(dialog2.search_input, Qt.Key.Key_Escape)
    assert dialog2.executed_command_id is None


def test_shortcut_manager_preserves_partial_registration_and_wires_ctrl_k() -> None:
    assert AppSettings.instance().shortcuts["command_palette"] == "Ctrl+K"

    win_isolated = QMainWindow()
    mgr_isolated = GlobalShortcutManager(win_isolated)
    mgr_isolated.register_all({"save": lambda: None})
    assert mgr_isolated.registered_keys == ["save"]
    win_isolated.deleteLater()

    opened: list[int] = []
    win_full = QMainWindow()
    win_full._central_area = SimpleNamespace(  # type: ignore[attr-defined]
        open_command_palette=lambda: opened.append(1)
    )
    mgr_full = GlobalShortcutManager(win_full)
    mgr_full.register_all(
        {
            "save": lambda: None,
            "run": lambda: None,
            "stop": lambda: None,
            "toggle_editor": lambda: None,
        }
    )
    assert "command_palette" in mgr_full.registered_keys
    action = mgr_full.get_action("command_palette")
    assert action is not None
    action.trigger()
    assert opened == [1]
    win_full.deleteLater()
