"""HTML 文档槽位抽取器（批 C-1）。

支持槽位类型：
- css：CSS 选择器 → 首个命中节点文本（复用项目自研 stdlib 解析器 html_tools）
- regex：在页面全文文本上正则搜索（捕获组 1 优先）
- jsonpath：在页内 JSON-LD（application/ld+json）脚本上导航（core.jsonpath 引擎）

其他类型（text）忽略，交给 TextDocExtractor。
"""

from __future__ import annotations

from typing import Any

from ..core.safe_data import safe_json_loads
from ..extraction.html_tools import node_text, parse_html, select_nodes
from .base import SlotHit, _regex_value


class HTMLDocExtractor:
    """HTML 文档抽取器：css / regex / jsonpath(JSON-LD) 槽位。"""

    def extract(self, html_text: str, definitions: list[Any]) -> list[SlotHit]:
        document = parse_html(html_text)
        full_text = node_text(document)
        hits: list[SlotHit] = []
        for definition in definitions:
            if definition.extractor_type == "css":
                nodes = select_nodes(document, definition.pattern)
                if nodes:
                    value = node_text(nodes[0]).strip()
                    hits.append(SlotHit(
                        definition.slot_key, value, 1.0,
                        {"css": definition.pattern, "matches": len(nodes)},
                    ))
            elif definition.extractor_type == "regex":
                value, confidence, evidence = _regex_value(definition.pattern, full_text)
                if value is not None:
                    hits.append(SlotHit(definition.slot_key, value, confidence, evidence))
            elif definition.extractor_type == "jsonpath":
                self._jsonld_hits(html_text, definition, hits)
        return hits

    def _jsonld_hits(self, html_text: str, definition: Any, hits: list[SlotHit]) -> None:
        from ..core.jsonpath import json_path

        document = parse_html(html_text)
        for node in select_nodes(document, 'script[type="application/ld+json"]'):
            raw = node_text(node).strip()
            payload = safe_json_loads(raw)
            if payload is None:
                continue
            values = json_path(payload, definition.pattern)
            if values:
                hits.append(SlotHit(
                    definition.slot_key, values[0], 1.0,
                    {"jsonpath": definition.pattern, "matches": len(values)},
                ))
                break


__all__ = ["HTMLDocExtractor"]
