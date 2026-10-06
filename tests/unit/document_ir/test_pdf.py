import hashlib
import json

import pytest
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from omnicrawler.document_ir import parse_document
from omnicrawler.services.archive_analysis import execute


def _pdf(path, texts):
    writer = PdfWriter()
    for text in texts:
        page = writer.add_blank_page(width=400, height=400)
        font = DictionaryObject({NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"),
                                 NameObject("/BaseFont"): NameObject("/Helvetica")})
        page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F1"): writer._add_object(font)})})
        stream = DecodedStreamObject()
        stream.set_data(f"BT /F1 12 Tf 50 300 Td ({text}) Tj ET".encode())
        page[NameObject("/Contents")] = writer._add_object(stream)
    writer.write(path)


def test_real_pdf_delivery_analysis_has_page_provenance_and_resumes(tmp_path, monkeypatch):
    path = tmp_path / "selected.pdf"
    _pdf(path, ["Observed price: 12.", "Observed price: 34."])
    document = parse_document(path)
    assert [{key: row[key] for key in ("page", "page_paragraph")} for row in document.paragraph_locators] == [{"page": 1, "page_paragraph": 1}, {"page": 2, "page_paragraph": 1}]
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"format": 1, "sources": [{"id": "pdf-one", "path": path.name,
                         "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}]}))
    output = tmp_path / "report"
    execute(manifest, output)
    report = json.loads((output / "analysis.json").read_text())
    assert [item["locator"]["page"] for item in report["evidence"]] == [1, 2]
    assert [item["quote"] for item in report["evidence"]] == ["Observed price: 12.", "Observed price: 34."]
    from omnicrawler.services import archive_analysis
    monkeypatch.setattr(archive_analysis, "parse_document", lambda *_: pytest.fail("valid stage reparsed"))
    execute(manifest, output)


def test_omitted_pages_are_explicit_and_empty_pdf_is_not_success(tmp_path):
    path = tmp_path / "mixed.pdf"
    _pdf(path, ["", "Known text."])
    parsed = parse_document(path)
    assert parsed.metadata["omitted_pages_needing_ocr"] == [1] and parsed.warnings
    assert parsed.paragraph_locators[0]["page"] == 2
    with pytest.raises(ValueError, match="page limit"):
        parse_document(path, {"max_pages": 1})
    _pdf(path, [""])
    with pytest.raises(ValueError, match="OCR"):
        parse_document(path)


def test_selected_ocr_pages_have_page_evidence_without_invented_regions(tmp_path):
    path = tmp_path / "mixed.pdf"
    _pdf(path, ["", "Native text.", ""])
    calls = []

    class LocalOCR:
        def recognize(self, png):
            assert png.startswith(b"\x89PNG")
            calls.append(png)
            return "Recovered scan.", 0.8

    parsed = parse_document(path, {"ocr_pages": [1], "ocr_backend": LocalOCR(), "ocr_dpi": 100})
    assert len(calls) == 1
    assert parsed.paragraphs == ["Recovered scan.", "Native text."]
    assert parsed.metadata["ocr_pages_recognized"] == [1]
    assert parsed.metadata["omitted_pages_needing_ocr"] == [3]
    locator = parsed.paragraph_locators[0]
    assert locator["page"] == 1 and locator["text_source"] == "ocr"
    assert locator["ocr_confidence"] == 0.8
    assert "bbox" not in locator
    assert parsed.metadata["page_geometry"]["1"]["rotation_degrees"] == 0


def test_selected_ocr_requires_backend_and_preserves_failed_omission(tmp_path):
    path = tmp_path / "mixed.pdf"
    _pdf(path, ["", "Known text."])
    with pytest.raises(ValueError, match="local ocr_backend"):
        parse_document(path, {"ocr_pages": [1]})

    class FailingOCR:
        def recognize(self, png):
            raise RuntimeError("fixture failure")

    parsed = parse_document(path, {"ocr_pages": [1], "ocr_backend": FailingOCR()})
    assert parsed.metadata["ocr_pages_recognized"] == []
    assert parsed.metadata["ocr_failures"] == [{"page": 1, "error_type": "RuntimeError"}]
    assert parsed.metadata["omitted_pages_needing_ocr"] == [1]
