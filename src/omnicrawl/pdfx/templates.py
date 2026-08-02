"""PDF template resources shared by source, wheel and portable installs."""

from __future__ import annotations

from pathlib import Path

BUILTIN_PREFIX = "builtin:pdf/"
DEFAULT_PDF_TEMPLATE = f"{BUILTIN_PREFIX}generic_template.yaml"
LEGACY_DEFAULT_PDF_TEMPLATE = "configs/pdf/generic_template.yaml"


def builtin_pdf_resource(name: str = "generic_template.yaml") -> Path:
    """Return an installed PDF template resource without depending on a repo root."""
    candidate = Path(__file__).resolve().parent.parent / "templates" / "pdf" / name
    if not candidate.is_file():
        raise FileNotFoundError(f"内置 PDF 模板不存在: {name}")
    return candidate


def is_builtin_pdf_reference(value: str | Path) -> bool:
    return str(value).strip().replace("\\", "/").startswith(BUILTIN_PREFIX)


def resolve_builtin_pdf_reference(value: str | Path) -> Path:
    raw = str(value).strip().replace("\\", "/")
    if not raw.startswith(BUILTIN_PREFIX):
        raise ValueError(f"不是内置 PDF 资源引用: {value}")
    return builtin_pdf_resource(raw[len(BUILTIN_PREFIX):])


def resolve_pdf_project_config(value: str | Path) -> Path:
    """Resolve a PDF project config, retaining the historical default as an alias."""
    raw = str(value).strip() or DEFAULT_PDF_TEMPLATE
    normalized = raw.replace("\\", "/")
    if is_builtin_pdf_reference(normalized):
        return resolve_builtin_pdf_reference(normalized)
    candidate = Path(raw).expanduser().resolve()
    if candidate.is_file():
        return candidate
    if normalized == LEGACY_DEFAULT_PDF_TEMPLATE or normalized.endswith("/" + LEGACY_DEFAULT_PDF_TEMPLATE):
        return builtin_pdf_resource()
    return candidate
