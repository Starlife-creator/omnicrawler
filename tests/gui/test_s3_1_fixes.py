"""S3.1 包回归测试：redact 正则 / help 未知 id / NavIndex / 日志裁剪。"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication

from omnicrawler.gui.delegates.error_dialog import ErrorDialogHelper
from omnicrawler.gui.help_center import HelpCenterDock
from omnicrawler.gui.main import NavIndex

_app = QApplication.instance() or QApplication([])


# ── S3.1.14：error_dialog 正则误伤 ──────────────────────────────────

def test_redact_keeps_plain_questions() -> None:
    helper = ErrorDialogHelper.__new__(ErrorDialogHelper)
    text = "为什么失败了？请重试？这是普通句子。"
    assert helper.redact_error(text) == text


def test_redact_removes_credential_query_params() -> None:
    helper = ErrorDialogHelper.__new__(ErrorDialogHelper)
    redacted = helper.redact_error("request failed?token=abc123&page=2")
    assert "[REDACTED]" in redacted
    assert "abc123" not in redacted


def test_redact_keeps_selector_and_url_rules() -> None:
    helper = ErrorDialogHelper.__new__(ErrorDialogHelper)
    redacted = helper.redact_error('selector=".price" https://example.org/secret/path')
    assert "https://[REDACTED]" in redacted
    assert '[REDACTED]' in redacted  # selector 值被替换


# ── S3.1.12：help_center 未知 id 防护 ───────────────────────────────

def test_help_center_unknown_id_does_not_update_current(tmp_path: Path) -> None:
    center = HelpCenterDock()
    center.set_context("simple", {})
    known = center._current_id
    center.show_help("unknown-id-xyz", reveal=False)
    assert center._current_id == known  # 未知 id 不写入 _current_id
    center._copy_example()  # 不再 KeyError


# ── S3.1.2/15：NavIndex 常量（侧栏行号，0-based；须与 main.py nav_items 逐行一致） ──

def test_nav_index_constants() -> None:
    assert NavIndex.WORK_HEADER == 0
    assert NavIndex.HOME == 1
    assert NavIndex.WORKSPACE == 2
    assert NavIndex.WIZARD == NavIndex.WORKSPACE  # 旧扩展兼容别名
    assert NavIndex.MONITOR == 3
    assert NavIndex.RESULTS == 4
    assert NavIndex.CHANGE_MONITOR == 6
    assert NavIndex.PDF_WORKBENCH == 8
    assert NavIndex.CONVERT_TOOL == 9
    assert NavIndex.SCENE == 10
    assert NavIndex.YAML_EDITOR == 12
    assert NavIndex.EVIDENCE == 13
    assert NavIndex.PLUGIN_MARKET == 14
    assert NavIndex.DEVELOPER == 15


# ── S3.1.4：日志缓存裁剪 ────────────────────────────────────────────

def test_log_console_caps_cached_logs(tmp_path: Path) -> None:
    from omnicrawler.gui.widgets.log_console import MAX_CACHED_LOGS, LogConsole

    console = LogConsole()
    for index in range(MAX_CACHED_LOGS + 500):
        console.append_log(f"line {index}", "info")
    assert len(console._all_logs) <= MAX_CACHED_LOGS
    assert console._all_logs[0][0] == "line 500"  # 最旧被裁剪
