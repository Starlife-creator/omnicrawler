"""ConvertX 转换路径枚举（批 A-2，VERT 路径）+ 能力目录视图。

VERT 路径 = 全部已知「格式族 → 格式族」转换组合及其当前可用性。

设计决策（A-2 + 能力目录升级）：
- **族目录**：``FORMAT_FAMILIES`` 声明全部已知格式族及其扩展名 key
  （含别名归一：.ndjson→jsonl、.db→duckdb），是「完整能力图」而非仅当前可用项。
  数据真源已下沉到 :mod:`omnicrawler.convertx.catalog`，此处保持向后兼容的再导出。
- **真源**：路径可用性由 READERS/WRITERS 注册表实时判定——可选依赖缺失时
  对应 Reader/Writer 不注册，enabled 自动为 False，无需重复声明依赖状态。
- **覆盖层**：CONVERTER_META 按 ``"{src}->{dst}"`` 覆盖默认展示（label/note/enabled），
  用于：禁用无意义的同格式路径、显式开启同族特殊转换（jsonl→jsonl 的 flat↔nested
  重排）、补充依赖安装提示。
- **可预览**：``assume_installed`` 接受 extra / 族名 / 扩展名，展示“装上后会新增哪些路径”。
- 同族路径默认 enabled=False（多数无文件级意义），由 META 显式开启。
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from .catalog import (
    FORMAT_DEPENDENCIES,
    FORMAT_FAMILIES,
    family_status,
    normalize_assume_installed,
    unlock_hints,
)

#: 覆盖层："{src}->{dst}" → 展示元数据（label/note/enabled 均可覆盖）
CONVERTER_META: dict[str, dict[str, Any]] = {
    "jsonl->jsonl": {
        "enabled": True,
        "label": "JSONL 重排（flat ↔ nested）",
        "note": "配合 --flat / --nested，在流水线原始结构与扁平列之间互转",
    },
    "document->document": {
        "enabled": True,
        "label": "文档 → 文档/文本/Markdown（document_ir）",
        "note": "docx/pptx/odt/epub/eml/html/pdf/txt → txt/md/html/docx/pdf/xlsx；缺依赖时按格式降级",
    },
    "csv->csv": {"enabled": False, "note": "同格式拷贝请用文件复制"},
    "xlsx->xlsx": {"enabled": False, "note": "同格式拷贝请用文件复制"},
    "parquet->parquet": {"enabled": False, "note": "同格式拷贝请用文件复制"},
    "duckdb->duckdb": {"enabled": False, "note": "同格式拷贝请用文件复制"},
    "image->image": {
        "enabled": True,
        "label": "图片重编码（Pillow）",
        "note": "png/jpg/webp/bmp/tiff/gif 互转；需要 Pillow（omnicrawler[ocr-tesseract]）",
    },
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
    simulated: bool = False


def format_families() -> tuple[str, ...]:
    """全部已知格式族（稳定排序）。"""
    return tuple(FORMAT_FAMILIES)


def enumerate_paths(*, assume_installed: Iterable[str] = ()) -> list[ConversionPath]:
    """枚举全部已知转换路径（族笛卡尔积）。

    可用性判定：
        enabled = CONVERTER_META 覆盖值（若指定）
        else = 非同族 且 源族可读 且 目标族可写
    缺依赖的读写侧通过 catalog 的安装提示生成 note。
    ``assume_installed`` 会模拟对应格式已注册，并标记 ``simulated=True``。
    """
    assumed = normalize_assume_installed(assume_installed)
    families = tuple(FORMAT_FAMILIES)
    paths: list[ConversionPath] = []
    for src in families:
        for dst in families:
            src_exts = FORMAT_FAMILIES[src]
            dst_exts = FORMAT_FAMILIES[dst]
            src_status = family_status(src, assume_installed=assumed)
            dst_status = family_status(dst, assume_installed=assumed)
            reader_ok = src_status.readable
            writer_ok = dst_status.writable
            same_family = src == dst
            meta = CONVERTER_META.get(f"{src}->{dst}", {})
            # META 只能收紧/放开语义，不能绕过“依赖真的注册了”这一事实。
            meta_enabled = bool(meta.get("enabled", not same_family))
            route_allowed = (src == "image") == (dst == "image")
            enabled = meta_enabled and reader_ok and writer_ok and route_allowed
            label = str(meta.get("label", f"{src} → {dst}"))
            hints: list[str] = []
            if not reader_ok:
                hints.append(f"读 {src} 需要 {src_status.install_hint or src_status.reason or '额外依赖'}")
            if not writer_ok:
                hints.append(f"写 {dst} 需要 {dst_status.install_hint or dst_status.reason or '额外依赖'}")
            note = str(meta.get("note", "；".join(hints)))
            paths.append(ConversionPath(
                src_family=src,
                dst_family=dst,
                src_extensions=src_exts,
                dst_extensions=dst_exts,
                enabled=enabled,
                label=label,
                note=note,
                simulated=bool(src_status.simulated or dst_status.simulated),
            ))
    return paths


def available_paths(
    *, enabled_only: bool = True, assume_installed: Iterable[str] = (),
) -> list[ConversionPath]:
    """当前可用的路径（默认只返回 enabled=True）。"""
    return [
        path for path in enumerate_paths(assume_installed=assume_installed)
        if not enabled_only or path.enabled
    ]


def newly_unlocked_paths(assume_installed: Iterable[str]) -> list[ConversionPath]:
    """模拟安装后新增的可用路径（与当前真实状态对比）。"""
    baseline = {(path.src_family, path.dst_family) for path in available_paths()}
    return [
        path for path in available_paths(assume_installed=assume_installed)
        if (path.src_family, path.dst_family) not in baseline
    ]


def path_matrix() -> dict[str, list[str]]:
    """可用路径的二维视图：{源族: [目标族...]}（GUI 展示友好）。"""
    matrix: dict[str, list[str]] = {}
    for path in available_paths():
        matrix.setdefault(path.src_family, []).append(path.dst_family)
    return matrix


def describe(*, assume_installed: Iterable[str] = ()) -> str:
    """路径清单文本（CLI --list-paths 使用），含缺失能力与解锁提示。"""
    assumed = normalize_assume_installed(assume_installed)
    lines = ["ConvertX 转换路径（VERT）:"]
    if assumed:
        lines.append(f"  模拟已安装格式: {', '.join(sorted(assumed))}")
    for path in enumerate_paths(assume_installed=assumed):
        # ASCII 标记：Windows 控制台默认 GBK 时 ✓/✗ 会直接 UnicodeEncodeError。
        marker = "[+]" if path.enabled else "[-]"
        note = f"  [{path.note}]" if path.note else ""
        default_label = f"{path.src_family} → {path.dst_family}"
        label_part = f"  {path.label}" if path.label != default_label else ""
        simulated = "  (模拟)" if path.simulated and path.enabled else ""
        lines.append(f"  {marker} {default_label}{label_part}{simulated}{note}")

    if assumed:
        new_paths = newly_unlocked_paths(assumed)
        lines.append("")
        lines.append(f"模拟安装后新增可用路径：{len(new_paths)} 条")
        for path in new_paths:
            lines.append(f"  + {path.src_family} → {path.dst_family}")

    hints = unlock_hints(assume_installed=assumed)
    if hints:
        lines.append("")
        lines.append("缺失能力与解锁提示：")
        for hint in hints:
            formats = " ".join(hint.formats)
            lines.append(f"  ! {hint.install}: {formats}")
            if hint.edges:
                preview = ", ".join(hint.edges[:8])
                more = " ..." if len(hint.edges) > 8 else ""
                lines.append(f"      可解锁: {preview}{more}")
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
    "newly_unlocked_paths",
    "path_matrix",
]
