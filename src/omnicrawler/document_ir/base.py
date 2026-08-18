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

    # ── 导出视图 ─────────────────────────────────────────
    def to_text(self) -> str:
        """拼成纯文本（段落间空行；表格以制表符分隔）。"""
        blocks: list[str] = []
        if self.title:
            blocks.append(self.title)
        blocks.extend(self.paragraphs)
        for table in self.tables:
            for row in table:
                blocks.append("\t".join(cell.replace("\n", " ").replace("\t", " ") for cell in row))
            blocks.append("")
        return "\n\n".join(blocks).strip()

    def to_markdown(self) -> str:
        """拼成 Markdown（标题 #、表格 | 分隔）。"""
        lines: list[str] = []
        if self.title:
            lines.append(f"# {self.title}")
            lines.append("")
        lines.extend(self.paragraphs)
        for table in self.tables:
            if not table:
                continue
            header = table[0]
            lines.append("| " + " | ".join(header) + " |")
            lines.append("| " + " | ".join("---" for _ in header) + " |")
            for row in table[1:]:
                cells = row if len(row) == len(header) else row + [""] * (len(header) - len(row))
                lines.append("| " + " | ".join(cell.replace("\n", " ").replace("|", "\\|") for cell in cells) + " |")
            lines.append("")
        return "\n\n".join(lines).strip()

    def paragraph_count(self) -> int:
        return len(self.paragraphs)

    def table_count(self) -> int:
        return len(self.tables)


__all__ = ["DocumentIR"]
