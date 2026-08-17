from __future__ import annotations

import csv
import json
import time
from datetime import datetime, timedelta

import pytest


@pytest.fixture(scope="module")
def qt_app():
    widgets = pytest.importorskip("PyQt6.QtWidgets")

    return widgets.QApplication.instance() or widgets.QApplication([])


def test_result_table_stream_filter_evidence_paging_and_exports(qt_app, tmp_path, monkeypatch):
    from PyQt6.QtCore import QModelIndex
    from PyQt6.QtGui import QDesktopServices
    from PyQt6.QtWidgets import QFileDialog, QMessageBox, QProgressDialog

    from omnicrawler.gui.views.result_table import CsvStreamModel, ExportThread, ResultTable

    csv_path = tmp_path / "records.csv"
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["record_id", "title", "value"])
        for index in range(1105):
            writer.writerow([f"r{index}", f"title {index}", index])
    (tmp_path / "records.jsonl").write_text(
        '{bad json}\n' + json.dumps({"record_id": "r0", "evidence": {"selector": "h1"}}) + "\n",
        encoding="utf-8",
    )
    model = CsvStreamModel()
    assert model.load_file(tmp_path / "missing.csv") is False
    assert model.load_file(csv_path) is True
    assert model.total_rows == 1105 and model.total_pages == 2
    assert model.rowCount() == 1000 and model.columnCount() == 3
    assert model.data(QModelIndex()) is None
    assert model.data(model.index(0, 1)) == "title 0"
    assert model.headerData(0, __import__("PyQt6.QtCore", fromlist=["Qt"]).Qt.Orientation.Horizontal) == "record_id"
    assert model.canFetchMore(QModelIndex()) is False
    model.go_to_page(999)
    model.go_to_page(1)
    assert model.rowCount() == 105

    view = ResultTable()
    assert view.load_csv(tmp_path / "missing.csv") is False
    assert view.load_csv(csv_path) is True

    # 等待异步 CSV 索引完成（模型有行数据后才能触发 _show_evidence）
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        qt_app.processEvents()
        if view._model.rowCount() > 0:
            break
        time.sleep(0.02)
    assert view._model.rowCount() > 0, "CSV 索引超时"

    view._apply_filter("title 10")
    view._apply_filter("")
    view._show_evidence(view._proxy.index(0, 0), QModelIndex())
    # 等待异步证据加载完成
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        qt_app.processEvents()
        if "selector" in view._evidence.toPlainText():
            break
        time.sleep(0.02)
    assert "selector" in view._evidence.toPlainText()
    view._current_page = 1
    view._go_first()
    view._go_next()
    view._go_prev()
    view._go_last()
    view._go_to_page(1)
    view.refresh()

    # 等待 refresh() 触发的异步 CSV 索引完成
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        qt_app.processEvents()
        if view._model.rowCount() > 0:
            break
        time.sleep(0.02)

    messages = []
    monkeypatch.setattr(QMessageBox, "information", lambda *args: messages.append(args[2]) or QMessageBox.StandardButton.Ok)
    monkeypatch.setattr(QMessageBox, "critical", lambda *args: messages.append(args[2]) or QMessageBox.StandardButton.Ok)
    filtered = tmp_path / "filtered.csv"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *args, **kwargs: (str(filtered), "CSV"))
    view._export_filtered_csv()
    assert filtered.is_file()
    monkeypatch.setattr(QDesktopServices, "openUrl", lambda *args: True)
    view._open_folder()
    progress = QProgressDialog()
    view._on_export_finished(True, "ok.xlsx", progress)
    view._on_export_finished(False, "error", progress)

    output = tmp_path / "records.xlsx"
    result = []
    worker = ExportThread(csv_path, output)
    worker.finished_signal.connect(lambda ok, message: result.append((ok, message)))
    worker.run()
    assert result[-1][0] is True and output.is_file()


def test_yaml_editor_sync_diff_format_and_file_paths(qt_app, tmp_path, monkeypatch):
    from PyQt6.QtWidgets import QMessageBox

    from omnicrawler.gui.core.config_model import CrawlConfig
    from omnicrawler.gui.views.yaml_editor import YamlEditor

    config = CrawlConfig(project_name="visual_task", seed_urls=["https://example.com"], max_pages=5)
    editor = YamlEditor()
    monkeypatch.setattr(QMessageBox, "critical", lambda *args: QMessageBox.StandardButton.Ok)
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: QMessageBox.StandardButton.Ok)
    statuses = []
    editor.sync_status.connect(statuses.append)
    editor.set_config(config)
    assert editor.get_config().project_name == "visual_task"
    editor._try_sync_from_editor()
    assert "已同步" in statuses
    editor.set_yaml_text("not: [valid")
    assert editor.get_config() is None
    editor._try_sync_from_editor()
    assert any("解析错误" in status for status in statuses)
    editor.set_config(config)
    other = CrawlConfig(project_name="other", seed_urls=["https://example.com/2"], max_pages=9, concurrency=4)
    diffs = editor._compute_diffs(config, other)
    assert {item[0] for item in diffs} >= {"项目名", "种子 URL", "最大页数", "并发数"}
    editor._apply_choices({item[0]: "form" for item in diffs})
    editor._apply_choices({item[0]: "editor" for item in diffs})
    editor._format_yaml()
    path = tmp_path / "task.yaml"
    path.write_text(editor._editor.toPlainText(), encoding="utf-8")
    assert editor.load_file(path)
    assert not editor.load_file(tmp_path / "missing.yaml")
    monkeypatch.setattr(QMessageBox, "question", lambda *args, **kwargs: QMessageBox.StandardButton.Yes)
    path.write_text(path.read_text(encoding="utf-8") + "\n# changed", encoding="utf-8")
    editor._check_external_change()


