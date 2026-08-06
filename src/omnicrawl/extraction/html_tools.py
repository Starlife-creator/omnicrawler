from __future__ import annotations

import html
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Any

BeautifulSoup: Any
Tag: Any
try:
    from bs4 import BeautifulSoup as _BeautifulSoup
    from bs4.element import Tag as _Tag
    BeautifulSoup = _BeautifulSoup
    Tag = _Tag
except ImportError:  # Base install keeps a small but useful selector fallback.
    BeautifulSoup = None
    Tag = ()


@dataclass(eq=False)
class MiniNode:
    tag: str
    attrs: dict[str, str] = field(default_factory=dict)
    parent: MiniNode | None = None
    children: list[MiniNode] = field(default_factory=list)
    parts: list[str] = field(default_factory=list)

    def iter_descendants(self) -> Iterable[MiniNode]:
        for child in self.children:
            yield child
            yield from child.iter_descendants()

    def get_text(self, separator: str = " ", strip: bool = True) -> str:
        values = list(self.parts)
        for child in self.children:
            values.append(child.get_text(separator, strip))
        text = separator.join(item for item in values if item)
        text = html.unescape(text)
        return re.sub(r"\s+", " ", text).strip() if strip else text


class _MiniParser(HTMLParser):
    VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = MiniNode("document")
        self.current = self.root

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        node = MiniNode(tag.lower(), {k.lower(): v or "" for k, v in attrs}, self.current)
        self.current.children.append(node)
        if tag.lower() not in self.VOID:
            self.current = node

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if self.current.tag == tag.lower() and tag.lower() not in self.VOID:
            self.current = self.current.parent or self.root

    def handle_endtag(self, tag: str) -> None:
        node = self.current
        while node is not self.root:
            if node.tag == tag.lower():
                self.current = node.parent or self.root
                return
            node = node.parent or self.root

    def handle_data(self, data: str) -> None:
        cleaned = data.strip()
        if cleaned:
            self.current.parts.append(cleaned)


def _match_simple(node: MiniNode, token: str) -> bool:
    attr_match = re.search(r"\[([\w:-]+)(?:\s*=\s*['\"]?([^'\"\]]+)['\"]?)?\]", token)
    if attr_match:
        key, expected = attr_match.group(1).lower(), attr_match.group(2)
        if key not in node.attrs or (expected is not None and node.attrs.get(key) != expected):
            return False
        token = token[:attr_match.start()] + token[attr_match.end():]
    id_match = re.search(r"#([\w-]+)", token)
    if id_match and node.attrs.get("id") != id_match.group(1):
        return False
    classes = re.findall(r"\.([\w-]+)", token)
    node_classes = set(node.attrs.get("class", "").split())
    if any(item not in node_classes for item in classes):
        return False
    tag = re.match(r"^[A-Za-z][\w-]*|^\*", token)
    return not tag or tag.group(0) == "*" or node.tag == tag.group(0).lower()


def _mini_select(context: MiniNode, selector: str) -> list[MiniNode]:
    results: list[MiniNode] = []
    for group in selector.split(","):
        tokens = [part for part in re.split(r"\s+|\s*>\s*", group.strip()) if part]
        current = [context]
        for token in tokens:
            following: list[MiniNode] = []
            for parent in current:
                following.extend(node for node in parent.iter_descendants() if _match_simple(node, token))
            current = following
        for item in current:
            if item not in results:
                results.append(item)
    return results


def parse_html(text: str) -> Any:
    if BeautifulSoup is not None:
        return BeautifulSoup(text, "html.parser")
    parser = _MiniParser()
    parser.feed(text)
    return parser.root


def select_nodes(context: Any, selector: str) -> list[Any]:
    if not selector:
        return [context]
    if BeautifulSoup is not None and hasattr(context, "select"):
        return list(context.select(selector))
    return _mini_select(context, selector)


def node_text(node: Any) -> str:
    if hasattr(node, "get_text"):
        return str(node.get_text(" ", strip=True))
    return str(node)


def node_attr(node: Any, name: str) -> str | None:
    if isinstance(node, MiniNode):
        return node.attrs.get(name.lower())
    if hasattr(node, "get"):
        value = node.get(name)
        if isinstance(value, list):
            return " ".join(map(str, value))
        return None if value is None else str(value)
    return None


def node_markup(node: Any) -> str:
    """Serialize either the optional BeautifulSoup tree or the built-in mini tree."""

    if isinstance(node, MiniNode):
        children = "".join(node_markup(child) for child in node.children)
        parts = "".join(html.escape(value) for value in node.parts)
        if node.tag == "document":
            return parts + children
        attributes = "".join(
            f' {html.escape(str(key))}="{html.escape(str(value), quote=True)}"'
            for key, value in node.attrs.items()
        )
        if node.tag in _MiniParser.VOID:
            return f"<{node.tag}{attributes}>"
        return f"<{node.tag}{attributes}>{parts}{children}</{node.tag}>"
    return str(node)


def discover_links(document: Any) -> list[tuple[str, str, str]]:
    found: list[tuple[str, str, str]] = []
    seen: set[str] = set()
    for selector, attribute, kind in (
        ("a[href]", "href", "link"), ("iframe[src]", "src", "link"),
        ("img[src]", "src", "media"), ("source[src]", "src", "media"),
        ("video[src]", "src", "media"), ("audio[src]", "src", "media"),
    ):
        for node in select_nodes(document, selector):
            value = node_attr(node, attribute)
            if not value:
                continue
            # S2.5.34：伪协议链接（javascript:/mailto:/tel:/void(0)）直接过滤
            if _is_pseudo_protocol(value):
                continue
            key = value.casefold()
            if key in seen:
                continue  # 链接去重
            seen.add(key)
            found.append((value, node_text(node), kind))
    return found


def _is_pseudo_protocol(value: str) -> bool:
    """S2.5.34：伪协议/空协议链接识别（javascript:、mailto:、tel:、void(0) 等）。"""
    lowered = value.strip().casefold()
    if lowered.startswith(("javascript:", "mailto:", "tel:", "sms:", "data:", "file:", "about:")):
        return True
    if lowered.startswith(("javascript", "vbscript")) and "(" in lowered:
        return True
    if "void(0)" in lowered or "void (0)" in lowered:
        return True
    return False
