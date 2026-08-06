"""S3.1.17：pdf_region 页码 1 基统一。"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("fitz")

import fitz

from omnicrawl.pipeline_ops.pdf_region import extract_region, make_region_rule


def _pdf(tmp_path: Path) -> Path:
    pdf = tmp_path / "multi.pdf"
    document = fitz.open()
    for index in range(3):
        page = document.new_page()
        page.insert_text((72, 72), f"Page {index + 1} content", fontsize=14)
    document.save(pdf)
    document.close()
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