def test_log_console_filters_search_redaction_trim_and_export(qt_app, tmp_path, monkeypatch):
    from PyQt6.QtGui import QTextCursor
    from PyQt6.QtWidgets import QFileDialog

    import omnicrawler.gui.widgets.log_console as module
    from omnicrawler.gui.widgets.log_console import LogConsole

    console = LogConsole()
    console.append_log("GET https://secret.example/path?token=abc selector='.private'", "info")
    console.append_log("warning message", "warn")
    console.append_log("error exception", "error")
    console._set_filter("warn")
    assert "warning" in console._editor.toPlainText()
    console._set_filter("all")
    cursor = console._editor.textCursor()
    cursor.movePosition(QTextCursor.MoveOperation.Start)
    cursor.movePosition(QTextCursor.MoveOperation.Right, QTextCursor.MoveMode.KeepAnchor, 5)
    console._editor.setTextCursor(cursor)
    console._copy_selected()
    console._search_highlight()
    redacted = console._redact_log("https://private.example/a?q=1 selector=#secret xpath=//hidden")
    assert "private.example" not in redacted and "REDACTED" in redacted
    output = tmp_path / "logs.txt"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *args, **kwargs: (str(output), "txt"))
    console.export_logs()
    assert output.is_file() and "secret.example" not in output.read_text(encoding="utf-8")
    monkeypatch.setattr(module, "MAX_BLOCKS", 2)
    monkeypatch.setattr(module, "TRIM_HEAD", 1)
    console.append_log("trim me", "info")
    console._do_trim()
    console.clear()
    assert console._all_logs == []


def test_task_history_persistence_cleanup_and_signals(qt_app, tmp_path, monkeypatch):
    from PyQt6.QtWidgets import QMessageBox

    from omnicrawler.gui.views.task_history import TaskHistory

    config = tmp_path / "task.yaml"
    config.write_text("source: {}", encoding="utf-8")
    history = TaskHistory(tmp_path)
    history.load_history()
    history.add_record("id", "project", str(config), str(tmp_path / "work"))
    history.update_record("id", "finished")
    assert history._list.count() == 1
    loaded, viewed = [], []
    history.load_config_requested.connect(loaded.append)
    history.view_results_requested.connect(viewed.append)
    history._list.setCurrentRow(0)
    history._load_selected()
    history._view_results()
    assert loaded == [str(config)] and viewed
    old = dict(history._records[0])
    old["task_id"] = "old"
    old["started_at"] = (datetime.now() - timedelta(days=90)).isoformat()
    invalid = dict(old, task_id="invalid", started_at="not-a-date")
    history._records.extend([old, invalid])
    # A3：清理前有确认门——测试中直接确认通过
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.Yes)
    history._cleanup()
    assert all(record["task_id"] != "old" for record in history._records)
    monkeypatch.setattr(QMessageBox, "information", lambda *args: QMessageBox.StandardButton.Ok)
    config.unlink()
    history._list.setCurrentRow(0)
    history._load_selected()


def test_desktop_validator_covers_schema_and_selector_errors():
    from omnicrawler.gui.core.config_model import CrawlConfig, FieldDef
    from omnicrawler.gui.core.validator import validate_full_config, validate_schema, validate_selector_format

    cases = (
        FieldDef("css_json", "$.item", "css"), FieldDef("css_xpath", "//h1", "css"),
        FieldDef("xpath_json", "$.item", "xpath"), FieldDef("xpath_css", ".title", "xpath"),
        FieldDef("json", "item.title", "jsonpath"), FieldDef("empty", "", "css"),
    )
    assert all(validate_selector_format(field) for field in cases)
    errors, warnings = validate_schema({"unknown": 1, "project": [], "source": [], "extract": []})
    assert errors and warnings
    errors, warnings = validate_schema({"project": {}, "source": {"kind": "bad", "seeds": "x"}, "extract": {"fields": {}}})
    assert errors and warnings
    config = CrawlConfig(seed_urls=["https://example.com/{{id}}"], source_kind="unsupported", fields=list(cases))
    errors, warnings = validate_full_config(config)
    assert errors and warnings


def test_api_pagination_invalid_limit_falls_back_safely():
    from omnicrawler.extraction.api_discovery import ApiEndpointProfile, _pagination_config

    profile = ApiEndpointProfile(
        "https://example.com/api", "https://example.com/api?offset=0&limit=bad", "GET", 200,
        "application/json", "items", 1,
        {"offset": {"location": "query", "sample": 0}, "limit": {"location": "query", "sample": "bad"}},
        {}, {}, .8, {},
    )
    assert _pagination_config(profile)["step"] == 1
