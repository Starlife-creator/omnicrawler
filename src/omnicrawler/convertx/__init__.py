"""convertx：表格 / 文档互转工具层（独立 CLI / GUI 复用）的公共入口。

实现已下沉至 :mod:`omnicrawler.convertx._core`（长期债治理：``__init__`` 只做导出）；
完整设计说明与实现见 ``_core.py``。外部 ``from omnicrawler.convertx import ...``
用法完全不变。

★ 显式点名导出（P0-2 收尾修复）：早先用 ``from ._core import *``，但通配导入
**不会带出下划线名**，且静态检查器 ``tools/check_release_integrity.py``
（``_defined_names``）无法从 ``import *`` 推断包级可见名——导致 ``convert`` /
``READERS`` / ``WRITERS`` / ``sniff_format`` / ``ConvertResult`` /
``TaskProgressEvent`` / ``ConversionCancelledError`` / ``XLSX_ROW_LIMIT`` /
``_DECODE_CHUNK_BYTES`` / ``_ENCODING_SAMPLE_BYTES`` 等名字在包级缺失
（AttributeError，并使 ``pytest tests/unit`` 在收集期中断）。
现改为逐名显式导入 + 显式 ``__all__``，与 ``plugins/plugins.py`` 的门面写法一致。
"""

from __future__ import annotations

from ._core import (
    _DECODE_CHUNK_BYTES,
    _ENCODING_SAMPLE_BYTES,
    READERS,
    WRITERS,
    XLSX_ROW_LIMIT,
    CanonicalRecords,
    ConversionCancelledError,
    ConvertResult,
    ProgressTracker,
    ReaderFn,
    StageSpec,
    TaskProgressEvent,
    WriterFn,
    _ensure_parent_dir,
    _ordered_columns,
    atomic_output,
    check_cancel,
    convert,
    read_csv,
    read_jsonl,
    register_reader,
    register_writer,
    sniff_format,
    write_csv,
    write_jsonl,
)

__all__ = [
    "READERS",
    "WRITERS",
    "CanonicalRecords",
    "ConversionCancelledError",
    "ConvertResult",
    "ProgressTracker",
    "ReaderFn",
    "StageSpec",
    "TaskProgressEvent",
    "WriterFn",
    "XLSX_ROW_LIMIT",
    "atomic_output",
    "check_cancel",
    "convert",
    "read_csv",
    "read_jsonl",
    "register_reader",
    "register_writer",
    "sniff_format",
    "write_csv",
    "write_jsonl",
    "_DECODE_CHUNK_BYTES",
    "_ENCODING_SAMPLE_BYTES",
    "_ensure_parent_dir",
    "_ordered_columns",
]
