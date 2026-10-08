import pytest

pytest.importorskip("PySide6")

from omnicrawler.gui.views.professional_review import EvidenceView


def test_missing_optional_field_is_visible_without_fabricating_a_value():
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    record = {"record_id": "r1", "source_url": "https://example.org/", "data": {"title": "A"},
              "evidence": {"_quality": {"review_required": True, "missing_fields": ["date"], "missing_required": []}}}
    view = EvidenceView()
    try:
        view.show_record(record)
        app.processEvents()
        rows = {view._field_table.item(row, 0).text(): row for row in range(view._field_table.rowCount())}
        assert "date" in rows
        row = rows["date"]
        assert view._field_table.item(row, 1).text() == "未提取"
        assert view._field_table.item(row, 4).text() == "未知"
        assert "配置字段" in view._risk_details.text()
        assert record["data"] == {"title": "A"}
    finally:
        view.close()
        view.deleteLater()
