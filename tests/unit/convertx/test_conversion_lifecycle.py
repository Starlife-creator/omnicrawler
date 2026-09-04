"""User-visible loss reporting and cancellation must preserve committed output."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from threading import Event

import pytest

from omnicrawler import convertx


def source_file(root: Path) -> Path:
    source = root / "input.jsonl"
    source.write_text('\n'.join(json.dumps({"id": n}) for n in range(700)), encoding="utf-8")
    return source


def test_skipped_records_have_bounded_non_sensitive_diagnostics(tmp_path):
    source = tmp_path / "input.jsonl"
    source.write_text('{"id":1}\n\n' + '{"secret":invalid\n' * 20 + '[]\n{"id":2}\n', encoding="utf-8")
    result = convertx.convert(source, tmp_path / "out.csv")
    assert result.rows == 2
    assert result.extra["accepted_records"] == result.extra["written_records"] == 2
    assert result.extra["rejected_records"] == 21
    assert len(result.extra["rejection_samples"]) == 10
    assert result.extra["rejection_samples"][0]["line"] == 3
    assert "21" in result.warnings[0]
    assert "secret" not in json.dumps(result.extra) + str(result.warnings)


@pytest.mark.parametrize("bad_line", ['{"bad":', '[]', 'null'])
def test_abort_preserves_existing_output(tmp_path, bad_line):
    source = tmp_path / "input.jsonl"
    source.write_text('{"id":1}\n' + bad_line, encoding="utf-8")
    target = tmp_path / "out.csv"
    target.write_bytes(b"previous output")
    with pytest.raises(ValueError, match="行 2"):
        convertx.convert(source, target, on_error="abort")
    assert target.read_bytes() == b"previous output"
    assert len(list(tmp_path.iterdir())) == 2


def test_options_can_be_reused_without_leaking_hooks_or_statistics(tmp_path):
    source = source_file(tmp_path)
    options = {"reader_jsonl": {"flat": True}, "writer_csv": {"encoding": "utf-8"}}
    expected = json.loads(json.dumps(options))
    first = convertx.convert(source, tmp_path / "first.csv", options=options, should_stop=lambda: False)
    second = convertx.convert(source, tmp_path / "second.csv", options=options)
    assert options == expected
    assert first.extra == second.extra


def test_custom_registry_keeps_unknown_counts_and_writer_metadata(tmp_path, monkeypatch):
    source = source_file(tmp_path)
    metadata = {"warnings": ["custom warning"]}
    monkeypatch.setitem(convertx.READERS, ".jsonl", lambda path, options: [{"custom": True}])

    def writer(rows, path, options):
        path.write_text(json.dumps(rows), encoding="utf-8")
        return metadata

    monkeypatch.setitem(convertx.WRITERS, ".csv", writer)
    result = convertx.convert(source, tmp_path / "custom.csv")
    assert result.rows == 1
    assert result.extra["rejected_records"] is None
    assert result.extra["written_records"] is None
    assert metadata == {"warnings": ["custom warning"]}


def test_multiline_csv_counts_logical_records(tmp_path):
    source = tmp_path / "input.csv"
    source.write_text('id,text\n1,"first\nsecond"\n2,third\n', encoding="utf-8", newline="")
    result = convertx.convert(source, tmp_path / "out.jsonl")
    assert result.extra["written_records"] == 2
    assert result.extra["rejected_records"] == 0
    assert json.loads((tmp_path / "out.jsonl").read_text(encoding="utf-8").splitlines()[0])["text"] == "first\nsecond"


def test_xlsx_reports_actual_rows_and_cell_truncation(tmp_path, monkeypatch):
    openpyxl = pytest.importorskip("openpyxl")
    monkeypatch.setattr(convertx, "XLSX_ROW_LIMIT", 2)
    source = tmp_path / "input.jsonl"
    source.write_text('\n'.join(json.dumps({"id": i, "text": "x" * 33000 if i == 0 else "ok"}) for i in range(3)), encoding="utf-8")
    target = tmp_path / "out.xlsx"
    result = convertx.convert(source, target)
    assert result.rows == 3  # Existing API retains the accepted-record count.
    assert result.extra["written_records"] == 2
    assert result.extra["omitted_records"] == 1
    assert result.extra["truncated_cells"] == 1
    assert len(result.warnings) == 2
    wb = openpyxl.load_workbook(target, read_only=True)
    try:
        rows = list(wb.active.values)
        assert len(rows) == 3  # header + two actual records
        assert len(rows[1][rows[0].index("text")]) == 32700
    finally:
        wb.close()


def test_csv_reports_existing_cell_truncation_without_changing_formula_protection(tmp_path):
    source = tmp_path / "input.jsonl"
    source.write_text(json.dumps({"long": "x" * 33000, "formula": "=SUM(A1)"}), encoding="utf-8")
    target = tmp_path / "out.csv"
    result = convertx.convert(source, target)
    assert result.extra["truncated_cells"] == 1
    assert "截断" in result.warnings[0]
    with target.open(encoding="utf-8-sig", newline="") as fh:
        row = next(csv.DictReader(fh))
    assert len(row["long"]) == 32700
    assert row["formula"] == "'=SUM(A1)"


def test_both_cli_entry_points_report_actual_written_rows(tmp_path, monkeypatch, capsys):
    import argparse

    from omnicrawler.cli._handlers import _run_convert
    from omnicrawler.convertx.__main__ import main

    pytest.importorskip("openpyxl")
    monkeypatch.setattr(convertx, "XLSX_ROW_LIMIT", 2)
    source = source_file(tmp_path)
    target = tmp_path / "out.xlsx"
    assert main([str(source), str(target)]) == 0
    lines = capsys.readouterr().out.splitlines()
    payload = json.loads(next(line.split("  ", 1)[1] for line in lines if line.startswith("[cx] WARN")))
    assert payload["rows"] == 700
    assert payload["written_records"] == 2
    _run_convert(argparse.Namespace(src=str(source), dst=str(target), quiet=False))
    output = capsys.readouterr().out
    assert "写入 2 行" in output
    assert "有异常" in output


@pytest.mark.parametrize("stage", ["before", "read", "write"])
def test_cancellation_has_one_terminal_event_and_preserves_output(tmp_path, stage):
    source = source_file(tmp_path)
    target = tmp_path / "out.csv"
    target.write_bytes(b"old")
    stopped = Event()
    events = []
    if stage == "before":
        stopped.set()
    with pytest.raises(convertx.ConversionCancelledError):
        convertx.convert(
            source, target, should_stop=stopped.is_set, on_progress=events.append,
            on_read_progress=(lambda payload: stopped.set()) if stage == "read" else None,
            on_write_progress=(lambda payload: stopped.set()) if stage == "write" else None,
        )
    assert [ev.state for ev in events if ev.state in {"finished", "cancelled", "failed"}] == ["cancelled"]
    assert target.read_bytes() == b"old"
    assert len(list(tmp_path.iterdir())) == 2


@pytest.mark.parametrize("extension,dependency", [(".csv", None), (".jsonl", None), (".xlsx", "openpyxl"), (".parquet", "pyarrow")])
def test_file_writer_cancel_before_commit_preserves_old_file(tmp_path, extension, dependency):
    if dependency:
        pytest.importorskip(dependency)
    source = source_file(tmp_path)
    target = tmp_path / ("out" + extension)
    target.write_bytes(b"old content")
    stop = Event()
    with pytest.raises(convertx.ConversionCancelledError):
        convertx.convert(source, target, should_stop=stop.is_set, on_write_progress=lambda payload: stop.set())
    assert target.read_bytes() == b"old content"
    assert len(list(tmp_path.iterdir())) == 2


@pytest.mark.parametrize("error", [PermissionError, KeyboardInterrupt])
def test_commit_failure_cleans_temporary_file(tmp_path, monkeypatch, error):
    source = source_file(tmp_path)
    target = tmp_path / "out.csv"
    target.write_bytes(b"old")
    events = []

    def fail_replace(self, other):
        raise error("blocked")

    monkeypatch.setattr(Path, "replace", fail_replace)
    with pytest.raises(error):
        convertx.convert(source, target, on_progress=events.append)
    assert target.read_bytes() == b"old"
    assert len(list(tmp_path.iterdir())) == 2
    expected = "cancelled" if error is KeyboardInterrupt else "failed"
    assert [ev.state for ev in events if ev.state in {"finished", "cancelled", "failed"}] == [expected]


def test_cancellation_after_commit_does_not_report_false_cancel(tmp_path, monkeypatch):
    source = source_file(tmp_path)
    target = tmp_path / "out.csv"
    stop = Event()
    events = []
    replace = Path.replace

    def replace_then_cancel(self, other):
        result = replace(self, other)
        stop.set()
        return result

    monkeypatch.setattr(Path, "replace", replace_then_cancel)
    result = convertx.convert(source, target, should_stop=stop.is_set, on_progress=events.append)
    assert result.extra["written_records"] == 700
    assert [ev.state for ev in events if ev.state in {"finished", "cancelled", "failed"}] == ["finished"]
    with target.open(encoding="utf-8-sig", newline="") as fh:
        assert len(list(csv.DictReader(fh))) == 700


def test_xlsx_partial_save_does_not_replace_previous_workbook(tmp_path, monkeypatch):
    openpyxl = pytest.importorskip("openpyxl")
    source = source_file(tmp_path)
    target = tmp_path / "out.xlsx"
    target.write_bytes(b"original")

    def broken_save(self, filename):
        Path(filename).write_bytes(b"partial zip")
        raise OSError("disk full")

    monkeypatch.setattr(openpyxl.Workbook, "save", broken_save)
    with pytest.raises(RuntimeError, match="无法写入 Excel"):
        convertx.convert(source, target)
    assert target.read_bytes() == b"original"
    assert len(list(tmp_path.iterdir())) == 2


def test_duckdb_cancel_rolls_back_target_table_and_preserves_other_tables(tmp_path):
    duckdb = pytest.importorskip("duckdb")
    source = source_file(tmp_path)
    target = tmp_path / "out.duckdb"
    with duckdb.connect(str(target)) as con:
        con.execute("CREATE TABLE records AS SELECT 99 AS old_id")
        con.execute("CREATE TABLE unrelated AS SELECT 'keep' AS value")
    stop = Event()
    with pytest.raises(convertx.ConversionCancelledError):
        convertx.convert(source, target, should_stop=stop.is_set, on_write_progress=lambda payload: stop.set())
    with duckdb.connect(str(target)) as con:
        assert con.execute("SELECT * FROM records").fetchall() == [(99,)]
        assert con.execute("SELECT * FROM unrelated").fetchall() == [("keep",)]
    result = convertx.convert(source, target)
    assert result.extra["written_records"] == 700
    with duckdb.connect(str(target)) as con:
        assert con.execute("SELECT count(*) FROM records").fetchone() == (700,)
        assert con.execute("SELECT * FROM unrelated").fetchall() == [("keep",)]


def test_cancel_new_duckdb_leaves_no_empty_database(tmp_path):
    pytest.importorskip("duckdb")
    source = source_file(tmp_path)
    target = tmp_path / "new.duckdb"
    stop = Event()
    with pytest.raises(convertx.ConversionCancelledError):
        convertx.convert(source, target, should_stop=stop.is_set, on_write_progress=lambda payload: stop.set())
    assert list(tmp_path.iterdir()) == [source]


@pytest.mark.parametrize("extension", [".txt", ".md"])
def test_document_write_cancellation_keeps_original(tmp_path, monkeypatch, extension):
    source = tmp_path / "source.txt"
    source.write_text("A document", encoding="utf-8")
    target = tmp_path / ("out" + extension)
    target.write_text("old", encoding="utf-8")
    write_text = Path.write_text
    stop = Event()

    def write_then_cancel(self, *args, **kwargs):
        result = write_text(self, *args, **kwargs)
        stop.set()
        return result

    monkeypatch.setattr(Path, "write_text", write_then_cancel)
    with pytest.raises(convertx.ConversionCancelledError):
        convertx.convert(source, target, should_stop=stop.is_set)
    assert target.read_text(encoding="utf-8") == "old"
    assert len(list(tmp_path.iterdir())) == 2
