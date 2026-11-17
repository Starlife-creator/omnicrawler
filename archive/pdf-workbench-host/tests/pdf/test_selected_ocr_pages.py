from types import SimpleNamespace

import pytest

from omnicrawler.pdfx import ocr
from omnicrawler.pdfx.database import Database


def test_selected_pages_leave_other_pending_pages_untouched(tmp_path, monkeypatch):
    rendered = []
    monkeypatch.setattr(ocr, "create_backend", lambda config: SimpleNamespace(recognize=lambda png: ("Selected text", 0.9)))
    monkeypatch.setattr(ocr, "render_page", lambda path, page, **kwargs: rendered.append(page) or b"PNG")
    with Database(tmp_path / "state.db") as db:
        db.execute("INSERT INTO documents(doc_id,sha256,primary_path,filename,size_bytes,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
                   ("doc", "digest", "fixture.pdf", "fixture.pdf", 1, "now", "now"))
        for page in range(1, 5):
            db.execute("INSERT INTO pages(doc_id,page_no,needs_ocr,ocr_status,updated_at) VALUES(?,?,?,?,?)",
                       ("doc", page, 1, "pending", "now"))
        result = ocr.ocr_stage(SimpleNamespace(ocr={"dpi": 100}), db, page_numbers=[3, 3])
        assert result["selected"] == result["recognized"] == 1
        assert rendered == [3]
        rows = db.fetchall("SELECT page_no,ocr_status FROM pages ORDER BY page_no")
        assert [row["ocr_status"] for row in rows] == ["pending", "pending", "done", "pending"]
        assert db.fetchone("SELECT status FROM documents")["status"] == "parsed_partial"
        result = ocr.ocr_stage(SimpleNamespace(ocr={"dpi": 100}), db, page_numbers=[2, 4], limit_pages=1)
        assert result["selected"] == result["recognized"] == 1
        assert rendered == [3, 2]


@pytest.mark.parametrize("pages", [[True], [0], [-1], ["1"], "1"])
def test_invalid_page_selection_rejected_before_backend(pages, monkeypatch):
    monkeypatch.setattr(ocr, "create_backend", lambda config: pytest.fail("invalid selection started OCR"))
    with pytest.raises(ValueError, match="页码"):
        ocr.ocr_stage(SimpleNamespace(ocr={}), object(), page_numbers=pages)
