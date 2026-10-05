"""统一文档中间表示（document_ir）基础类型。

``DocumentIR`` 是把任意文档（txt/html/docx/pptx/odt/epub/eml/pdf…）归一后的
结构化表示：标题、段落、表格、链接、元数据。下游消费方（doc_extractors 槽位
抽取、ConvertX 文档族导出、GUI 预览）只依赖此结构，不关心原始格式。

设计约束：
- 纯 Python，无外部 CLI；富文档解析依赖按需懒加载。
- ``to_text()`` / ``to_markdown()`` 提供两种导出视图，供 ConvertX 写 txt/md。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class DocumentBlock:
    """Reference a legacy view item in source order without duplicating content."""

    kind: str
    index: int
    heading_level: int = 0


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

    def add_paragraph(self, text: str, *, locator: dict[str, Any] | None = None, heading_level: int = 0) -> None:
        self.blocks.append(DocumentBlock("paragraph", len(self.paragraphs), heading_level))
        self.paragraphs.append(text)
        self.paragraph_locators.append(dict(locator or {}))

    def add_table(self, cells: list[list[str]], *, locator: dict[str, Any] | None = None) -> None:
        self.blocks.append(DocumentBlock("table", len(self.tables)))
        self.tables.append(cells)
        self.table_locators.append(dict(locator or {}))

    def promote_first_paragraph_to_title(self) -> None:
        if not self.paragraphs:
            return
        self.title = self.paragraphs.pop(0)
        if self.paragraph_locators:
            self.metadata["title_locator"] = self.paragraph_locators.pop(0)
        self.blocks = [DocumentBlock(block.kind, block.index - 1 if block.kind == "paragraph" else block.index,
                                     block.heading_level) for block in self.blocks
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
        return ([DocumentBlock("paragraph", index) for index in range(len(self.paragraphs))]
                + [DocumentBlock("table", index) for index in range(len(self.tables))])

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
