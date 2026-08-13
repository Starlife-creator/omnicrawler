"""ConvertX 转换路径枚举（批 A-2，VERT 路径）。

VERT 路径 = 全部已知「格式族 → 格式族」转换组合及其当前可用性。

设计决策（A-2）：
- **族目录**：``FORMAT_FAMILIES`` 声明全部已知格式族及其扩展名 key
  （含别名归一：.ndjson→jsonl、.db→duckdb），是「完整能力图」而非仅当前可用项。
- **真源**：路径可用性由 READERS/WRITERS 注册表实时判定——可选依赖缺失时
  对应 Reader/Writer 不注册，enabled 自动为 False，无需重复声明依赖状态。
- **覆盖层**：CONVERTER_META 按 ``"{src}->{dst}"`` 覆盖默认展示（label/note/enabled），
  用于：禁用无意义的同格式路径、显式开启同族特殊转换（jsonl→jsonl 的 flat↔nested
  重排）、补充依赖安装提示。
- 同族路径默认 enabled=False（多数无文件级意义），由 META 显式开启。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from . import READERS, WRITERS  # import 时注册表已填充（含可选依赖探测）

#: 已知格式族：族名 → 注册表扩展名 key（前导点）
FORMAT_FAMILIES: dict[str, tuple[str, ...]] = {
    "csv": (".csv",),
    "jsonl": (".jsonl", ".ndjson"),
    "xlsx": (".xlsx",),
    "parquet": (".parquet",),
    "duckdb": (".duckdb", ".db"),
    # S2：文档族（document_ir 桥接，Reader=任意文档→记录；Writer=.txt/.md 导出）
    "document": (".txt", ".md", ".html", ".htm", ".eml", ".docx", ".pptx", ".odt", ".epub"),
}

#: 各族可选依赖说明（缺依赖时对应 Reader/Writer 不注册，enabled=False）
FORMAT_DEPENDENCIES: dict[str, str] = {
    "xlsx": "openpyxl（安装 omnicrawl[pdf] 或 omnicrawl[gui]）",
    "parquet": "pyarrow（安装 omnicrawl[storage]）",
    "duckdb": "duckdb（安装 omnicrawl[storage]）",
    "document": "python-docx/python-pptx（安装 omnicrawl[document]）；odt/epub 仅标准库",
}

#: 覆盖层："{src}->{dst}" → 展示元数据（label/note/enabled 均可覆盖）
CONVERTER_META: dict[str, dict[str, Any]] = {
    "jsonl->jsonl": {
        "enabled": True,
        "label": "JSONL 重排（flat ↔ nested）",
        "note": "配合 --flat / --nested，在流水线原始结构与扁平列之间互转",
    },
    # S2：文档族内部（docx→txt、epub→md 等）默认开启，走 document_ir 桥接
    "document->document": {
        "enabled": True,
        "label": "文档 → 文本/Markdown（document_ir）",
        "note": "docx/pptx/odt/epub/eml/html/txt → txt/md；缺 python-docx/pptx 时对应格式降级",
    },
    "csv->csv": {"enabled": False, "note": "同格式拷贝请用文件复制"},
    "xlsx->xlsx": {"enabled": False, "note": "同格式拷贝请用文件复制"},
    "parquet->parquet": {"enabled": False, "note": "同格式拷贝请用文件复制"},
    "duckdb->duckdb": {"enabled": False, "note": "同格式拷贝请用文件复制"},
}


@dataclass(frozen=True, slots=True)
class ConversionPath:
    """一条转换路径（源格式族 → 目标格式族）。"""

    src_family: str
    dst_family: str
    src_extensions: tuple[str, ...]
    dst_extensions: tuple[str, ...]
    enabled: bool
    label: str
    note: str = ""


def format_families() -> tuple[str, ...]:
    """全部已知格式族（稳定排序）。"""
    return tuple(FORMAT_FAMILIES)


def enumerate_paths() -> list[ConversionPath]:
    """枚举全部已知转换路径（族笛卡尔积）。

    可用性判定：
        enabled = CONVERTER_META 覆盖值（若指定）
        else = 非同族 且 源族有 Reader 且 目标族有 Writer
    缺依赖的读写侧通过 FORMAT_DEPENDENCIES 生成 note 提示。
    """
    families = tuple(FORMAT_FAMILIES)
    paths: list[ConversionPath] = []
    for src in families:
        for dst in families:
            src_exts = FORMAT_FAMILIES[src]
            dst_exts = FORMAT_FAMILIES[dst]
            reader_ok = any(ext in READERS for ext in src_exts)
            writer_ok = any(ext in WRITERS for ext in dst_exts)
            same_family = src == dst
            meta = CONVERTER_META.get(f"{src}->{dst}", {})
            enabled = bool(meta.get("enabled", not same_family and reader_ok and writer_ok))
            label = str(meta.get("label", f"{src} → {dst}"))
            hints: list[str] = []
            if not reader_ok and src in FORMAT_DEPENDENCIES:
                hints.append(f"读 {src} 需要 {FORMAT_DEPENDENCIES[src]}")
            if not writer_ok and dst in FORMAT_DEPENDENCIES:
                hints.append(f"写 {dst} 需要 {FORMAT_DEPENDENCIES[dst]}")
            note = str(meta.get("note", "；".join(hints)))
            paths.append(ConversionPath(
                src_family=src,
                dst_family=dst,
                src_extensions=src_exts,
                dst_extensions=dst_exts,
                enabled=enabled,
                label=label,
                note=note,
            ))
    return paths


def available_paths(*, enabled_only: bool = True) -> list[ConversionPath]:
    """当前可用的路径（默认只返回 enabled=True）。"""
    return [path for path in enumerate_paths() if not enabled_only or path.enabled]


def path_matrix() -> dict[str, list[str]]:
    """可用路径的二维视图：{源族: [目标族...]}（GUI 展示友好）。"""
    matrix: dict[str, list[str]] = {}
    for path in available_paths():
        matrix.setdefault(path.src_family, []).append(path.dst_family)
    return matrix


def describe() -> str:
    """路径清单文本（CLI --list-paths 使用）。"""
    lines = ["ConvertX 转换路径（VERT）:"]
    for path in enumerate_paths():
        marker = "✓" if path.enabled else "✗"
        note = f"  [{path.note}]" if path.note else ""
        default_label = f"{path.src_family} → {path.dst_family}"
        label_part = f"  {path.label}" if path.label != default_label else ""
        lines.append(f"  {marker} {default_label}{label_part}{note}")
    return "\n".join(lines)


__all__ = [
    "CONVERTER_META",
    "ConversionPath",
    "FORMAT_DEPENDENCIES",
    "FORMAT_FAMILIES",
    "available_paths",
    "describe",
    "enumerate_paths",
    "format_families",
    "path_matrix",
]
