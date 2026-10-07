"""Native PDF text adapter reusing pdfx, with explicit page provenance."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

from .base import DocumentIR
from .parsers import register_document_parser
from .pdf_layout import column_blocks, infer_columns, mark_table_continuations, validate_columns


@register_document_parser(".pdf")
def parse_pdf(path: Path, options: dict[str, Any]) -> DocumentIR:
    # OCR is opt-in with an explicitly supplied local backend; no backend/model
    # discovery or downloads are triggered by this adapter.
    from ..pdfx.parser import _iter_parsed_pages

    limit = options.get("max_pages", 200)
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 2000:
        raise ValueError("PDF max_pages must be an integer between 1 and 2000")
    boundaries = validate_columns(options.get("column_boundaries", []))
    automatic = options.get("auto_columns", False)
    if type(automatic) is not bool or (automatic and boundaries):
        raise ValueError("PDF auto_columns must be boolean and cannot accompany column_boundaries")
    selected = options.get("ocr_pages", [])
    if not isinstance(selected, list) or len(selected) > 2000 or any(type(page) is not int or not 1 <= page <= limit for page in selected):
        raise ValueError("PDF ocr_pages must be a bounded list of positive page numbers")
    selected_pages = set(selected)
    backend = options.get("ocr_backend")
    if selected_pages and not callable(getattr(backend, "recognize", None)):
        raise ValueError("Selected PDF OCR pages require an explicit local ocr_backend")
    dpi = options.get("ocr_dpi", 220)
    if type(dpi) is not int or not 72 <= dpi <= 600:
        raise ValueError("PDF ocr_dpi must be between 72 and 600")
    document = DocumentIR(source=path, kind=".pdf", title=path.stem)
    if boundaries:
        document.metadata["column_boundaries"] = boundaries
    omitted: list[int] = []
    recognized: list[int] = []
    failures: list[dict[str, Any]] = []
    iterator = _iter_parsed_pages(str(path), min_chars=1, max_garbled_ratio=0.1, include_structure=True)
    try:
        for index, page in enumerate(iterator, 1):
            if index > limit:
                raise ValueError("PDF exceeds the selected page limit")
            document.metadata["page_count"] = index
            document.metadata.setdefault("page_geometry", {})[str(page["page_no"])] = page.get("page_geometry", {})
            source_method = "native"
            if page["needs_ocr"]:
                if page["page_no"] in selected_pages:
                    try:
                        from ..pdfx.parser import render_page, text_quality
                        assert backend is not None
                        rendered = render_page(str(path), page["page_no"], dpi=dpi)
                        rich = getattr(backend, "recognize_rich", None)
                        if callable(rich):
                            rich_result = rich(rendered)
                            text, confidence = rich_result.text, rich_result.confidence
                            rich_words = list(rich_result.words)
                            rich_tables = list(rich_result.tables)
                        else:
                            text, confidence = backend.recognize(rendered)
                            rich_words, rich_tables = [], []
                        if not isinstance(text, str) or text_quality(text)[0] < 1 or text_quality(text)[1] > 0.1:
                            raise ValueError("OCR returned no usable text")
                        if confidence is not None and (type(confidence) not in (int, float) or not math.isfinite(confidence)
                                                       or not 0 <= confidence <= 1):
                            raise ValueError("OCR confidence must be between 0 and 1")
                        page = {**page, "final_text": text, "words": rich_words, "tables": rich_tables}
                        if rich_tables:
                            document.metadata.setdefault("ocr_tables", {})[str(page["page_no"])] = rich_tables
                        recognized.append(page["page_no"])
                        source_method = "ocr"
                        document.warnings.append(f"Page {page['page_no']}: OCR text; paragraph regions and reading order are not verified")
                    except Exception as exc:
                        failures.append({"page": page["page_no"], "error_type": type(exc).__name__})
                        omitted.append(page["page_no"])
                        continue
                else:
                    omitted.append(page["page_no"])
                    continue
            page_blocks: list[tuple[str, Any, dict[str, Any]]] = []
            for table_no, table in enumerate(page.get("tables", []), 1):
                table_locator = {"page": page["page_no"], "page_table": table_no,
                                 "coordinate_system": "image_pixels_top_left" if source_method == "ocr" else "pdf_points_top_left"}
                if "bbox" in table:
                    table_locator["bbox"] = table["bbox"]
                if source_method == "ocr":
                    table_locator["ocr_structure"] = "富结果"
                cells = table.get("cells", table)
                if cells and isinstance(cells, list) and isinstance(cells[0], dict):
                    max_row = max(int(cell.get("row", 0)) + int(cell.get("row_span", 1)) - 1 for cell in cells)
                    max_col = max(int(cell.get("column", 0)) + int(cell.get("column_span", 1)) - 1 for cell in cells)
                    rows = [["" for _ in range(max_col + 1)] for _ in range(max_row + 1)]
                    for cell in cells:
                        rows[int(cell.get("row", 0))][int(cell.get("column", 0))] = str(cell.get("text", ""))
                    cells = rows
                page_blocks.append(("table", cells, table_locator))
            for paragraph_no, paragraph in enumerate(str(page["final_text"]).split("\n\n"), 1):
                if paragraph.strip():
                    locator: dict[str, Any] = {"page": page["page_no"], "page_paragraph": paragraph_no}
                    region = _paragraph_region(paragraph, page.get("words", []))
                    if region is not None:
                        locator.update(bbox=region, coordinate_system="image_pixels_top_left" if source_method == "ocr" else "pdf_points_top_left")
                    page_blocks.append(("paragraph", paragraph.strip(), locator))
            page_boundaries = infer_columns(page) if automatic and source_method == "native" else boundaries
            if page_boundaries and source_method == "native":
                page_blocks = column_blocks(page, page_boundaries)
                ordering = "inferred_columns_unverified" if automatic else "explicit_columns"
                if automatic:
                    document.metadata.setdefault("inferred_columns", {})[str(page["page_no"])] = page_boundaries
                    document.warnings.append(f"Page {page['page_no']}: inferred column order requires review")
                if any(block[2].get("spanning_columns") for block in page_blocks):
                    ordering = "explicit_columns_spanning_region_unverified"
                    document.warnings.append(f"Page {page['page_no']}: spanning table order requires review")
            elif page_blocks and all("bbox" in block[2] for block in page_blocks):
                page_blocks.sort(key=lambda block: (block[2]["bbox"][1], block[2]["bbox"][0]))
                ordering = "geometric_top_left_not_verified"
                document.warnings.append(f"Page {page['page_no']}: geometric order is a heuristic; multi-column reading order is not verified")
            else:
                ordering = "native_groups_unknown_reading_order"
                if page.get("tables"):
                    document.warnings.append(f"Page {page['page_no']}: paragraph/table reading order could not be established")
                # Text-only native order is useful; tables have no proven insertion point.
                page_blocks.sort(key=lambda block: block[0] == "table")
            for kind, value, locator in page_blocks:
                locator["text_source"] = source_method
                if source_method == "ocr":
                    locator.update(ocr_backend=type(backend).__name__, ocr_dpi=dpi,
                                   ocr_confidence=confidence)
                locator["order_source"] = ordering
                if kind == "table":
                    document.add_table(value, locator=locator)
                else:
                    document.add_paragraph(value, locator=locator)
    finally:
        iterator.close()
    mark_table_continuations(document)
    document.metadata["omitted_pages_needing_ocr"] = omitted
    if selected_pages:
        if selected_pages - set(range(1, document.metadata.get("page_count", 0) + 1)):
            raise ValueError("Selected PDF OCR page does not exist")
        document.metadata.update(ocr_pages_requested=sorted(selected_pages), ocr_pages_recognized=recognized,
                                 ocr_failures=failures)
    if omitted:
        document.metadata["repair_action"] = {"service": "processors.pdf", "requires_ocr": True, "pages": omitted}
        document.warnings.append(f"Native text only; pages needing OCR were omitted: {omitted}")
    if not document.paragraphs and not document.tables:
        raise ValueError("PDF has no usable native text; OCR is required")
    return document


def _paragraph_region(text: str, words: list[dict[str, Any]]) -> list[float] | None:
    wanted = text.split()
    token_words = [(token, word) for word in words for token in str(word.get("text", "")).split()]
    tokens = [token for token, _word in token_words]
    for index in range(max(0, len(tokens) - len(wanted) + 1)):
        if wanted and tokens[index:index + len(wanted)] == wanted:
            matched = [word for _token, word in token_words[index:index + len(wanted)]]
            boxes: list[list[Any]] = []
            for word in matched:
                value = word.get("bbox")
                if isinstance(value, list) and len(value) == 4:
                    boxes.append(value)
            if len(boxes) == len(matched):
                return [min(float(box[0]) for box in boxes), min(float(box[1]) for box in boxes),
                        max(float(box[2]) for box in boxes), max(float(box[3]) for box in boxes)]
            if all(all(key in word for key in ("x0", "top", "x1", "bottom")) for word in matched):
                return [min(float(word["x0"]) for word in matched), min(float(word["top"]) for word in matched),
                        max(float(word["x1"]) for word in matched), max(float(word["bottom"]) for word in matched)]
            return None
    return None
