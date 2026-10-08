"""Reviewed cross-page rows must keep their own original page, region and source node."""
import copy
import hashlib
import json
from pathlib import Path

import pytest
from reportlab.pdfgen import canvas
from reportlab.platypus import Table, TableStyle

from omnicrawler.document_ir import parse_document
from omnicrawler.document_ir.pdf_layout import confirm_table_continuations
from omnicrawler.services.archive_analysis import execute


def make_pdf(path):
    writer = canvas.Canvas(str(path), pagesize=(600, 800))
    for value in ("FIRST", "SECOND", "THIRD"):
        table = Table([["Item", "FY2025 USD"], [value, "100"]], colWidths=[120, 120], rowHeights=[30, 30])
        table.setStyle(TableStyle([("GRID", (0,0), (-1,-1), 1, "black")]))
        table.wrap(600,800)
        table.drawOn(writer,40,600)
        writer.showPage()
    writer.save()


def test_reviewed_chain_maps_each_row_to_its_original_page_and_node(tmp_path):
    path = tmp_path / "tables.pdf"
    make_pdf(path)
    original = parse_document(path)
    before = copy.deepcopy(original)
    merged = confirm_table_continuations(original, [(0,1), (1,2)])
    assert original == before
    origins = [merged.table_row_locator(0, row) for row in range(4)]
    assert [row["page"] for row in origins] == [1,1,2,3]
    assert [row["source_table"] for row in origins] == [1,1,2,3]
    assert [row["source_row"] for row in origins] == [1,2,2,2]
    assert origins[2]["bbox"] == original.table_locators[1]["bbox"]
    assert origins[2]["node_id"] == next(b.node_id for b in original.blocks if b.kind == "table" and b.index == 1)
    assert len({row["node_id"] for row in origins[1:]}) == 3
    assert [row["page"] for row in json.loads(json.dumps(merged.table_locators))[0]["row_locators"]] == [1,1,2,3]


def test_manifest_confirmed_tables_reach_evidence_and_invalidate_old_view_cache(tmp_path):
    path = tmp_path / "tables.pdf"
    make_pdf(path)
    source = {"id":"tables","path":path.name,"sha256":hashlib.sha256(path.read_bytes()).hexdigest()}
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"format":1,"sources":[source]}))
    first = execute(manifest,tmp_path / "report")
    assert json.loads(Path(first["report"]).read_text(encoding="utf-8"))["documents"][0]["table_count"] == 3
    source["parse_options"] = {"confirmed_table_continuations":[[0,1],[1,2]]}
    manifest.write_text(json.dumps({"format":1,"sources":[source]}))
    result = execute(manifest,tmp_path / "report")
    report = json.loads(Path(result["report"]).read_text(encoding="utf-8"))
    assert report["documents"][0]["table_count"] == 1
    rows = [row for row in report["evidence"] if row["quote"].startswith(("FIRST", "SECOND", "THIRD"))]
    assert [row["locator"]["page"] for row in rows] == [1,2,3]
    assert [row["locator"]["table"] for row in rows] == [1,1,1]
    assert [row["locator"]["row"] for row in rows] == [2,3,4]
    assert [row["locator"]["source_row"] for row in rows] == [2,2,2]
    assert all(row["locator"]["bbox"] for row in rows)


@pytest.mark.parametrize("pairs", [[(0,9)],[(True,1)],[(1,0)],[(0,1),(0,1)]])
def test_invalid_confirmation_does_not_mutate_source(tmp_path,pairs):
    path = tmp_path / "tables.pdf"
    make_pdf(path)
    original = parse_document(path)
    before = copy.deepcopy(original)
    with pytest.raises(ValueError,match="candidates"):
        confirm_table_continuations(original,pairs)
    assert original == before
