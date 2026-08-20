"""S2.5.18：offline_demo 生成合法 PDF（可被打开解析，OCR 路径可走通）。

Phase 0：验证读取由 fitz 换为 pdfplumber。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from omnicrawler.services.offline_demo import create_demo_workspace

pytest.importorskip("pdfplumber")


def test_demo_pdfs_are_valid_and_parseable(tmp_path: Path) -> None:
    import pdfplumber

    demo = create_demo_workspace(tmp_path / "demo")
    assert demo.index.is_file() and demo.config.is_file()

    with pdfplumber.open(str(demo.root / "report.pdf")) as report:
        assert len(report.pages) >= 1
        text = report.pages[0].extract_text(layout=False) or ""
        assert "Revenue" in text and "1,200,000" in text

    with pdfplumber.open(str(demo.root / "scan.pdf")) as scan:
        assert len(scan.pages) >= 1
        # 纯位图页无文字层——OCR 演示路径（tesseract）真实可走通
        assert (scan.pages[0].extract_text(layout=False) or "").strip() == ""


def test_demo_pdfs_survive_pdfx_parse_pipeline(tmp_path: Path) -> None:
    from omnicrawler.pdfx.parser import parse_document

    demo = create_demo_workspace(tmp_path / "demo2")
    parsed = parse_document(str(demo.root / "report.pdf"), min_chars=10, max_garbled_ratio=0.4)
    assert parsed["page_count"] >= 1
    assert "Revenue" in parsed["pages"][0]["final_text"]
