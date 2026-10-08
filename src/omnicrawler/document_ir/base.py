"""统一文档中间表示（document_ir）基础类型。

``DocumentIR`` 是把任意文档（txt/html/docx/pptx/odt/epub/eml/pdf…）归一后的
结构化表示：标题、段落、表格、链接、元数据。下游消费方（doc_extractors 槽位
抽取、ConvertX 文档族导出、GUI 预览）只依赖此结构，不关心原始格式。

设计约束：
- 纯 Python，无外部 CLI；富文档解析依赖按需懒加载。
- ``to_text()`` / ``to_markdown()`` 提供两种导出视图，供 ConvertX 写 txt/md。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class DocumentBlock:
    """Reference a legacy view item in source order without duplicating content."""

    kind: str
    index: int
    heading_level: int = 0
    node_id: str = ""
    parent_id: str = ""


@dataclass(slots=True)
class DocumentIR:
    """一份文档的统一中间表示。"""

    source: Path
    kind: str                    # 规范化格式名（带前导点，如 '.docx'）
    title: str = ""
    paragraphs: list[str] = field(default_factory=list)
    tables: list[list[list[str]]] = field(default_factory=list)
    links: list[tuple[str, str]] = field(default_factory=list)  # (文本, href)
    metadata: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    paragraph_locators: list[dict[str, Any]] = field(default_factory=list)
    table_locators: list[dict[str, Any]] = field(default_factory=list)

    blocks: list[DocumentBlock] = field(default_factory=list)
    _sections: list[tuple[int, str]] = field(default_factory=list, init=False, repr=False)
    _source_digest: str | None = field(default=None, init=False, repr=False)

    def _block(self, kind: str, index: int, locator: dict[str, Any], heading_level: int = 0) -> DocumentBlock:
        if self._source_digest is None:
            digest = hashlib.sha256()
            if self.source.is_file():
                with self.source.open("rb") as source:
                    for chunk in iter(lambda: source.read(1024 * 1024), b""):
                        digest.update(chunk)
                self._source_digest = digest.hexdigest()
                self.metadata["source_sha256"] = self._source_digest
            else:
                self._source_digest = "unverified"
                self.metadata["source_identity"] = "unverified"
        identity = json.dumps([self._source_digest, kind, index, locator], sort_keys=True, ensure_ascii=False)
        node_id = "node-" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]
        if heading_level:
            while self._sections and self._sections[-1][0] >= heading_level:
                self._sections.pop()
        parent = self._sections[-1][1] if self._sections else ""
        if heading_level:
            self._sections.append((heading_level, node_id))
        return DocumentBlock(kind, index, heading_level, node_id, parent)

    def add_paragraph(self, text: str, *, locator: dict[str, Any] | None = None, heading_level: int = 0) -> None:
        self.blocks.append(self._block("paragraph", len(self.paragraphs), locator or {}, heading_level))
        self.paragraphs.append(text)
        self.paragraph_locators.append(dict(locator or {}))

    def add_table(self, cells: list[list[str]], *, locator: dict[str, Any] | None = None) -> None:
        self.blocks.append(self._block("table", len(self.tables), locator or {}))
        self.tables.append(cells)
        self.table_locators.append(dict(locator or {}))

    def table_row_locator(self, table_index: int, row_index: int) -> dict[str, Any]:
        """Resolve a logical table row to its original source, including reviewed merges."""
        if (type(table_index) is not int or type(row_index) is not int or table_index < 0
                or table_index >= len(self.tables) or row_index < 0 or row_index >= len(self.tables[table_index])):
            raise ValueError("Table row locator indexes are invalid")
        if self.table_locators and len(self.table_locators) != len(self.tables):
            raise ValueError("Table locators do not match table views")
        locator = self.table_locators[table_index] if self.table_locators else {}
        result = {key: value for key, value in locator.items() if key not in {"row_locators", "source_tables"}}
        rows = locator.get("row_locators")
        if rows is not None:
            if not isinstance(rows, list) or len(rows) != len(self.tables[table_index]) or not isinstance(rows[row_index], dict):
                raise ValueError("Table row provenance does not match table content")
            result.update(rows[row_index])
        else:
            result.update(source_table=table_index + 1, source_row=row_index + 1)
            block = next((block for block in self.blocks if block.kind == "table" and block.index == table_index), None)
            if block is not None:
                result.update(node_id=block.node_id, parent_id=block.parent_id)
        return result

    def promote_first_paragraph_to_title(self) -> None:
        if not self.paragraphs:
            return
        self.title = self.paragraphs.pop(0)
        for block in self.blocks:
            if block.kind == "paragraph" and block.index == 0:
                self.metadata["title_node_id"] = block.node_id
                break
        if self.paragraph_locators:
            self.metadata["title_locator"] = self.paragraph_locators.pop(0)
        self.blocks = [replace(block, index=block.index - 1 if block.kind == "paragraph" else block.index) for block in self.blocks
                       if block.kind != "paragraph" or block.index != 0]

    def ordered_blocks(self) -> list[DocumentBlock]:
        if self.blocks:
            expected = {("paragraph", index) for index in range(len(self.paragraphs))}
            expected.update(("table", index) for index in range(len(self.tables)))
            actual = [(block.kind, block.index) for block in self.blocks]
            if len(set(actual)) != len(actual) or set(actual) != expected:
                raise ValueError("DocumentIR blocks do not match paragraph/table views")
            return self.blocks
        # Historical manually-created IR lacks source ordering; keep its old view.
        self.metadata["block_order"] = "legacy_grouped_unverified"
        self.blocks = ([self._block("paragraph", index, self.paragraph_locators[index] if self.paragraph_locators else {})
                        for index in range(len(self.paragraphs))]
                       + [self._block("table", index, self.table_locators[index] if self.table_locators else {})
                          for index in range(len(self.tables))])
        return self.blocks

    # ── 导出视图 ─────────────────────────────────────────
    def to_text(self) -> str:
        """拼成纯文本（段落间空行；表格以制表符分隔）。"""
        blocks: list[str] = []
        if self.title:
            blocks.append(self.title)
        for block in self.ordered_blocks():
            if block.kind == "paragraph":
                blocks.append(self.paragraphs[block.index])
            else:
                for row in self.tables[block.index]:
                    blocks.append("\t".join(cell.replace("\n", " ").replace("\t", " ") for cell in row))
                blocks.append("")
        return "\n\n".join(blocks).strip()

    def to_markdown(self) -> str:
        """拼成 Markdown（标题 #、表格 | 分隔）。"""
        lines: list[str] = []
        if self.title:
            lines.append(f"# {self.title}")
            lines.append("")
        def escape(cell: str) -> str:
            return cell.replace("\\", "\\\\").replace("|", "\\|").replace("\n", " ").replace("\r", " ")

        for block in self.ordered_blocks():
            if block.kind == "paragraph":
                prefix = "#" * min(6, max(0, block.heading_level))
                lines.append((prefix + " " if prefix else "") + self.paragraphs[block.index])
                continue
            table = self.tables[block.index]
            if not table:
                continue
            width = max(len(row) for row in table)
            if not width:
                continue
            rows = [row + [""] * (width - len(row)) for row in table]
            lines.append("| " + " | ".join(escape(cell) for cell in rows[0]) + " |")
            lines.append("| " + " | ".join("---" for _ in range(width)) + " |")
            for row in rows[1:]:
                lines.append("| " + " | ".join(escape(cell) for cell in row) + " |")
            lines.append("")
        return "\n\n".join(lines).strip()

    def paragraph_count(self) -> int:
        return len(self.paragraphs)

    def table_count(self) -> int:
        return len(self.tables)


__all__ = ["DocumentBlock", "DocumentIR"]
