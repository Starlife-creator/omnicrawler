"""Native PDF text adapter reusing pdfx, with explicit page provenance."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .base import DocumentIR
from .parsers import register_document_parser


@register_document_parser(".pdf")
def parse_pdf(path: Path, options: dict[str, Any]) -> DocumentIR:
    # Optional native dependencies remain lazy; this adapter never starts OCR.
    from ..pdfx.parser import _iter_parsed_pages

    limit = options.get("max_pages", 200)
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 2000:
        raise ValueError("PDF max_pages must be an integer between 1 and 2000")
    document = DocumentIR(source=path, kind=".pdf", title=path.stem)
    omitted: list[int] = []
    iterator = _iter_parsed_pages(str(path), min_chars=1, max_garbled_ratio=0.1, include_structure=True)
    try:
        for index, page in enumerate(iterator, 1):
            if index > limit:
                raise ValueError("PDF exceeds the selected page limit")
            document.metadata["page_count"] = index
            if page["needs_ocr"]:
                omitted.append(page["page_no"])
                continue
            page_blocks: list[tuple[str, Any, dict[str, Any]]] = []
            for table_no, table in enumerate(page.get("tables", []), 1):
                page_blocks.append(("table", table["cells"], {"page": page["page_no"], "page_table": table_no,
                                    "bbox": table["bbox"], "coordinate_system": "pdf_points_top_left"}))
            for paragraph_no, paragraph in enumerate(str(page["final_text"]).split("\n\n"), 1):
                if paragraph.strip():
                    locator: dict[str, Any] = {"page": page["page_no"], "page_paragraph": paragraph_no}
                    region = _paragraph_region(paragraph, page.get("words", []))
                    if region is not None:
                        locator.update(bbox=region, coordinate_system="pdf_points_top_left")
                    page_blocks.append(("paragraph", paragraph.strip(), locator))
            if page_blocks and all("bbox" in block[2] for block in page_blocks):
                page_blocks.sort(key=lambda block: (block[2]["bbox"][1], block[2]["bbox"][0]))
                ordering = "geometric_top_left"
            else:
                ordering = "native_groups_unknown_reading_order"
                if page.get("tables"):
                    document.warnings.append(f"Page {page['page_no']}: paragraph/table reading order could not be established")
                # Text-only native order is useful; tables have no proven insertion point.
                page_blocks.sort(key=lambda block: block[0] == "table")
            for kind, value, locator in page_blocks:
                locator["order_source"] = ordering
                if kind == "table":
                    document.add_table(value, locator=locator)
                else:
                    document.add_paragraph(value, locator=locator)
    finally:
        iterator.close()
    document.metadata["omitted_pages_needing_ocr"] = omitted
    if omitted:
        document.metadata["repair_action"] = {"service": "processors.pdf", "requires_ocr": True, "pages": omitted}
        document.warnings.append(f"Native text only; pages needing OCR were omitted: {omitted}")
    if not document.paragraphs and not document.tables:
        raise ValueError("PDF has no usable native text; OCR is required")
    return document


def _paragraph_region(text: str, words: list[dict[str, Any]]) -> list[float] | None:
    wanted = text.split()
    tokens = [str(word["text"]) for word in words]
    for index in range(max(0, len(tokens) - len(wanted) + 1)):
        if wanted and tokens[index:index + len(wanted)] == wanted:
            matched = words[index:index + len(wanted)]
            return [min(float(word["x0"]) for word in matched), min(float(word["top"]) for word in matched),
                    max(float(word["x1"]) for word in matched), max(float(word["bottom"]) for word in matched)]
    return None
