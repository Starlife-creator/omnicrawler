"""ConvertX 能力目录：格式可用性、缺失原因、安装提示与 ``assume-installed`` 预览。

设计目标（对齐成熟转换工具的 ``ConverterCatalog`` / ``EngineStatus`` 语义）：

- **可用性来自注册表**：Reader/Writer 是否注册是唯一真源；本模块只补“为什么缺”。
- **可行动**：每个缺失格式都带模块名与安装 extra；``unlock_hints`` 列出装上后会
  新增哪些格式对（按格式级边，不受族聚合影响）。
- **可预览**：``assume_installed`` 接受 extra 名、族名或扩展名，模拟安装后的目录，
  用于 CLI ``--list-paths --assume-installed storage`` 和 GUI 灰显提示。

本模块不执行安装、不下载任何东西；只做静态能力推断。
"""

from __future__ import annotations

import importlib.util
from collections.abc import Iterable
from dataclasses import dataclass, field

from ._core import READERS, WRITERS

#: 已知格式族：族名 → 注册表扩展名 key（前导点）。
FORMAT_FAMILIES: dict[str, tuple[str, ...]] = {
    "csv": (".csv",),
    "jsonl": (".jsonl", ".ndjson"),
    "xlsx": (".xlsx",),
    "parquet": (".parquet",),
    "duckdb": (".duckdb", ".db"),
    # 文档族（document_ir 桥接）：读入任意文档；写出 txt/md/html/docx/pdf/xlsx。
    "document": (".txt", ".md", ".html", ".htm", ".eml", ".docx", ".pptx", ".odt", ".epub", ".pdf"),
    "image": (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff", ".gif"),
}

#: 兼容旧展示层：族 → 依赖说明（保留给 GUI/文档，不再作为唯一真源）。
FORMAT_DEPENDENCIES: dict[str, str] = {
    "xlsx": "openpyxl（安装 omnicrawler[pdf] 或 omnicrawler[gui]）",
    "parquet": "pyarrow（安装 omnicrawler[storage]）",
    "duckdb": "duckdb（安装 omnicrawler[storage]）",
    "document": "python-docx/python-pptx（omnicrawler[document]）；reportlab（omnicrawler[pdf]）",
    "image": "Pillow（安装 omnicrawler[ocr-tesseract]）",
}

#: 格式 → (Python 模块, 安装提示)。模块可导入但未注册时仍会如实显示未知原因。
_FORMAT_REQUIREMENTS: dict[str, tuple[str, str]] = {
    ".xlsx": ("openpyxl", "omnicrawler[pdf] 或 omnicrawler[gui]"),
    ".parquet": ("pyarrow", "omnicrawler[storage]"),
    ".duckdb": ("duckdb", "omnicrawler[storage]"),
    ".db": ("duckdb", "omnicrawler[storage]"),
    ".docx": ("docx", "omnicrawler[document]"),
    ".pptx": ("pptx", "omnicrawler[document]"),
    ".pdf": ("pypdfium2", "omnicrawler[pdf]"),
    ".html": ("", ""),
    ".htm": ("", ""),
    ".eml": ("", ""),
    ".odt": ("", ""),
    ".epub": ("", ""),
    ".txt": ("", ""),
    ".md": ("", ""),
}

#: 只读格式：可以解析，但目前没有有意义的 Writer（不把“装依赖”误报成能写）。
_READ_ONLY_FORMATS: frozenset[str] = frozenset({".eml", ".epub", ".odt", ".pptx"})

_IMAGE_EXTENSIONS = FORMAT_FAMILIES["image"]
for _image_ext in _IMAGE_EXTENSIONS:
    _FORMAT_REQUIREMENTS[_image_ext] = ("PIL", "omnicrawler[ocr-tesseract]")

#: extra 名 → 会补齐的格式（用于 assume-installed 预览）。
EXTRA_FORMATS: dict[str, tuple[str, ...]] = {
    "storage": (".parquet", ".duckdb", ".db"),
    "document": (".docx", ".pptx"),
    "pdf": (".xlsx", ".pdf"),
    "gui": (".xlsx",),
    "ocr-tesseract": _IMAGE_EXTENSIONS,
}
EXTRA_FORMATS["full"] = tuple(dict.fromkeys(
    extension for extensions in FORMAT_FAMILIES.values() for extension in extensions
))

_ALIAS_EXTENSIONS: dict[str, str] = {
    "ndjson": ".jsonl",
    "db": ".duckdb",
    "jpeg": ".jpg",
    "tif": ".tiff",
}


@dataclass(frozen=True, slots=True)
class FormatStatus:
    extension: str
    readable: bool
    writable: bool
    available: bool
    simulated: bool = False
    can_read: bool = True
    can_write: bool = True
    reason: str = ""
    install_hint: str = ""


@dataclass(frozen=True, slots=True)
class FamilyStatus:
    family: str
    extensions: tuple[str, ...]
    readable: bool
    writable: bool
    available: bool
    simulated: bool = False
    missing_formats: tuple[str, ...] = ()
    reason: str = ""
    install_hint: str = ""


@dataclass(frozen=True, slots=True)
class MissingCapability:
    extension: str
    readable: bool
    writable: bool
    reason: str
    install_hint: str
    can_read: bool = True
    can_write: bool = True
    unlocked_edges: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class UnlockHint:
    install: str
    formats: tuple[str, ...] = ()
    edges: tuple[str, ...] = ()


def _normalize_extension(value: str) -> str:
    text = value.strip().lower()
    if not text:
        return ""
    if not text.startswith("."):
        text = _ALIAS_EXTENSIONS.get(text, f".{text}")
    return _ALIAS_EXTENSIONS.get(text, text)


def _module_present(module: str) -> bool:
    if not module:
        return True
    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ValueError):
        return False


