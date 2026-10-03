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
    iterator = _iter_parsed_pages(str(path), min_chars=1, max_garbled_ratio=0.1)
    try:
        for index, page in enumerate(iterator, 1):
            if index > limit:
                raise ValueError("PDF exceeds the selected page limit")
            document.metadata["page_count"] = index
            if page["needs_ocr"]:
                omitted.append(page["page_no"])
                continue
            for paragraph_no, paragraph in enumerate(str(page["final_text"]).split("\n\n"), 1):
                if paragraph.strip():
                    document.paragraphs.append(paragraph.strip())
                    document.paragraph_locators.append({"page": page["page_no"], "page_paragraph": paragraph_no})
    finally:
        iterator.close()
    document.metadata["omitted_pages_needing_ocr"] = omitted
    if omitted:
        document.warnings.append(f"Native text only; pages needing OCR were omitted: {omitted}")
    if not document.paragraphs:
        raise ValueError("PDF has no usable native text; OCR is required")
    return document
