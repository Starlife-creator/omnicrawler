"""Exercise actual conversion workers with deterministic cancellation timing."""

from __future__ import annotations

from threading import Event
from types import SimpleNamespace

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication

from omnicrawler import convertx
from omnicrawler.gui.views.convert_tool import ConvertView, _ConvertWorker, _DocWorker


@pytest.fixture(scope="module")
def app():
    from omnicrawler.gui import i18n

    i18n.set_language("zh_CN")
    return QApplication.instance() or QApplication([])


def make_worker(kind, source, target):
    if kind == "document":
        return _DocWorker(src_path=source, dst_path=target)
    return _ConvertWorker(
        src_path=source, dst_path=target, src_format=None, dst_format=".csv",
        flat=True, nested=False, table="records", compression="zstd",
    )


@pytest.mark.parametrize("kind", ["table", "document"])
@pytest.mark.parametrize("action", ["cancel", "requestInterruption"])
def test_worker_cancel_or_window_shutdown_emits_only_cancelled(app, tmp_path, monkeypatch, kind, action):
    ext = ".txt" if kind == "document" else ".jsonl"
    source = tmp_path / ("input" + ext)
    source.write_text("Document" if kind == "document" else '{"id":1}\n', encoding="utf-8")
    target = tmp_path / ("out.txt" if kind == "document" else "out.csv")
    target.write_bytes(b"old")
    entered, release = Event(), Event()
    original = convertx.READERS[ext]

    def blocked_reader(path, options):
        entered.set()
        if not release.wait(5):
            raise RuntimeError("test reader release timed out")
        return original(path, options)

    monkeypatch.setitem(convertx.READERS, ext, blocked_reader)
    worker = make_worker(kind, source, target)
    outcomes = []
    worker.succeeded.connect(lambda result: outcomes.append("succeeded"))
    worker.failed.connect(lambda error: outcomes.append(error))
    worker.cancelled.connect(lambda: outcomes.append("cancelled"))
    worker.start()
    try:
        assert entered.wait(5)
        getattr(worker, action)()
    finally:
        release.set()
        assert worker.wait(5000)
        app.processEvents()
    assert outcomes == ["cancelled"]
    assert target.read_bytes() == b"old"
    assert len(list(tmp_path.iterdir())) == 2

    # A new task after cancellation can complete and commit normally.
    outcomes.clear()
    again = make_worker(kind, source, target)
    again.succeeded.connect(lambda result: outcomes.append("succeeded"))
    again.cancelled.connect(lambda: outcomes.append("cancelled"))
    again.failed.connect(lambda error: outcomes.append(error))
    again.start()
    assert again.wait(5000)
    app.processEvents()
    assert outcomes == ["succeeded"]
    assert target.read_bytes() != b"old"


def test_cancel_button_cleans_up_worker_and_allows_retry(app, tmp_path, monkeypatch):
    entered, release = Event(), Event()
    source = tmp_path / "input.jsonl"
    source.write_text('{"id":1}\n', encoding="utf-8")
    target = tmp_path / "out.csv"
    target.write_bytes(b"old")
    original = convertx.READERS[".jsonl"]

    def reader(path, options):
        entered.set()
        if not release.wait(5):
            raise RuntimeError("test reader release timed out")
        return original(path, options)

    monkeypatch.setitem(convertx.READERS, ".jsonl", reader)
    view = ConvertView()
    notices = []
    toast = SimpleNamespace(info=notices.append, warning=notices.append, error=notices.append, success=notices.append)
    monkeypatch.setattr(view, "_toast", lambda: toast)
    view._src_path = source
    view._dst_path_edit.setText(str(target))
    view._fmt_combo.setCurrentIndex(view._fmt_combo.findData(".csv"))
    view._start_conversion()
    worker = view._worker
    try:
        assert worker is not None
        assert entered.wait(5)
        view._btn_cancel.click()
        assert not view._btn_cancel.isEnabled()
        assert "停止" in view._stage_label.text()
    finally:
        release.set()
        assert worker is not None and worker.wait(5000)
        app.processEvents()
    assert view._worker is None
    assert view._btn_run.isEnabled()
    assert "已取消" in view._stage_label.text()
    assert target.read_bytes() == b"old"
    view.close()


def test_success_summary_uses_written_count_and_warning_severity(app, monkeypatch):
    view = ConvertView()
    notices = []
    toast = SimpleNamespace(
        warning=lambda text: notices.append(("warning", text)),
        success=lambda text: notices.append(("success", text)),
    )
    monkeypatch.setattr(view, "_toast", lambda: toast)
    result = convertx.ConvertResult(
        source_format=".jsonl", target_format=".xlsx", rows=100,
        warnings=["省略 98 条"], extra={"written_records": 2},
    )
    view._on_succeeded(result)
    assert "写入 2 行" in view._stage_label.text()
    assert "有异常" in view._stage_label.text()
    assert notices[0][0] == "warning"
    assert "省略 98 条" in view._log.toPlainText()
    view.close()
