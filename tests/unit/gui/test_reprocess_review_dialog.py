import json

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from omnicrawler.core.models import CrawlRequest, ExtractedRecord, FetchResult
from omnicrawler.gui.views.professional_review import EvidenceView
from omnicrawler.gui.views.reprocess_review import ReprocessReviewDialog
from omnicrawler.state import StateStore


def test_user_selects_reordered_candidate_and_confirms_difference(tmp_path):
    app = QApplication.instance() or QApplication([])
    database = tmp_path / "state.sqlite3"
    with StateStore(database) as state:
        run = state.start_run("review", "config.yaml")
        request = CrawlRequest("https://example.org/")
        state.save_records(run, request, [ExtractedRecord(request.url, "item", {"name": "old", "removed": 0})])
        record_id = state.rows("SELECT record_id FROM records")[0]["record_id"]
        state.edit_record(record_id, "name", "manual")
        state.preserve_reprocess_candidate(run, FetchResult(request, request.url, 200, {}, b"html", 0), [
            ExtractedRecord(request.url, "item", {"name": "wrong"}),
            ExtractedRecord(request.url, "item", {"name": "correct", "amount": None}, {"name": {"source": "quote"}}),
        ])
    dialog = ReprocessReviewDialog(database, record_id)
    dialog.show()
    app.processEvents()
    assert not dialog.accept_candidate.isEnabled()
    dialog.candidates.setCurrentIndex(2)
    dialog.reason.setText("Confirmed corresponding source")
    assert dialog.accept_candidate.isEnabled()
    assert "quote" in dialog.evidence.toPlainText()
    names = [dialog.differences.item(row, 0).text() for row in range(dialog.differences.rowCount())]
    assert dialog.differences.item(names.index("amount"), 2).text() == "null"
    assert dialog.differences.item(names.index("removed"), 2).text() != "null"
    QTest.mouseClick(dialog.accept_candidate, Qt.MouseButton.LeftButton)
    assert dialog.result_record["data"] == {"name": "correct", "amount": None}
    view = EvidenceView(workspace=tmp_path)
    view.show_record(dialog.result_record)
    assert view._field_table.rowCount() == 2
    assert all(view._field_table.item(row, 4).text() != "100%" for row in range(2))
    assert view._risk_badge.text() != "0"
    assert "复核" in view._risk_details.text()
    view.show_record({"record_id": record_id, "data": {"name": "not assessed"}, "evidence": {}})
    assert view._risk_badge.text() != "0" and "评估" in view._risk_details.text()
    view.clear()
    assert not view._reprocess_btn.isEnabled()
    view.close()
    view.deleteLater()
    with StateStore(database) as state:
        assert json.loads(state.rows("SELECT data_json FROM records")[0]["data_json"]) == dialog.result_record["data"]
    dialog.close()
    dialog.deleteLater()
    app.processEvents()
