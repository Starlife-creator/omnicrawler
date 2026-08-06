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
    """提取指定页（1 基）矩形区域文本（S3.1.17：页码统一 1 基，边界转换）。"""
    try:
        import fitz
    except ImportError as exc:
        raise RuntimeError("PDF region selection requires PyMuPDF") from exc
    with fitz.open(pdf) as document:
        if not 1 <= page_number <= document.page_count:
            raise IndexError("PDF page number is outside the document")
        page = document.load_page(page_number - 1)
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
    # S3.1.17：rule.page 与 extract_region 统一 1 基（原 0 基入参 +1 存储不一致）
    return PdfRegionRule(name.strip() or "field", page_number, rect, text, confidence)
