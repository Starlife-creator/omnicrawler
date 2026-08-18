"""PDF 文档槽位抽取器（批 C-1）。

依赖可选 PyMuPDF（fitz）：缺依赖时构造/抽取抛出带安装提示的 RuntimeError。
抽取流程：fitz 提取全文文本 → 复用 TextDocExtractor 的 regex/text 槽位。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .base import SlotHit, TextDocExtractor


class PDFDocExtractor:
    """PDF 文档抽取器：提取文本后按 regex/text 槽位匹配。"""

    def __init__(self) -> None:
        try:
            import fitz  # noqa: F401 —— 可选依赖探测

            self._fitz = fitz
        except ImportError as exc:
            raise RuntimeError(
                "PDF 槽位抽取需要 PyMuPDF；请安装 omnicrawler[pdf]（pip install PyMuPDF）"
            ) from exc

    def extract(self, pdf_path: str | Path, definitions: list[Any]) -> list[SlotHit]:
        path = Path(pdf_path)
        if not path.is_file():
            raise FileNotFoundError(f"PDF 文档不存在: {path}")
        text = self._extract_text(path)
        return TextDocExtractor().extract(text, definitions)

    def _extract_text(self, path: Path) -> str:
        parts: list[str] = []
        with self._fitz.open(str(path)) as document:
            for page in document:
                parts.append(page.get_text())
        return "\n".join(parts)


__all__ = ["PDFDocExtractor"]
