"""document_ir 解析器注册表与标准库解析器（txt/html/eml）。

S1 范围：纯标准库 + 项目内 html_tools（自包含）解析，零外部 CLI。
富文档（docx/pptx/odt/epub）解析在 S2 以懒加载方式追加注册。

设计对齐 convertx 的注册表范式：``register_document_parser`` 装饰器 +
``sniff_document_format`` 后缀推断 + 可选依赖缺失时抛 ModuleNotFoundError。
"""

from __future__ import annotations

import email
import email.header
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ..core.encoding import read_text_auto
from ..extraction.html_tools import discover_links, node_attr, node_text, parse_html, select_nodes
from .base import DocumentIR

#: 解析器签名：输入文件 + options → 统一中间表示
DocumentParserFn = Callable[[Path, dict[str, Any]], DocumentIR]

DOCUMENT_PARSERS: dict[str, DocumentParserFn] = {}


def register_document_parser(*extensions: str) -> Callable[[DocumentParserFn], DocumentParserFn]:
    """注册一个文档解析器（装饰器）。extensions 含前导点，如 '.txt'。"""

    def _wrap(fn: DocumentParserFn) -> DocumentParserFn:
        for ext in extensions:
            DOCUMENT_PARSERS[ext.lower()] = fn
        return fn

    return _wrap


def sniff_document_format(path: Path) -> str | None:
    """按后缀推断格式（统一返回 DOCUMENT_PARSERS 中的 key）。

    Alias 归一化：.htm → .html（同一解析器）。
    """
    suffix = path.suffix.lower()
    if suffix == ".htm":
        return ".html"
    return suffix if suffix in DOCUMENT_PARSERS else None


def _require_file(path: Path) -> None:
    if not isinstance(path, Path):
        path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"document_ir: 输入文件不存在: {path}")
    if not path.exists():
        raise FileNotFoundError(f"document_ir: 输入文件路径无效: {path}")


def _require_parser(kind: str, hint: str) -> None:
    """缺可选依赖时的统一降级提示（对齐 convertx CLI 风格）。"""
    raise ModuleNotFoundError(
        f"缺少可选依赖 '{hint}'。该格式需要额外安装：pip install {hint}"
        f"（或 pip install omnicrawl[document]）",
        name=hint,
    )


# ── .txt ─────────────────────────────────────────────────
@register_document_parser(".txt")
def _parse_txt(path: Path, options: dict[str, Any]) -> DocumentIR:
    _require_file(path)
    fallback = str(options.get("encoding_fallback", "utf-8"))
    text, encoding = read_text_auto(path, fallback=fallback)
    lines = [ln.strip() for ln in text.splitlines()]
    paragraphs: list[str] = []
    buffer: list[str] = []
    for ln in lines:
        if not ln:
            if buffer:
                paragraphs.append("\n".join(buffer))
                buffer = []
            continue
        buffer.append(ln)
    if buffer:
        paragraphs.append("\n".join(buffer))
    title = paragraphs[0] if paragraphs else ""
    # 首段通常为标题：仅当它较短（如一行）时视为标题
    if title and "\n" not in title and len(title) <= 80:
        paragraphs = paragraphs[1:]
    else:
        title = path.stem
    return DocumentIR(
        source=path,
        kind=".txt",
        title=title,
        paragraphs=paragraphs,
        metadata={"encoding": encoding, "line_count": len(lines)},
    )


# ── .html / .htm ─────────────────────────────────────────
_HEADING_SELECTOR = "h1,h2,h3,h4,h5,h6"
_CONTENT_SELECTOR = "p,li,blockquote,h2,h3,h4,h5,h6"

# T3：正文主体抽取 —— 正文容器词典（按优先级）。
# 命中首个非空容器后只取容器内正文，跳过导航/页脚/侧栏；全部未命中回退全页选择。
# 覆盖常见 CMS/WordPress/Elementor 页面结构与语义化 main/article。
_MAIN_CONTAINER_SELECTORS = (
    "main",
    "article",
    "[role=main]",
    "#content",
    "#main",
    ".content",
    ".entry-content",
    ".post-content",
    ".article-content",
    ".page-content",
    ".main-content",
)


