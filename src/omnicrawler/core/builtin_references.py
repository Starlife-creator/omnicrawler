"""Import-safe resolution of built-in configuration resource references.

This module owns references that are part of the public configuration schema.
Feature packages may re-export them, but core configuration must not import a
feature implementation merely to resolve an installed data file.
"""

from __future__ import annotations

from pathlib import Path

BUILTIN_PDF_PREFIX = "builtin:pdf/"
DEFAULT_PDF_TEMPLATE = f"{BUILTIN_PDF_PREFIX}generic_template.yaml"
LEGACY_DEFAULT_PDF_TEMPLATE = "configs/pdf/generic_template.yaml"


def builtin_pdf_resource(name: str = "generic_template.yaml") -> Path:
    """Return an installed PDF template resource without a repository root."""
    candidate = Path(__file__).resolve().parent.parent / "templates" / "pdf" / name
    if not candidate.is_file():
        raise FileNotFoundError(f"内置 PDF 模板不存在: {name}")
    return candidate


def is_builtin_pdf_reference(value: str | Path) -> bool:
    return str(value).strip().replace("\\", "/").startswith(BUILTIN_PDF_PREFIX)


def resolve_builtin_pdf_reference(value: str | Path) -> Path:
    """Resolve a ``builtin:pdf/`` reference while rejecting path traversal."""
    raw = str(value).strip().replace("\\", "/")
    if not raw.startswith(BUILTIN_PDF_PREFIX):
        raise ValueError(f"不是内置 PDF 资源引用: {value}")
    name = raw[len(BUILTIN_PDF_PREFIX):]
    base = builtin_pdf_resource().resolve().parent
    resolved = (base / name).resolve()
    if base not in resolved.parents:
        raise ValueError(f"内置 PDF 资源引用越界: {value}")
    if not resolved.is_file():
        raise FileNotFoundError(f"内置 PDF 模板不存在: {name}")
    return resolved
