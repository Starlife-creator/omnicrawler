"""DocumentIR 出口与图片路由测试。

覆盖：docx→md/html/pdf/xlsx 的表格保留、CSV→文档、PDF→Markdown、
以及图片族互转、透明通道降级、非图片目标拒绝。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from omnicrawler.convertx import READERS, WRITERS, convert, sniff_format
from omnicrawler.document_ir.writers import ir_from_records


def _make_docx(path: Path) -> Path:
    docx = pytest.importorskip("docx")
    document = docx.Document()
    document.add_heading("经营分析", level=1)
    document.add_paragraph("本季度收入增长。")
    table = document.add_table(rows=2, cols=2)
    table.cell(0, 0).text = "指标"
    table.cell(0, 1).text = "数值"
    table.cell(1, 0).text = "收入"
    table.cell(1, 1).text = "42"
    document.save(str(path))
    return path


def test_document_output_writers_registered() -> None:
    assert ".pdf" in READERS
    for extension in (".html", ".docx", ".pdf", ".txt", ".md"):
        assert extension in WRITERS


def test_docx_to_markdown_preserves_table(tmp_path: Path) -> None:
    src = _make_docx(tmp_path / "report.docx")
    dst = tmp_path / "report.md"
    result = convert(src, dst)
    assert result.extra["tables"] == 1
    md = dst.read_text(encoding="utf-8")
    assert "| 指标 | 数值 |" in md
    assert "| --- | --- |\n| 收入 | 42 |" in md


def test_docx_to_html_preserves_table(tmp_path: Path) -> None:
    src = _make_docx(tmp_path / "report.docx")
    dst = tmp_path / "report.html"
    convert(src, dst)
    page = dst.read_text(encoding="utf-8")
    assert page.startswith("<!doctype html>")
    assert "<table>" in page
    assert "<td>收入</td>" in page
    assert 'charset="utf-8"' in page


def test_docx_to_xlsx_preserves_tables(tmp_path: Path) -> None:
    openpyxl = pytest.importorskip("openpyxl")
    src = _make_docx(tmp_path / "report.docx")
    dst = tmp_path / "report.xlsx"
    result = convert(src, dst)
    assert result.extra["tables"] == 1
    workbook = openpyxl.load_workbook(dst)
    assert "Table 1" in workbook.sheetnames
    sheet = workbook["Table 1"]
    assert sheet["A1"].value == "指标"
    assert sheet["B2"].value == "42"


def test_docx_to_pdf(tmp_path: Path) -> None:
    pytest.importorskip("reportlab")
    pypdf = pytest.importorskip("pypdf")
    src = _make_docx(tmp_path / "report.docx")
    dst = tmp_path / "report.pdf"
    result = convert(src, dst)
    assert result.extra["tables"] == 1
    assert dst.read_bytes().startswith(b"%PDF")
    text = pypdf.PdfReader(str(dst)).pages[0].extract_text()
    assert "经营分析" in text


def test_pdf_to_markdown(tmp_path: Path) -> None:
    pytest.importorskip("reportlab")
    pytest.importorskip("pypdfium2")
    src = _make_docx(tmp_path / "report.docx")
    pdf = tmp_path / "report.pdf"
    convert(src, pdf)
    dst = tmp_path / "roundtrip.md"
    result = convert(pdf, dst)
    assert result.source_format == ".pdf"
    md = dst.read_text(encoding="utf-8")
    assert "经营分析" in md


def test_csv_to_docx_builds_table(tmp_path: Path) -> None:
    docx = pytest.importorskip("docx")
    src = tmp_path / "records.csv"
    src.write_text("名称,数量\n苹果,3\n梨,5\n", encoding="utf-8-sig")
    dst = tmp_path / "records.docx"
    result = convert(src, dst)
    assert result.target_format == ".docx"
    document = docx.Document(str(dst))
    assert document.tables
    table = document.tables[0]
    assert table.cell(0, 0).text == "名称"
    assert table.cell(2, 1).text == "5"


def test_ir_from_records_single_document_record() -> None:
    ir = ir_from_records(
        [{"record_type": "document", "title": "标题", "text": "第一段\n\n第二段"}],
        source=Path("x.jsonl"),
    )
    assert ir.title == "标题"
    assert ir.paragraphs == ["第一段", "第二段"]


def test_ir_from_records_table() -> None:
    ir = ir_from_records([{"a": 1, "b": 2}, {"a": 3, "b": 4}], source=Path("x.csv"))
    assert ir.tables == [[["a", "b"], ["1", "2"], ["3", "4"]]]


def test_image_png_to_jpg_and_alpha_warning(tmp_path: Path) -> None:
    image_module = pytest.importorskip("PIL.Image")
    src = tmp_path / "a.png"
    image_module.new("RGBA", (8, 6), (255, 0, 0, 128)).save(src)
    assert sniff_format(src) == ".png"
    dst = tmp_path / "b.jpg"
    result = convert(src, dst)
    assert result.status == "completed_with_warnings"
    assert result.output_path == dst
    assert any("透明" in warning for warning in result.warnings)
    with image_module.open(dst) as opened:
        assert opened.format == "JPEG"
        assert opened.size == (8, 6)


def test_image_quality_option(tmp_path: Path) -> None:
    image_module = pytest.importorskip("PIL.Image")
    src = tmp_path / "a.png"
    image_module.new("RGB", (64, 64), (10, 20, 30)).save(src)
    low = tmp_path / "low.jpg"
    high = tmp_path / "high.jpg"
    convert(src, low, options={"quality": 20})
    convert(src, high, options={"quality": 95})
    assert low.stat().st_size < high.stat().st_size


def test_image_to_table_is_rejected(tmp_path: Path) -> None:
    image_module = pytest.importorskip("PIL.Image")
    src = tmp_path / "a.png"
    image_module.new("RGB", (4, 4), (0, 0, 0)).save(src)
    with pytest.raises(ValueError, match="图片族只支持图片格式互转"):
        convert(src, tmp_path / "out.csv")


def test_image_rename_policy(tmp_path: Path) -> None:
    image_module = pytest.importorskip("PIL.Image")
    from omnicrawler.convertx import OutputRule

    src = tmp_path / "a.png"
    image_module.new("RGB", (4, 4), (0, 0, 0)).save(src)
    dst = tmp_path / "b.png"
    image_module.new("RGB", (4, 4), (255, 255, 255)).save(dst)

    result = convert(src, dst, output_rule=OutputRule(on_conflict="rename"))

    assert result.output_path == tmp_path / "b (2).png"
    assert dst.stat().st_size > 0
    assert result.output_path is not None and result.output_path.exists()
