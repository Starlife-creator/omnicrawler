import time
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from reportlab.pdfgen import canvas

from omnicrawler.gui.views.pdf_layout_review import PdfLayoutReviewDialog


def test_real_preview_stale_options_and_fresh_export(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    def wait(dialog):
        deadline = time.monotonic() + 30
        while dialog._worker is not None and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(.01)
        assert dialog._worker is None
    source = tmp_path / "columns.pdf"
    writer = canvas.Canvas(str(source), pagesize=(600, 800))
    for x, prefix in ((40, "LEFT"), (340, "RIGHT")):
        for index in range(5):
            writer.drawString(x, 740 - index * 20, f"{prefix} row {index}")
    writer.save()
    dialog = PdfLayoutReviewDialog(source)
    dialog.show()
    QTest.mouseClick(dialog.preview, Qt.MouseButton.LeftButton)
    wait(dialog)
    assert dialog.export.isEnabled() and not dialog.image.pixmap().isNull()
    assert dialog.text.toPlainText().index("LEFT row 4") < dialog.text.toPlainText().index("RIGHT row 0")
    dialog.automatic.setChecked(False)
    dialog.columns.setText("300")
    assert not dialog.export.isEnabled()
    dialog._load()
    wait(dialog)
    monkeypatch.setattr("omnicrawler.gui.views.pdf_layout_review.QFileDialog.getExistingDirectory", lambda *_: str(tmp_path))
    dialog._export()
    output = tmp_path / "columns-reviewed"
    assert (output / "document.md").is_file() and (output / "document.json").is_file()
    assert Path(dialog.reviewed_document().source) == source
    dialog.close()
