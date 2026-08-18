"""文档槽位抽取器包（批 C-1）：base（文本/JSON）+ html + pdf。

``extract_slots`` 是统一入口，按 document_type 分派：
- text：TextDocExtractor（regex / text）
- json：JSONDocExtractor（jsonpath / regex），content 为 JSON 字符串
- html：HTMLDocExtractor（css / regex / jsonpath 走 JSON-LD）
- pdf：PDFDocExtractor（regex / text），content 为 PDF 文件路径
"""

from __future__ import annotations

import json
from typing import Any

from .base import JSONDocExtractor, SlotHit, TextDocExtractor
from .html import HTMLDocExtractor
from .pdf import PDFDocExtractor


def extract_slots(
    content: str,
    definitions: list[Any],
    *,
    document_type: str = "text",
) -> list[SlotHit]:
    """按文档类型抽取槽位。

    Args:
        content: 文档内容——text/html 传字符串；json 传 JSON 字符串；
            pdf 传文件路径；auto 传文件路径（经 document_ir 解析为文本）。
        definitions: SlotDefinition 列表。
        document_type: text | json | html | pdf | auto。

    Raises:
        RuntimeError: PDF 缺 PyMuPDF 依赖。
    """
    kind = str(document_type).casefold()
    if kind == "auto":
        # 富文档（docx/pptx/odt/epub/eml/txt/pdf/html）统一经 document_ir
        # 解析为纯文本后走 TextDocExtractor；css/jsonpath 槽位请显式指定
        # document_type="html"/"json" 以保留原始结构。
        from ..document_ir import parse_document

        doc = parse_document(content)
        return TextDocExtractor().extract(doc.to_text(), definitions)
    if kind == "html":
        return HTMLDocExtractor().extract(content, definitions)
    if kind == "json":
        payload = json.loads(content)
        return JSONDocExtractor().extract(payload, definitions)
    if kind == "pdf":
        return PDFDocExtractor().extract(content, definitions)
    return TextDocExtractor().extract(content, definitions)


__all__ = [
    "HTMLDocExtractor",
    "JSONDocExtractor",
    "PDFDocExtractor",
    "SlotHit",
    "TextDocExtractor",
    "extract_slots",
]
