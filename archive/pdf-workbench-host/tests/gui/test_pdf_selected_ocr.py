from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QMessageBox

from omnicrawler.gui.views.pdf_workbench import PdfWorkbenchView
from omnicrawler.gui.views.pdf_workbench_worker import _PdfPipelineWorker


def test_invalid_ocr_page_numbers_do_not_start_processing(tmp_path, monkeypatch):
    messages = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: messages.append(args))
    view = PdfWorkbenchView()
    view._state = "ready"
    view._pdf_files = [tmp_path / "scan.pdf"]
    view._ocr_pages_input.setText("0,2")
    view._execute()
    assert messages
    assert view._state == "ready" and view._worker is None
    view.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_pdf_worker_forwards_snapshot_of_page_selection(monkeypatch):
    captured = []

    def extraction(path, **kwargs):
        captured.append(kwargs)
        return {"status": {}}

    monkeypatch.setattr("omnicrawler.pdfx.service.run_extraction", extraction)
    pages = [2, 4]
    worker = _PdfPipelineWorker("fixture.yaml", ocr_pages=pages)
    pages.append(6)
    worker.run()
    assert captured[0]["ocr_pages"] == [2, 4]
    assert callable(captured[0]["should_stop"])
    assert captured[0]["should_stop"]() is False
    worker.cancel()
    assert captured[0]["should_stop"]() is True
    worker.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
