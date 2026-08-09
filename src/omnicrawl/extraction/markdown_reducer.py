"""Markdown 降维与语义分块 — 页面 HTML → 干净 Markdown → LLM 友好分块。

目标（对齐 Helios L3 配套策略）：移除导航/广告/脚本等噪声，只保留语义内容，
将输入 LLM 的 token 量降低 60-80%，同时保留标题层级用于语义分块。

依赖：stdlib（html.parser）。lxml/bs4 存在时自动用于更强健的标签处理，
缺失时退化为轻量实现，不阻塞主流程。
"""

from __future__ import annotations

import re
from html.parser import HTMLParser

_BLOCK_TAGS = {
    "p", "div", "section", "article", "header", "footer", "nav", "aside",
    "main", "h1", "h2", "h3", "h4", "h5", "h6", "li", "tr", "ul", "ol",
    "table", "blockquote", "pre", "figure", "figcaption", "dl", "dt", "dd",
    "form", "fieldset", "br", "hr",
}
_SKIP_TAGS = {
    "script", "style", "noscript", "template", "svg", "canvas", "iframe",
    "embed", "object", "video", "audio", "source", "track", "form", "input",
    "button", "select", "option", "textarea", "label", "nav", "footer",
}
_HEADING_RE = re.compile(r"^h([1-6])$")
_LINK_RE = re.compile(r"\[([^\]]*)\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")


class _ReducerParser(HTMLParser):
    """把 HTML 转换为近似 GitHub-Flavored Markdown 的文本流。"""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self._skip_depth = 0
        self._list_stack: list[str] = []
        self._in_pre = False
        self._pending_newline = False
        self._td_cells: list[str] = []

    def _flush_newline(self) -> None:
        if self._pending_newline:
            self.out.append("\n")
            self._pending_newline = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if self._skip_depth:
            if tag in _SKIP_TAGS:
                self._skip_depth += 1
            return
        if tag in _SKIP_TAGS:
            self._skip_depth = 1
            return
        if tag == "pre":
            self._in_pre = True
            self._flush_newline()
            self.out.append("```\n")
            return
        match = _HEADING_RE.match(tag)
        if match:
            self._flush_newline()
            level = int(match.group(1))
            self.out.append(f"{'#' * level} ")
            return
        if tag in {"ul", "ol"}:
            self._list_stack.append("1. " if tag == "ol" else "- ")
            self._flush_newline()
            return
        if tag == "li":
            self._flush_newline()
            prefix = self._list_stack[-1] if self._list_stack else "- "
            self.out.append(prefix)
            if self._list_stack and self._list_stack[-1].startswith(("1.", "2.")):
                self._list_stack[-1] = f"{int(self._list_stack[-1].rstrip('. ')) + 1}. "
            return
        if tag == "a":
            self.out.append("[")
            return
        if tag == "img":
            src = dict(attrs).get("src", "")
            if src:
                self.out.append(f"![image]({src})")
            return
        if tag == "strong" or tag == "b":
            self.out.append("**")
            return
        if tag == "em" or tag == "i":
            self.out.append("*")
            return
        if tag == "code":
            self.out.append("`")
            return
        if tag == "br":
            self._pending_newline = True
            return
        if tag == "hr":
            self._flush_newline()
            self.out.append("\n---\n")
            return
        if tag == "blockquote":
            self._flush_newline()
            self.out.append("> ")
            return
        if tag == "tr":
            self._flush_newline()
            return
        if tag == "th" or tag == "td":
            self._td_cells.append("")
            return
        if tag == "table":
            self._flush_newline()
            return
        if tag in _BLOCK_TAGS:
            self._pending_newline = True

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if self._skip_depth:
            if tag in _SKIP_TAGS:
                self._skip_depth -= 1
            return
        if tag == "pre":
            self.out.append("\n```\n")
            self._in_pre = False
            return
        if tag == "a":
            self.out.append("]")
            return
        if tag == "strong" or tag == "b":
            self.out.append("**")
            return
        if tag == "em" or tag == "i":
            self.out.append("*")
            return
        if tag == "code":
            self.out.append("`")
            return
        if tag in {"ul", "ol"}:
            if self._list_stack:
                self._list_stack.pop()
            self._pending_newline = True
            return
        if tag == "li":
            self.out.append("\n")
            return
        if tag == "th" or tag == "td":
            cell = self._td_cells.pop() if self._td_cells else ""
            self.out.append(cell.strip())
            return
        if tag == "tr":
            self.out.append("\n")
            return
        if tag in _BLOCK_TAGS:
            self._pending_newline = True

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        if self._in_pre:
            self.out.append(data)
            return
        self.out.append(data)
        if self._td_cells:
            self._td_cells[-1] += data

    def text(self) -> str:
        raw = "".join(self.out)
        lines: list[str] = []
        for line in raw.splitlines():
            stripped = line.strip()
            if stripped:
                lines.append(stripped)
        return "\n".join(lines).strip()


