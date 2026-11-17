"""B-P2-18：convertx 表头公式注入中和（CSV + XLSX 表头与数据行同口径）。"""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from omnicrawler import convertx


def test_xlsx_header_formula_prefix_neutralized(tmp_path: Path) -> None:
    openpyxl = pytest.importorskip("openpyxl")
    source = tmp_path / "in.jsonl"
    source.write_text(json.dumps({"=evil": "v", "@evil2": "w"}), encoding="utf-8")
    target = tmp_path / "out.xlsx"
    convertx.convert(source, target)

    wb = openpyxl.load_workbook(target, read_only=True)
    try:
        header = next(wb.active.iter_rows(min_row=1, max_row=1, values_only=True))
    finally:
        wb.close()
    assert any(str(cell).startswith("'=") for cell in header)
    assert any(str(cell).startswith("'@") for cell in header)


def test_csv_header_formula_prefix_neutralized(tmp_path: Path) -> None:
    source = tmp_path / "in.jsonl"
    source.write_text(json.dumps({"=evil": "v", "-evil": "w"}), encoding="utf-8")
    target = tmp_path / "out.csv"
    convertx.convert(source, target)

    with target.open(encoding="utf-8-sig", newline="") as fh:
        header = next(csv.reader(fh))
    assert any(cell.startswith("'=") for cell in header)
    assert any(cell.startswith("'-") for cell in header)
