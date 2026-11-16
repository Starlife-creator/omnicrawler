"""把采集结果导出为 Markdown（支柱①的交付能力）。

**为什么在这里**

实现住在 `omnicrawler/export/markdown_exporter.py`，原先被归入"文档子系统"。
但可达性分析显示它的**全部**消费者都是支柱①的路径：

    gui/views/result_table.py        结果表「导出 Markdown」
    gui/main.py                      主窗口导出动作
    gui/views/professional_review.py  专业复核导出

也就是说：**把采集结果导出成 Markdown 是爬虫支柱的交付形态**，
与 PDF/文档工作台无关。挂在文档子系统下会造成两个问题：

1. 文档工作台组件化后，爬虫结果**少一种交付格式**（用户已拍板：导出归支柱①）；
2. `result_table` 这类支柱核心界面反向依赖文档子系统，边界混乱。

本模块提供稳定入口 `export_results_markdown`，实现仍复用
`MarkdownExporter`（原样，不重写）——逻辑与输出格式完全不变。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from ..export.markdown_exporter import MarkdownExporter

__all__ = ["MarkdownExporter", "export_results_markdown", "export_single_record_markdown"]


def export_results_markdown(
    csv_path: Path,
    jsonl_path: Path | None = None,
    output_path: Path | None = None,
    *,
    include_evidence: bool = True,
    group_by: str | None = None,
    max_records: int = 0,
) -> Path:
    """把结果 CSV（可选带 JSONL 证据）导出为 Markdown，返回写入路径。

    稳定入口：GUI 与 CLI 都应经由这里，而不是直接摸 `MarkdownExporter`——
    将来换实现（模板化输出等）时只需改这一处。

    参数语义与 `MarkdownExporter.export_results` **逐字一致**，这里不做任何
    转换，避免"包装层悄悄改了行为"。
    """
    return MarkdownExporter.export_results(
        csv_path,
        jsonl_path,
        output_path,
        include_evidence=include_evidence,
        group_by=group_by,
        max_records=max_records,
    )

def export_single_record_markdown(
    record: dict[str, Any],
    output_path: Path | None = None,
    *,
    style: str = "card",
) -> str:
    """把**单条**记录导出为 Markdown（专业复核的「复制为 Markdown」用）。

    与 `export_results_markdown` 同理：这是支柱①的交付能力，
    支柱侧调用方不应直接摸 `MarkdownExporter`。
    参数语义与底层逐字一致，不做转换。
    """
    return MarkdownExporter.export_single_record(
        record,
        output_path,
        style=style,
    )
