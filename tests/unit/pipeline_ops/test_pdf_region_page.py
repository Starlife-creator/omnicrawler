"""S3.1.17：pdf_region 页码 1 基统一。"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("pdfplumber")

from omnicrawler.pipeline_ops.pdf_region import extract_region, make_region_rule


def _pdf(tmp_path: Path) -> Path:
    # Phase 0：fitz → reportlab（fixture；文本置于距顶部 72pt，与 fitz 插入位置等价）
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    pdf = tmp_path / "multi.pdf"
    _, page_height = A4
    c = canvas.Canvas(str(pdf), pagesize=A4)
    for index in range(3):
        c.setFont("Helvetica", 14)
        c.drawString(72, page_height - 72, f"Page {index + 1} content")
        c.showPage()
    c.save()
    return pdf


def test_extract_region_uses_one_based_page(tmp_path: Path) -> None:
    pdf = _pdf(tmp_path)
    assert "Page 3" in extract_region(pdf, 3, (0, 0, 400, 200))
    assert "Page 1" in extract_region(pdf, 1, (0, 0, 400, 200))


def test_extract_region_out_of_range(tmp_path: Path) -> None:
    pdf = _pdf(tmp_path)
    with pytest.raises(IndexError):
        extract_region(pdf, 0, (0, 0, 400, 200))
    with pytest.raises(IndexError):
        extract_region(pdf, 4, (0, 0, 400, 200))


def test_make_region_rule_stores_one_based_page(tmp_path: Path) -> None:
    pdf = _pdf(tmp_path)
    rule = make_region_rule(pdf, "title", 3, (0, 0, 400, 200))
    assert rule.page == 3
    assert "Page 3" in rule.sample_text
