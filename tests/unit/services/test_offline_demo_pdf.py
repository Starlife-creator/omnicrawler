"""S2.5.18：offline_demo 生成合法 PDF（可被 PyMuPDF 打开，OCR 路径可走通）。"""

from __future__ import annotations

from pathlib import Path

import pytest

from omnicrawl.services.offline_demo import create_demo_workspace

pytest.importorskip("fitz")


def test_demo_pdfs_are_valid_and_parseable(tmp_path: Path) -> None:
    import fitz

    demo = create_demo_workspace(tmp_path / "demo")
    assert demo.index.is_file() and demo.config.is_file()

    report = fitz.open(str(demo.root / "report.pdf"))
    try:
        assert report.page_count >= 1
        text = report[0].get_text()
        assert "Revenue" in text and "1,200,000" in text
    finally:
        report.close()

    scan = fitz.open(str(demo.root / "scan.pdf"))
    try:
        assert scan.page_count >= 1
        # 纯位图页无文字层——OCR 演示路径（tesseract）真实可走通
        assert scan[0].get_text().strip() == ""
    finally:
        scan.close()


def test_demo_pdfs_survive_pdfx_parse_pipeline(tmp_path: Path) -> None:
    from omnicrawl.pdfx.parser import parse_document

    demo = create_demo_workspace(tmp_path / "demo2")
    parsed = parse_document(str(demo.root / "report.pdf"), min_chars=10, max_garbled_ratio=0.4)
    assert parsed["page_count"] >= 1
    assert "Revenue" in parsed["pages"][0]["final_text"]
