"""Fixed real PDFs verify explicit columns and preserve ambiguous continuation evidence."""
import pytest
from reportlab.pdfgen import canvas
from reportlab.platypus import Table, TableStyle

from omnicrawler.document_ir import parse_document


def test_explicit_two_column_order_preserves_each_region(tmp_path):
    path = tmp_path / "columns.pdf"
    writer = canvas.Canvas(str(path), pagesize=(600, 800))
    for x, prefix in ((40, "LEFT"), (340, "RIGHT")):
        writer.drawString(x, 740, prefix + " ONE")
        writer.drawString(x, 720, prefix + " TWO")
    writer.save()
    result = parse_document(path, {"column_boundaries": [300]})
    assert result.paragraphs == ["LEFT ONE", "LEFT TWO", "RIGHT ONE", "RIGHT TWO"]
    assert [row["column_index"] for row in result.paragraph_locators] == [0, 0, 1, 1]
    assert all(row["order_source"] == "explicit_columns" and row["bbox"] for row in result.paragraph_locators)
    assert result.metadata["column_boundaries"] == [300]


def test_repeated_cross_page_table_header_is_candidate_without_merging(tmp_path):
    path = tmp_path / "continued.pdf"
    writer = canvas.Canvas(str(path), pagesize=(600, 800))
    for value in ("first", "second"):
        table = Table([["Name", "Value"], [value, "100"]], colWidths=[120, 120], rowHeights=[30, 30])
        table.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 1, "black")]))
        table.wrap(600, 800)
        table.drawOn(writer, 40, 600)
        writer.showPage()
    writer.save()
    result = parse_document(path)
    assert len(result.tables) == 2
    assert result.table_locators[1]["continuation_candidate_of"] == 0
    assert result.table_locators[1]["continuation_verified"] is False
    assert result.metadata["table_continuation_candidates"] == [{"before_table": 0, "after_table": 1, "pages": [1, 2]}]
    assert any("continuation" in warning for warning in result.warnings)


@pytest.mark.parametrize("boundaries", [[True], [float("nan")], [0], [600], [400, 300], [300, 300], "300"])
def test_invalid_explicit_columns_are_rejected(tmp_path, boundaries):
    path = tmp_path / "page.pdf"
    writer = canvas.Canvas(str(path), pagesize=(600, 800))
    writer.drawString(40, 740, "Known text")
    writer.save()
    with pytest.raises(ValueError, match="column_boundaries"):
        parse_document(path, {"column_boundaries": boundaries})


def test_column_boundary_through_text_is_not_silently_split(tmp_path):
    path = tmp_path / "overlap.pdf"
    writer = canvas.Canvas(str(path), pagesize=(600, 800))
    writer.drawString(280, 740, "OVERLAPPING")
    writer.save()
    with pytest.raises(ValueError, match="intersects a word"):
        parse_document(path, {"column_boundaries": [300]})
