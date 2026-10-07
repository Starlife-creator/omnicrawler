"""Actual PDFs verify inferred reading order and explicit, source-preserving merge."""
import pytest
from reportlab.pdfgen import canvas
from reportlab.platypus import Table, TableStyle

from omnicrawler.document_ir import parse_document
from omnicrawler.document_ir.pdf_layout import confirm_table_continuations


def test_auto_columns_and_manual_override(tmp_path):
    path = tmp_path / "columns.pdf"
    writer = canvas.Canvas(str(path), pagesize=(600, 800))
    for x, prefix in ((40, "LEFT"), (340, "RIGHT")):
        for index in range(5):
            writer.drawString(x, 740 - index * 20, f"{prefix} row {index}")
    writer.save()
    result = parse_document(path, {"auto_columns": True})
    assert result.paragraphs == [f"{side} row {index}" for side in ("LEFT", "RIGHT") for index in range(5)]
    assert result.metadata["inferred_columns"]["1"]
    assert all(row["order_source"] == "inferred_columns_unverified" for row in result.paragraph_locators)
    assert any("requires review" in warning for warning in result.warnings)
    manual = parse_document(path, {"column_boundaries": [300]})
    assert manual.paragraphs == result.paragraphs
    assert "inferred_columns" not in manual.metadata
    with pytest.raises(ValueError, match="auto_columns"):
        parse_document(path, {"auto_columns": True, "column_boundaries": [300]})


def test_single_column_is_not_invented(tmp_path):
    path = tmp_path / "one.pdf"
    writer = canvas.Canvas(str(path))
    for index in range(8):
        writer.drawString(40, 740 - index * 20, f"One normal line with ordinary word spacing {index}")
    writer.save()
    assert "inferred_columns" not in parse_document(path, {"auto_columns": True}).metadata


def test_reviewed_continuation_chain_keeps_original_regions(tmp_path):
    path = tmp_path / "tables.pdf"
    writer = canvas.Canvas(str(path), pagesize=(600, 800))
    for value in ("first", "second", "third"):
        table = Table([["Name", "Value"], [value, "100"]], colWidths=[120, 120], rowHeights=[30, 30])
        table.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 1, "black")]))
        table.wrap(600, 800)
        table.drawOn(writer, 40, 600)
        writer.showPage()
    writer.save()
    original = parse_document(path)
    reviewed = confirm_table_continuations(original, [(0, 1), (1, 2)])
    assert len(original.tables) == 3 and len(reviewed.tables) == 1
    assert reviewed.tables[0] == [["Name", "Value"], ["first", "100"], ["second", "100"], ["third", "100"]]
    assert [item["page"] for item in reviewed.table_locators[0]["source_tables"]] == [1, 2, 3]
    assert reviewed.table_locators[0]["verification_source"] == "user_confirmation"
    assert len([block for block in reviewed.ordered_blocks() if block.kind == "table"]) == 1
    with pytest.raises(ValueError, match="candidates"):
        confirm_table_continuations(original, [(0, 2)])


def test_review_does_not_materialize_or_mutate_legacy_source_blocks(tmp_path):
    import copy

    from omnicrawler.document_ir.base import DocumentIR
    original = DocumentIR(kind="pdf", source=tmp_path / "legacy.pdf", tables=[[["A", "B"], ["1", "2"]], [["A", "B"], ["3", "4"]]],
                          table_locators=[{"page": 1}, {"page": 2}],
                          metadata={"table_continuation_candidates": [{"before_table": 0, "after_table": 1}]})
    before = copy.deepcopy(original)
    reviewed = confirm_table_continuations(original, [(0, 1)])
    assert original == before and original.blocks == []
    assert len(reviewed.ordered_blocks()) == 1

