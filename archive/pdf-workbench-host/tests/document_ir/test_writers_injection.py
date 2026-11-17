"""B-P1-1 / B-P3-5：DocumentIR 导出公式注入中和、Markdown 转义与原子写出回归。"""

from __future__ import annotations

from pathlib import Path

import pytest

from omnicrawler.document_ir import DocumentIR
from omnicrawler.document_ir.writers import write_markdown, write_text, write_xlsx


def _malicious_ir(root: Path) -> DocumentIR:
    ir = DocumentIR(
        source=root / "doc.txt",
        kind=".records",
        title='=HYPERLINK("http://evil.example", "click")',
    )
    ir.add_paragraph("@SUM(1+1)")
    ir.add_paragraph("-2 纯数字不转义")
    ir.add_table([["+cmd", "@cmd2"], ["1", "2"]])
    return ir


def test_xlsx_formula_prefix_neutralized(tmp_path: Path) -> None:
    openpyxl = pytest.importorskip("openpyxl")
    target = tmp_path / "doc.xlsx"
    write_xlsx(_malicious_ir(tmp_path), target, {})

    wb = openpyxl.load_workbook(target, read_only=True)
    try:
        doc = wb["Document"]
        doc_values = [row[0] for row in doc.values if row and row[0] is not None]
        # 标题与段落中的公式前导字符被单引号中和；纯数字单元格不被误伤。
        assert str(doc_values[0]).startswith("'")
        assert any(str(value).startswith("'@") for value in doc_values)

        table = wb["Table 1"]
        header = next(table.iter_rows(min_row=1, max_row=1, values_only=True))
        assert str(header[0]).startswith("'+")
        assert str(header[1]).startswith("'@")
        body = next(table.iter_rows(min_row=2, max_row=2, values_only=True))
        assert body[0] == "1"
        assert body[1] == "2"
    finally:
        wb.close()

    # 原子替换：目录下无残留临时文件。
    leftovers = [p for p in tmp_path.iterdir() if p.name.startswith(".doc-")]
    assert leftovers == []


def test_markdown_title_and_paragraph_escaped(tmp_path: Path) -> None:
    ir = DocumentIR(source=tmp_path / "d.txt", kind=".txt", title="# 恶意标题")
    ir.add_paragraph("**加粗** [链接](http://evil.example) `code`")
    md = ir.to_markdown()
    assert r"\# 恶意标题" in md
    assert r"\*\*加粗\*\*" in md
    assert r"\[链接\]" in md
    assert r"\`code\`" in md
    # 原始 Markdown 结构字符不得裸露进入输出。
    assert "**加粗**" not in md
    assert "[链接]" not in md.replace(r"\[链接\]", "")


def test_text_writers_leave_no_temp_files(tmp_path: Path) -> None:
    ir = DocumentIR(source=tmp_path / "d.txt", kind=".txt", title="t")
    ir.add_paragraph("body")
    write_markdown(ir, tmp_path / "d.md", {})
    write_text(ir, tmp_path / "d.txt", {})
    assert [p for p in tmp_path.iterdir() if p.name.startswith(".")] == []
