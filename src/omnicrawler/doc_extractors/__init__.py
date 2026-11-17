"""文档槽位抽取器包（批 C-1）：base（文本/JSON）+ html + pdf。

``extract_slots`` 是统一入口，按 document_type 分派：
- text：TextDocExtractor（regex / text）
- json：JSONDocExtractor（jsonpath / regex），content 为 JSON 字符串
- html：HTMLDocExtractor（css / regex / jsonpath 走 JSON-LD）
- pdf：PDFDocExtractor（regex / text），content 为 PDF 文件路径
"""

from __future__ import annotations

import json
import logging
from typing import Any

from .base import JSONDocExtractor, SlotHit, TextDocExtractor, _error_hit
from .html import HTMLDocExtractor
from .pdf import PDFDocExtractor

LOGGER = logging.getLogger(__name__)


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
        RuntimeError: PDF 缺 pdfplumber 依赖。
    """
    kind = str(document_type).casefold()
    if kind == "auto":
        # 富文档（docx/pptx/odt/epub/eml/txt/pdf/html）统一经 document_ir
        # 解析为纯文本后走 TextDocExtractor；css/jsonpath 槽位请显式指定
        # document_type="html"/"json" 以保留原始结构。
        #
        # 迁移后（2026-11-17）：`document_ir` 已随 PDF 工作台迁入插件
        # `pdf-workbench`。这里改为经 facade 按需取用：
        # * 插件已装 ⇒ 走原来的富文档解析；
        # * 插件未装 ⇒ 给出**可操作**提示，并指明可显式指定 document_type
        #   绕开富文档解析（html/json/text/pdf 四条路径都不依赖 document_ir）。
        from ..services.document_facade import document_capability

        if not document_capability()["available"]:
            raise RuntimeError(
                "document_type='auto' 需要 PDF 工作台插件（富文档解析依赖其 document_ir）。\n"
                "请安装 pdf-workbench 插件；或显式指定 document_type 为 "
                "html / json / text / pdf 之一（这四条路径不需插件）。"
            )
        from ..services.document_facade import parse_document

        doc = parse_document(content)
        return TextDocExtractor().extract(doc.to_text(), definitions)
    if kind == "html":
        return HTMLDocExtractor().extract(content, definitions)
    if kind == "json":
        # B-P2-14e：JSON 解析失败不再整体上抛，按定义产出错误槽位，其余槽位继续提取。
        try:
            payload = json.loads(content)
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            LOGGER.warning("JSON 槽位载荷解析失败，记录错误槽位: %s: %s",
                           type(exc).__name__, exc)
            return [_error_hit(definition, exc) for definition in definitions]
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
