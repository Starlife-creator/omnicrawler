"""Parser/OCR identities refresh unreviewed results without erasing human decisions."""
from __future__ import annotations

import csv

import pytest
import yaml

from omnicrawler.pdfx.config import load_config
from omnicrawler.pdfx.database import Database
from omnicrawler.pdfx.ocr_result import OCRRichResult
from omnicrawler.pdfx.review import apply_review
from omnicrawler.pdfx.service import run_extraction

canvas = pytest.importorskip("reportlab.pdfgen.canvas")


def project(tmp_path, *, native=True):
    source = tmp_path / "in"
    source.mkdir()
    pdf = canvas.Canvas(str(source / "sample.pdf"))
    if native:
        pdf.drawString(72, 760, "Invoice: A0")
    pdf.showPage()
    pdf.save()
    data = {
        "project_name": "processing-dependency", "input_dir": str(source),
        "work_dir": str(tmp_path / "work"), "output_dir": str(tmp_path / "out"),
        "database": str(tmp_path / "work" / "state.sqlite3"),
        "parser": {"workers": 1, "min_native_chars": 5 if native else 5000},
        "ocr": {"backend": "none" if native else "tesseract", "workers": 1},
        "retrieval": {"top_pages": 1, "fallback_pages": [1]},
        "llm": {"provider": "disabled"}, "extraction": {"workers": 1},
        "fields": [{"name": "invoice", "label": "Invoice", "type": "code",
                    "source": "content", "patterns": [r"Invoice: (?P<value>[A-Z0-9]+)"]}],
    }
    path = tmp_path / "project.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return path, data


def save(path, data):
    path.write_text(yaml.safe_dump(data), encoding="utf-8")


def value(config):
    with Database(config.database) as db:
        row = db.fetchone("SELECT normalized_value FROM field_values WHERE field_name='invoice'")
        return None if row is None else row[0]


class FakeOCR:
    def __init__(self, text="Invoice: OCR1", version="v1", fail=False):
        self.text, self.version, self.fail = text, version, fail

    def recognize_rich(self, png):
        if self.fail:
            raise RuntimeError("fixed OCR failure")
        return OCRRichResult(self.text, 0.99)


def install_ocr(monkeypatch, backend):
    from omnicrawler.pdfx import ocr
    monkeypatch.setattr(ocr, "create_backend", lambda config: backend)
    monkeypatch.setattr(ocr, "render_page", lambda *args, **kwargs: b"fixed-png")


def test_parser_change_reparses_and_reextracts(tmp_path):
    path, data = project(tmp_path)
    assert run_extraction(path)["extract"]["records"] == 1
    data["parser"]["min_native_chars"] = 5000
    save(path, data)
    result = run_extraction(path)
    assert result["processing"]["parse"]["parsed"] == 1
    # The existing native layer is still eligible for rule extraction; the old
    # record is replaced rather than counted again in this run's summary.
    assert result["extract"]["records"] == 1
    assert value(load_config(path)) == "A0"
    with Database(load_config(path).database) as db:
        assert db.fetchone("SELECT COUNT(*) FROM processing_dependencies WHERE stage='parse'")[0] == 1
        actions = [row[0] for row in db.fetchall(
            "SELECT action FROM dependency_events WHERE stage='parse' ORDER BY id"
        )]
        assert "invalidated" in actions
        assert actions[-1] == "applied"
    assert run_extraction(path)["processing"]["parse"]["parsed"] == 0


def test_tesseract_traineddata_change_refreshes_ocr(tmp_path, monkeypatch):
    path, data = project(tmp_path, native=False)
    data["ocr"]["lang"] = "eng"
    save(path, data)
    tessdata = tmp_path / "tessdata"
    tessdata.mkdir()
    model = tessdata / "eng.traineddata"
    model.write_bytes(b"signed-model-v1")
    monkeypatch.setenv("TESSDATA_PREFIX", str(tessdata))
    install_ocr(monkeypatch, FakeOCR())
    assert run_extraction(path)["extract"]["records"] == 1
    assert run_extraction(path)["processing"]["ocr"]["selected"] == 0
    model.write_bytes(b"signed-model-v2")
    install_ocr(monkeypatch, FakeOCR("Invoice: OCR2"))
    result = run_extraction(path)
    assert result["processing"]["ocr"]["recognized"] == 1
    assert value(load_config(path)) == "OCR2"


def test_ocr_version_change_recognizes_and_reextracts(tmp_path, monkeypatch):
    path, data = project(tmp_path, native=False)
    install_ocr(monkeypatch, FakeOCR())
    assert run_extraction(path)["extract"]["records"] == 1
    assert value(load_config(path)) == "OCR1"
    install_ocr(monkeypatch, FakeOCR("Invoice: OCR2", "v2"))
    result = run_extraction(path)
    assert result["processing"]["ocr"]["recognized"] == 1
    assert value(load_config(path)) == "OCR2"
    assert run_extraction(path)["processing"]["ocr"]["selected"] == 0


def test_ocr_failure_does_not_advance_dependency(tmp_path, monkeypatch):
    path, data = project(tmp_path, native=False)
    install_ocr(monkeypatch, FakeOCR())
    run_extraction(path)
    config = load_config(path)
    with Database(config.database) as db:
        old = db.fetchone("SELECT digest FROM processing_dependencies WHERE stage='ocr'")[0]
    install_ocr(monkeypatch, FakeOCR(version="v2", fail=True))
    run_extraction(path)
    with Database(config.database) as db:
        assert db.fetchone("SELECT digest FROM processing_dependencies WHERE stage='ocr'")[0] == old
        assert db.fetchone("SELECT ocr_status FROM pages")[0] == "failed"


def test_processing_change_preserves_human_decision_and_deduplicates_warning(tmp_path, monkeypatch):
    path, data = project(tmp_path, native=False)
    install_ocr(monkeypatch, FakeOCR())
    run_extraction(path)
    config = load_config(path)
    review = tmp_path / "review.csv"
    with Database(config.database) as db:
        record_id = db.fetchone("SELECT record_id FROM records")[0]
        with review.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=["记录ID", "复核决定", "Invoice"])
            writer.writeheader()
            writer.writerow({"记录ID": record_id, "复核决定": "accept", "Invoice": "MANUAL"})
        apply_review(config, db, review)
    install_ocr(monkeypatch, FakeOCR("Invoice: OCR2", "v2"))
    events = []
    for _ in range(2):
        result = run_extraction(path, callback=lambda stage, payload: events.append((stage, payload)))
        assert result["processing"]["ocr"]["selected"] == 0
    assert value(config) == "MANUAL"
    with Database(config.database) as db:
        assert db.fetchone("SELECT COUNT(*) FROM dependency_events WHERE action='protected_review'")[0] == 1
    assert any(stage == "warnings" for stage, _ in events)
