"""PDF template resources shared by source, wheel and portable installs."""

from __future__ import annotations

from pathlib import Path

from ..core.builtin_references import (
    BUILTIN_PDF_PREFIX,
    DEFAULT_PDF_TEMPLATE,
    LEGACY_DEFAULT_PDF_TEMPLATE,
    builtin_pdf_resource,
    is_builtin_pdf_reference,
    resolve_builtin_pdf_reference,
)

# Historical public name retained for callers of omnicrawler.pdfx.templates.
BUILTIN_PREFIX = BUILTIN_PDF_PREFIX


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
