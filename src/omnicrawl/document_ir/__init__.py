"""统一文档中间表示（document_ir）：任意文档 → DocumentIR → 下游消费。

入口 ``parse_document`` 按后缀分派到 DOCUMENT_PARSERS 注册表：
- S1：.txt / .html / .htm / .eml（纯标准库 + 项目内 html_tools，零外部 CLI）
- S2：.docx / .pptx / .odt / .epub（懒加载 python-docx / python-pptx / zipfile+lxml）
- .pdf 复用 pdfx（不重构）

进度协议：``on_progress`` 接收 services.progress.TaskProgressEvent（单 stage "parse"），
与 convertx 同构，GUI Worker 可直接消费。
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from ..services.progress import ProgressTracker, StageSpec, TaskProgressEvent
from . import office  # noqa: F401 — 触发 .docx/.pptx/.odt/.epub 注册（懒加载依赖）
from .base import DocumentIR
from .parsers import DOCUMENT_PARSERS, sniff_document_format

__all__ = [
    "DOCUMENT_PARSERS",
    "DocumentIR",
    "parse_document",
    "sniff_document_format",
]


def parse_document(
    path: Path | str,
    options: dict[str, Any] | None = None,
    *,
    on_progress: Callable[[TaskProgressEvent], None] | None = None,
) -> DocumentIR:
    """解析文档为统一中间表示。

    Args:
        path: 输入文档路径。
        options: 解析选项，如 ``encoding_fallback``（.txt/.html 编码回退）。
        on_progress: 统一进度事件回调（单 stage "parse"）。

    Raises:
        FileNotFoundError: 文件不存在。
        ModuleNotFoundError: 该格式缺少可选依赖。
        ValueError: 格式不支持或内容无法解析。
    """
    src = Path(path).expanduser().resolve()
    kind = sniff_document_format(src)
    if kind is None:
        raise ValueError(f"document_ir: 不支持的文档格式: {src.suffix or '(无扩展名)'}")

    tracker = ProgressTracker(
        [StageSpec("parse", weight=1.0, display_name="文档解析")],
        task_id=str(src.stem),
        on_event=on_progress,
    )
    tracker.start()
    tracker.begin_stage("parse")
    try:
        parser = DOCUMENT_PARSERS[kind]
        result = parser(src, dict(options or {}))
        tracker.finish()
        return result
    except Exception:
        tracker.fail(f"解析失败: {kind}")
        raise
