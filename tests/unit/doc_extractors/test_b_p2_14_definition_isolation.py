"""B-P2-14d/e：扫描件 needs_ocr 标记；JSON 坏载荷与 HTML 坏选择器按定义隔离，
失败槽位记录错误类型 + 消息，不中断其余定义提取。
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from omnicrawler.doc_extractors import extract_slots
from omnicrawler.doc_extractors.html import HTMLDocExtractor
from omnicrawler.state.scene_store import SlotDefinition


def _slot(key: str, extractor: str, pattern: str) -> SlotDefinition:
    return SlotDefinition(scene="t", slot_key=key, extractor_type=extractor, pattern=pattern)


# ── B-P2-14e：JSON 坏载荷整体降级为错误槽位 ─────────────────────────────

def test_bad_json_payload_produces_error_slots_without_raising() -> None:
    definitions = [
        _slot("company", "jsonpath", "$.company"),
        _slot("title", "jsonpath", "$.title"),
    ]
    hits = extract_slots("{not valid json", definitions, document_type="json")
    assert len(hits) == 2
    for hit in hits:
        assert hit.value is None
        assert hit.confidence == 0.0
        assert hit.evidence["error_type"]
        assert hit.evidence["error"]
        assert hit.evidence["matches"] == 0


# ── B-P2-14e：HTML 坏 CSS 定义隔离，好正则定义照常命中 ────────────────────

def test_bad_css_definition_isolated_regex_definition_still_hits() -> None:
    definitions = [
        _slot("revenue", "regex", r"营业收入\s*([\d,]+)"),
        _slot("broken", "css", "p:::"),
    ]
    html = "<html><body><p>营业收入 1,234 万元</p></body></html>"
    hits = HTMLDocExtractor().extract(html, definitions)

    by_key = {hit.slot_key: hit for hit in hits}
    assert by_key["revenue"].value == "1,234"
    broken = by_key["broken"]
    assert broken.value is None
    assert broken.confidence == 0.0
    assert broken.evidence["error_type"]
    assert broken.evidence["error"]


# ── B-P2-14d：扫描件（无文本层）标记 needs_ocr 并告警 ─────────────────────

def test_scanned_pdf_flags_needs_ocr_and_warns(
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from omnicrawler.doc_extractors.pdf import PDFDocExtractor

    pdf = tmp_path / "scanned.pdf"
    pdf.write_bytes(b"%PDF-1.4 scanned placeholder")
    extractor = object.__new__(PDFDocExtractor)
    extractor.needs_ocr = False
    monkeypatch.setattr(PDFDocExtractor, "_extract_text", lambda self, path: "")

    with caplog.at_level(logging.WARNING, logger="omnicrawler.doc_extractors.pdf"):
        hits = extractor.extract(pdf, [])

    assert hits == []
    assert extractor.needs_ocr is True
    assert any("OCR" in record.message for record in caplog.records)
