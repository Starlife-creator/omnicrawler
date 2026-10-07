"""Explicit geometric ordering keeps source regions; table links remain review candidates."""
from __future__ import annotations

import math
from bisect import bisect_right
from typing import Any

from .base import DocumentIR


def validate_columns(value: Any) -> list[float]:
    if not isinstance(value, list) or len(value) > 8 or any(
        type(number) not in (float, int) or not math.isfinite(number) or number <= 0 for number in value
    ) or value != sorted(set(value)):
        raise ValueError("PDF column_boundaries must be up to eight increasing positive point coordinates")
    return value


def column_blocks(page: dict[str, Any], boundaries: list[float]) -> list[tuple[str, Any, dict[str, Any]]]:
    if any(boundary >= float(page["width"]) for boundary in boundaries):
        raise ValueError("PDF column_boundaries must be inside every selected page")
    columns: list[list[dict[str, Any]]] = [[] for _ in range(len(boundaries) + 1)]
    tables = page.get("tables", [])
    for word in page.get("words", []):
        center_x = (float(word["x0"]) + float(word["x1"])) / 2
        center_y = (float(word["top"]) + float(word["bottom"])) / 2
        if any(table["bbox"][0] <= center_x <= table["bbox"][2] and
               table["bbox"][1] <= center_y <= table["bbox"][3] for table in tables):
            continue
        if any(float(word["x0"]) < boundary < float(word["x1"]) for boundary in boundaries):
            raise ValueError("PDF column_boundaries intersects a word; review page geometry")
        columns[bisect_right(boundaries, center_x)].append(word)
    blocks: list[tuple[str, Any, dict[str, Any]]] = []
    for column, words in enumerate(columns):
        lines: list[list[dict[str, Any]]] = []
        for word in sorted(words, key=lambda item: (float(item["top"]), float(item["x0"]))):
            if not lines or abs(float(lines[-1][0]["top"]) - float(word["top"])) > 3:
                lines.append([])
            lines[-1].append(word)
        for line in lines:
            line.sort(key=lambda item: float(item["x0"]))
            region = [min(float(item["x0"]) for item in line), min(float(item["top"]) for item in line),
                      max(float(item["x1"]) for item in line), max(float(item["bottom"]) for item in line)]
            blocks.append(("paragraph", " ".join(str(item["text"]) for item in line),
                           {"page": page["page_no"], "bbox": region, "column_index": column,
                            "coordinate_system": "pdf_points_top_left"}))
    for number, table in enumerate(tables, 1):
        bbox = table["bbox"]
        spanning = any(bbox[0] < boundary < bbox[2] for boundary in boundaries)
        blocks.append(("table", table["cells"], {"page": page["page_no"], "page_table": number, "bbox": bbox,
                      "column_index": bisect_right(boundaries, (bbox[0] + bbox[2]) / 2),
                      "spanning_columns": spanning, "coordinate_system": "pdf_points_top_left"}))
    blocks.sort(key=lambda block: (block[2]["column_index"], block[2]["bbox"][1], block[2]["bbox"][0]))
    paragraph = 0
    for kind, _, locator in blocks:
        if kind == "paragraph":
            paragraph += 1
            locator["page_paragraph"] = paragraph
    return blocks


def mark_table_continuations(document: DocumentIR) -> None:
    candidates: list[dict[str, Any]] = []
    for index in range(1, len(document.tables)):
        before, after = document.tables[index - 1], document.tables[index]
        old, new = document.table_locators[index - 1], document.table_locators[index]
        if not before or not after or len(before[0]) < 2 or before[0] != after[0] or new.get("page") != old.get("page", 0) + 1:
            continue
        old_box, new_box = old.get("bbox"), new.get("bbox")
        if not old_box or not new_box or abs(old_box[0] - new_box[0]) > 5 or abs(old_box[2] - new_box[2]) > 5:
            continue
        new.update(continuation_candidate_of=index - 1, continuation_verified=False)
        candidates.append({"before_table": index - 1, "after_table": index, "pages": [old["page"], new["page"]]})
    if candidates:
        document.metadata["table_continuation_candidates"] = candidates
        document.warnings.append("Cross-page table continuation candidates require review; original tables remain separate")