def _all_formats() -> tuple[str, ...]:
    seen: dict[str, None] = {}
    for extensions in FORMAT_FAMILIES.values():
        for extension in extensions:
            seen[_normalize_extension(extension)] = None
    return tuple(seen)


def known_formats() -> tuple[str, ...]:
    """全部已知格式（归一化、去重、稳定顺序），供 planner / 测试使用。"""
    return _all_formats()


def normalize_assume_installed(values: Iterable[str]) -> frozenset[str]:
    """把 extra / 族名 / 扩展名统一成扩展名集合。

    未知名称不静默忽略：抛 ``ValueError``，避免用户以为预览生效了。
    """
    assumed: set[str] = set()
    known = set(_all_formats())
    for raw in values:
        value = raw.strip().lower()
        if not value:
            continue
        if value in EXTRA_FORMATS:
            assumed.update(EXTRA_FORMATS[value])
            continue
        if value in FORMAT_FAMILIES:
            assumed.update(FORMAT_FAMILIES[value])
            continue
        extension = _normalize_extension(value)
        if extension not in known:
            options = ", ".join(sorted([*EXTRA_FORMATS, *FORMAT_FAMILIES]))
            raise ValueError(
                f"ConvertX: 未知的 assume-installed 名称 {raw!r}；可用 extra/族: {options}"
            )
        assumed.add(extension)
    return frozenset(assumed)


def _registered(extension: str) -> tuple[bool, bool]:
    normalized = _normalize_extension(extension)
    return normalized in READERS, normalized in WRITERS


def format_status(extension: str, *, assume_installed: Iterable[str] = ()) -> FormatStatus:
    normalized = _normalize_extension(extension)
    assumed = normalize_assume_installed(assume_installed)
    can_read = True
    can_write = normalized not in _READ_ONLY_FORMATS
    native_image = normalized in _IMAGE_EXTENSIONS and _module_present("PIL")
    registered_reader = normalized in READERS or native_image
    registered_writer = normalized in WRITERS or native_image
    simulated = normalized in assumed and not (registered_reader or registered_writer)
    readable = (registered_reader or simulated) and can_read
    writable = (registered_writer or simulated) and can_write
    requirement_module, hint = _FORMAT_REQUIREMENTS.get(normalized, ("", ""))
    reason = ""
    if not readable and not writable:
        if requirement_module and not _module_present(requirement_module):
            reason = f"缺少 Python 模块 {requirement_module}"
        elif requirement_module:
            reason = f"模块 {requirement_module} 已安装但未注册（请检查安装完整性）"
        elif normalized in _READ_ONLY_FORMATS:
            reason = "该格式仅支持读取（当前未注册 Reader）"
        else:
            reason = "该格式未注册 Reader/Writer"
    return FormatStatus(
        extension=normalized,
        readable=readable,
        writable=writable,
        available=readable or writable,
        simulated=simulated,
        can_read=can_read,
        can_write=can_write,
        reason=reason,
        install_hint=hint,
    )


def family_status(family: str, *, assume_installed: Iterable[str] = ()) -> FamilyStatus:
    if family not in FORMAT_FAMILIES:
        raise KeyError(f"ConvertX: 未知格式族 {family!r}")
    statuses = [format_status(extension, assume_installed=assume_installed)
                for extension in FORMAT_FAMILIES[family]]
    missing = tuple(status.extension for status in statuses if not status.available)
    hints = dict.fromkeys(status.install_hint for status in statuses if status.install_hint)
    reasons = [status.reason for status in statuses if status.reason]
    return FamilyStatus(
        family=family,
        extensions=FORMAT_FAMILIES[family],
        readable=any(status.readable for status in statuses),
        writable=any(status.writable for status in statuses),
        available=any(status.available for status in statuses),
        simulated=any(status.simulated for status in statuses),
        missing_formats=missing,
        reason=reasons[0] if reasons else "",
        install_hint="；".join(hints),
    )


