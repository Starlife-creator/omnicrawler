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
    """提取指定页（1 基）矩形区域文本（S3.1.17：页码统一 1 基，边界转换）。

    Phase 0（M0a）：fitz Rect/clip → pdfplumber crop。坐标系同为 PDF 点、
    左上原点；裁剪区域与页面边界取交集（对应 fitz.Rect & page.rect）。
    """
    try:
        import pdfplumber
    except ImportError as exc:
        raise RuntimeError("PDF region selection requires pdfplumber") from exc
    with pdfplumber.open(str(pdf)) as document:
        if not 1 <= page_number <= len(document.pages):
            raise IndexError("PDF page number is outside the document")
        page = document.pages[page_number - 1]
        x0, y0, x1, y1 = rect
        # 归一化坐标 + 裁剪到页面边界（clip.is_empty 等价判断）
        left = max(0.0, min(x0, x1))
        top = max(0.0, min(y0, y1))
        right = min(float(page.width), max(x0, x1))
        bottom = min(float(page.height), max(y0, y1))
        if left >= right or top >= bottom:
            return ""
        cropped = page.crop((left, top, right, bottom))
        return (cropped.extract_text(layout=False) or "").strip()


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
