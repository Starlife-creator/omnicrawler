"""Backward-compatible GUI import path for the shared i18n service."""

from __future__ import annotations

from typing import Any

from .. import i18n as _shared
from ..i18n import (
    DOMAIN,
    _,
    get_available_languages,
    get_current_language,
    ngettext,
    set_language,
)

__all__ = [
    "DOMAIN",
    "_",
    "get_available_languages",
    "get_current_language",
    "ngettext",
    "set_language",
]


def __getattr__(name: str) -> Any:
    """Expose legacy diagnostic globals such as ``_translation``."""
    return getattr(_shared, name)
