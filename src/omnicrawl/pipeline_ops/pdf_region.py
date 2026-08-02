from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class PdfRegionRule:
    name: str
    page: int
    rect: tuple[float, float, float, float]
    sample_text: str
    confidence: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def extract_region(pdf: Path, page_number: int, rect: tuple[float, float, float, float]) -> str:
    try:
        import fitz
    except ImportError as exc:
        raise RuntimeError("PDF region selection requires PyMuPDF") from exc
    with fitz.open(pdf) as document:
        if not 0 <= page_number < document.page_count:
            raise IndexError("PDF page number is outside the document")
        page = document.load_page(page_number)
        clip = fitz.Rect(*rect) & page.rect
        if clip.is_empty:
            return ""
        return page.get_text("text", clip=clip).strip()


def make_region_rule(
    pdf: Path,
    name: str,
    page_number: int,
    rect: tuple[float, float, float, float],
) -> PdfRegionRule:
    text = extract_region(pdf, page_number, rect)
    confidence = 0.95 if text else 0.2
    return PdfRegionRule(name.strip() or "field", page_number + 1, rect, text, confidence)