def capabilities(*, assume_installed: Iterable[str] = ()) -> tuple[FamilyStatus, ...]:
    return tuple(family_status(family, assume_installed=assume_installed) for family in FORMAT_FAMILIES)


def missing_capabilities(*, assume_installed: Iterable[str] = ()) -> tuple[MissingCapability, ...]:
    """列出缺失的格式能力，以及“补上它会解锁哪些格式对”。

    格式对按扩展名/族展示，不受族聚合影响：例如缺 python-docx 时，
    document 族仍可用（txt/md），但 ``docx → md`` 会被明确列为待解锁。
    """
    statuses = {
        extension: format_status(extension, assume_installed=assume_installed)
        for extension in _all_formats()
    }
    readable_families = [
        family for family in FORMAT_FAMILIES
        if any(statuses[ext].readable for ext in FORMAT_FAMILIES[family])
    ]
    writable_families = [
        family for family in FORMAT_FAMILIES
        if any(statuses[ext].writable for ext in FORMAT_FAMILIES[family])
    ]
    def edge_allowed(extension: str, family: str) -> bool:
        # 图片族只与图片族互通；不要把“装 Pillow”误报成能 csv→png。
        if extension in _IMAGE_EXTENSIONS or family == "image":
            return extension in _IMAGE_EXTENSIONS and family == "image"
        return True

    missing: list[MissingCapability] = []
    for extension, status in statuses.items():
        if status.available:
            continue
        edges: list[str] = []
        if extension in _IMAGE_EXTENSIONS:
            if not status.readable and status.can_read:
                edges.append(f"{extension} → image")
            if not status.writable and status.can_write:
                edges.append(f"image → {extension}")
        else:
            if not status.readable and status.can_read:
                edges.extend(f"{extension} → {family}" for family in writable_families
                             if edge_allowed(extension, family))
            if not status.writable and status.can_write:
                edges.extend(f"{family} → {extension}" for family in readable_families
                             if edge_allowed(extension, family))
        missing.append(MissingCapability(
            extension=extension,
            readable=status.readable,
            writable=status.writable,
            reason=status.reason,
            install_hint=status.install_hint,
            can_read=status.can_read,
            can_write=status.can_write,
            unlocked_edges=tuple(dict.fromkeys(edges)),
        ))
    return tuple(missing)


def unlock_hints(*, assume_installed: Iterable[str] = ()) -> tuple[UnlockHint, ...]:
    """按安装 extra 聚合缺失能力；顺序稳定，便于 CLI/GUI 展示。"""
    grouped: dict[str, dict[str, list[str]]] = {}
    for capability in missing_capabilities(assume_installed=assume_installed):
        key = capability.install_hint or "无需额外依赖（请检查安装完整性）"
        entry = grouped.setdefault(key, {"formats": [], "edges": []})
        entry["formats"].append(capability.extension)
        entry["edges"].extend(capability.unlocked_edges)
    return tuple(
        UnlockHint(
            install=key,
            formats=tuple(entry["formats"]),
            edges=tuple(dict.fromkeys(entry["edges"])),
        )
        for key, entry in grouped.items()
    )


@dataclass(frozen=True, slots=True)
class CapabilitySnapshot:
    families: tuple[FamilyStatus, ...] = field(default_factory=tuple)
    missing: tuple[MissingCapability, ...] = field(default_factory=tuple)
    unlocks: tuple[UnlockHint, ...] = field(default_factory=tuple)


def snapshot(*, assume_installed: Iterable[str] = ()) -> CapabilitySnapshot:
    return CapabilitySnapshot(
        families=capabilities(assume_installed=assume_installed),
        missing=missing_capabilities(assume_installed=assume_installed),
        unlocks=unlock_hints(assume_installed=assume_installed),
    )


__all__ = [
    "CapabilitySnapshot",
    "EXTRA_FORMATS",
    "FORMAT_DEPENDENCIES",
    "FORMAT_FAMILIES",
    "FamilyStatus",
    "FormatStatus",
    "MissingCapability",
    "UnlockHint",
    "capabilities",
    "family_status",
    "format_status",
    "known_formats",
    "missing_capabilities",
    "normalize_assume_installed",
    "snapshot",
    "unlock_hints",
]