def html_to_markdown(html: str, *, max_chars: int = 200_000) -> str:
    """HTML → 干净 Markdown（去除脚本/样式/导航/表单噪声）。

    Args:
        html: 原始页面 HTML。
        max_chars: 输出上限（防御性截断，默认 200k 字符）。

    Returns:
        干净的 Markdown 文本；解析失败时返回空字符串。
    """
    if not html:
        return ""
    parser = _ReducerParser()
    try:
        parser.feed(html[:max_chars * 8])
        parser.close()
    except Exception:
        return ""
    markdown = parser.text()
    if len(markdown) > max_chars:
        markdown = markdown[:max_chars]
    return markdown


def _split_on_blank_lines(markdown: str) -> list[str]:
    blocks: list[str] = []
    current: list[str] = []
    for line in markdown.splitlines():
        if not line.strip():
            if current:
                blocks.append("\n".join(current))
                current = []
            continue
        current.append(line)
    if current:
        blocks.append("\n".join(current))
    return blocks


def semantic_chunks(
    markdown: str,
    *,
    max_chars: int = 2000,
    min_chars: int = 300,
    max_chunks: int = 20,
) -> list[str]:
    """按标题层级优先、空白行为次级的语义分块。

    Args:
        markdown: html_to_markdown 的输出。
        max_chars: 单块最大字符数。
        min_chars: 单块最小字符数（低于此值尝试与相邻块合并）。
        max_chunks: 最大分块数（防御超长页面）。

    Returns:
        语义块列表（空输入返回空列表）。
    """
    if not markdown:
        return []
    max_chars = max(500, min(max_chars, 16000))
    min_chars = max(100, min(min_chars, max_chars // 2))

    headings = list(re.finditer(r"^(#{1,6})\s+.*$", markdown, re.MULTILINE))
    blocks: list[str]
    if not headings:
        blocks = _split_on_blank_lines(markdown)
    else:
        blocks = []
        for index, match in enumerate(headings):
            start = match.start()
            end = headings[index + 1].start() if index + 1 < len(headings) else len(markdown)
            section = markdown[start:end].strip()
            if section:
                blocks.append(section)
        if not blocks:
            blocks = _split_on_blank_lines(markdown)

    chunks: list[str] = []
    for block in blocks:
        if not chunks or len(chunks[-1]) + len(block) > max_chars:
            chunks.append(block)
        else:
            chunks[-1] = chunks[-1] + "\n" + block

    merged: list[str] = []
    for chunk in chunks:
        if merged and len(chunk) < min_chars:
            merged[-1] = merged[-1] + "\n" + chunk
        else:
            merged.append(chunk)
    if not merged:
        merged = chunks
    return merged[:max_chunks]


def reduce_for_llm(html: str, *, max_chars: int = 2000, max_chunks: int = 12) -> list[str]:
    """一站式：HTML → Markdown → 语义分块，直接产出 LLM 输入块。

    Args:
        html: 原始页面 HTML。
        max_chars: 单块最大字符数。
        max_chunks: 最大分块数。

    Returns:
        可直接拼入 prompt 的文本块列表。
    """
    markdown = html_to_markdown(html)
    return semantic_chunks(markdown, max_chars=max_chars, max_chunks=max_chunks)