def _select_main_container(document: Any) -> Any | None:
    """按正文容器词典返回首个含实质文本的容器（无命中返回 None）。"""
    for selector in _MAIN_CONTAINER_SELECTORS:
        for node in select_nodes(document, selector):
            if node_text(node).strip():
                return node
    return None


@register_document_parser(".html")
def _parse_html(path: Path, options: dict[str, Any]) -> DocumentIR:
    _require_file(path)
    fallback = str(options.get("encoding_fallback", "utf-8"))
    raw, encoding = read_text_auto(path, fallback=fallback)
    document = parse_html(raw)
    if document is None:
        raise ValueError(f"document_ir: 无法解析 HTML: {path}")

    title = ""
    heads = select_nodes(document, "h1")
    if heads:
        title = node_text(heads[0])
    if not title:
        title_nodes = select_nodes(document, "title")
        if title_nodes:
            title = node_text(title_nodes[0]).strip()

    # 正文主体：容器命中则限定容器内，未命中回退全页选择（向后兼容）。
    main_node: Any = None
    if options.get("main_content", True):
        main_node = _select_main_container(document)
    content_root = main_node if main_node is not None else document
    paragraphs: list[str] = []
    for node in select_nodes(content_root, _CONTENT_SELECTOR):
        text = node_text(node)
        if text and text not in paragraphs:
            paragraphs.append(text)

    links: list[tuple[str, str]] = []
    for href, text, kind in discover_links(document):
        if kind == "link" and href:
            links.append((text or href, href))

    meta: dict[str, Any] = {"encoding": encoding}
    if main_node is not None:
        meta["main_container"] = True
    description = select_nodes(document, 'meta[name="description"]')
    if description:
        desc = node_attr(description[0], "content")
        if desc:
            meta["description"] = desc

    return DocumentIR(
        source=path,
        kind=".html",
        title=title,
        paragraphs=paragraphs,
        links=links,
        metadata=meta,
    )


# ── .eml ─────────────────────────────────────────────────
_EML_HEADERS = ("from", "to", "cc", "subject", "date", "message-id")


def _decode_header_value(value: str) -> str:
    """解码 RFC2047 encoded-word 头（如 '=?utf-8?B?...?='）。"""
    try:
        parts = email.header.decode_header(value)
        return "".join(
            part.decode(charset or "utf-8", errors="replace") if isinstance(part, bytes) else part
            for part, charset in parts
        ).strip()
    except Exception:  # noqa: BLE001 — 畸形头直接回原值，不崩溃
        return value


@register_document_parser(".eml")
def _parse_eml(path: Path, options: dict[str, Any]) -> DocumentIR:
    _require_file(path)
    with path.open("rb") as fh:
        raw = fh.read()
    msg = email.message_from_bytes(raw)
    headers: dict[str, str] = {}
    for key in _EML_HEADERS:
        value = msg.get(key, "")
        if value:
            headers[key] = _decode_header_value(str(value))
    title = headers.get("subject", path.stem)

    body_parts: list[str] = []
    for part in msg.walk():
        content_type = part.get_content_type()
        if content_type == "text/plain":
            payload = part.get_payload(decode=True)
            if payload:
                charset = part.get_content_charset() or "utf-8"
                if not isinstance(payload, bytes):
                    payload = str(payload).encode("utf-8", errors="replace")
                try:
                    body_parts.append(payload.decode(charset, errors="replace"))
                except LookupError:
                    body_parts.append(payload.decode("utf-8", errors="replace"))
            continue
        if content_type == "multipart/alternative":
            continue  # 子 part 已单独遍历

    paragraphs: list[str] = []
    for body in body_parts:
        for block in re.split(r"\n\s*\n", body):
            text = " ".join(block.split())
            if text:
                paragraphs.append(text)

    return DocumentIR(
        source=path,
        kind=".eml",
        title=title,
        paragraphs=paragraphs,
        metadata={"headers": headers, "body_parts": len(body_parts)},
    )


__all__ = [
    "DOCUMENT_PARSERS",
    "DocumentParserFn",
    "register_document_parser",
    "sniff_document_format",
]
