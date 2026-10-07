import concurrent.futures
import json
from types import SimpleNamespace

import pytest

from omnicrawler.pdfx import ocr
from omnicrawler.pdfx.database import Database
from omnicrawler.pdfx.text_export import export_text_stage


class StructuredBackend:
    def recognize(self, png):
        pytest.fail("structured OCR was flattened before persistence")

    def recognize_rich(self, png):
        return ocr.OCRRichResult(
            "Observed revenue", 0.8,
            words=[{"text": "Observed revenue", "bbox": [10, 20, 120, 40], "confidence": 0.8}],
            blocks=[{"text": "Observed revenue", "bbox": [10, 20, 120, 40]}],
            tables=[{"html": "<table><tr><th colspan=2>Revenue</th></tr></table>",
                     "cells": [{"row": 0, "column": 0, "row_span": 1, "column_span": 2,
                                "header": True, "text": "Revenue"}]}],
            metadata={"coordinate_system": "image_pixels_top_left"},
        )


def seed(db):
    db.execute("INSERT INTO documents(doc_id,sha256,primary_path,filename,size_bytes,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
               ("doc", "digest", "fixture.pdf", "fixture.pdf", 1, "now", "now"))
    db.execute("INSERT INTO pages(doc_id,page_no,needs_ocr,ocr_status,updated_at) VALUES(?,?,?,?,?)",
               ("doc", 1, 1, "pending", "now"))


@pytest.mark.parametrize("workers", [1, 2])
def test_structure_survives_worker_database_reopen_export_and_reset(tmp_path, monkeypatch, workers):
    backend = StructuredBackend()
    monkeypatch.setattr(ocr, "create_backend", lambda config: backend)
    monkeypatch.setattr(ocr, "adaptive_ocr_workers", lambda count: count)
    monkeypatch.setattr(ocr, "render_page", lambda *args, **kwargs: b"PNG")
    monkeypatch.setattr(ocr, "_worker_backend", backend)
    monkeypatch.setattr(ocr, "_worker_document", object())
    monkeypatch.setattr(ocr, "_worker_document_path", "fixture.pdf")

    class InlinePool:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def submit(self, function, args):
            future = concurrent.futures.Future()
            future.set_result(function(args))
            return future

    monkeypatch.setattr(concurrent.futures, "ProcessPoolExecutor", InlinePool)
    config = SimpleNamespace(ocr={"dpi": 144}, output_dir=tmp_path / "output")
    path = tmp_path / "state.db"
    with Database(path) as db:
        seed(db)
        summary = ocr.ocr_stage(config, db, ocr_workers=workers)
        assert summary["recognized"] == 1 and summary["failed"] == 0
    with Database(path) as db:
        saved = json.loads(db.fetchone("SELECT ocr_structure_json FROM pages")[0])
        assert saved["metadata"]["dpi"] == 144
        assert saved["metadata"]["coordinate_system"] == "image_pixels_top_left"
        assert saved["words"][0]["confidence"] == 0.8
        assert saved["tables"][0]["cells"][0]["column_span"] == 2
        assert saved["blocks"][0]["bbox"] == [10, 20, 120, 40]
        export_text_stage(config, db)
        row = json.loads((config.output_dir / "pages.jsonl").read_text(encoding="utf-8"))
        assert row["text"] == "Observed revenue" and row["ocr_structure"] == saved
        db.reset_stage("ocr")
        assert db.fetchone("SELECT ocr_structure_json FROM pages")[0] is None
        export_text_stage(config, db)
        assert json.loads((config.output_dir / "pages.jsonl").read_text(encoding="utf-8"))["ocr_structure"] is None


@pytest.mark.parametrize("text, confidence", [("", 0.9), ("Recovered", float("nan")), ("Recovered", 1.1), ("Recovered", True)])
def test_unusable_ocr_cannot_be_saved_as_success(tmp_path, monkeypatch, text, confidence):
    backend = SimpleNamespace(recognize=lambda png: (text, confidence))
    monkeypatch.setattr(ocr, "create_backend", lambda config: backend)
    monkeypatch.setattr(ocr, "render_page", lambda *args, **kwargs: b"PNG")
    with Database(tmp_path / "state.db") as db:
        seed(db)
        summary = ocr.ocr_stage(SimpleNamespace(ocr={"dpi": 144}), db)
        assert summary["recognized"] == 0 and summary["failed"] == 1
        row = db.fetchone("SELECT ocr_status,ocr_structure_json FROM pages")
        assert row["ocr_status"] == "failed" and row["ocr_structure_json"] is None


def test_spans_reserve_grid_positions_and_bad_scores_keep_tables():
    html = '<table><tr><th rowspan=2>A</th><th colspan="2">B</th><th>C</th></tr><tr><td>D</td><td>E</td></tr></table>'
    result = ocr._paddle_page_result(
        {"overall_ocr_res": {"rec_texts": ["Title"], "rec_scores": ["bad"]},
         "table_res_list": [{"pred_html": html}]}, {"markdown_texts": "Title"})
    assert result.confidence is None
    cells = result.tables[0]["cells"]
    assert [(cell["row"], cell["column"]) for cell in cells] == [(0, 0), (0, 1), (0, 3), (1, 1), (1, 2)]
    assert cells[0]["row_span"] == 2 and cells[1]["column_span"] == 2


def test_tesseract_word_and_page_confidence_use_same_scale():
    backend = ocr.TesseractBackend.__new__(ocr.TesseractBackend)
    backend.Image = SimpleNamespace(open=lambda stream: SimpleNamespace(convert=lambda mode: object()))
    backend.lang = "eng"
    backend.pytesseract = SimpleNamespace(
        Output=SimpleNamespace(DICT="dict"),
        image_to_data=lambda *args, **kwargs: {
            "text": ["Revenue"], "conf": ["80"], "block_num": [1], "par_num": [1], "line_num": [1],
            "left": [10], "top": [20], "width": [100], "height": [30],
        },
    )
    result = backend.recognize_rich(b"PNG")
    assert result.confidence == result.words[0]["confidence"] == 0.8
    assert result.words[0]["bbox"] == [10, 20, 110, 50]
