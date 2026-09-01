"""Shared gettext translation service for presentation-neutral modules."""

from __future__ import annotations

import gettext as _gettext
import os
import sys
from pathlib import Path

DOMAIN = "omnicrawler-gui"

_translation: _gettext.NullTranslations | None = None
_current_language = "zh_CN"
_localedir: Path | None = None


def _has_domain(localedir: Path) -> bool:
    """Return whether *localedir* contains translations for ``DOMAIN``."""
    if not localedir.is_dir():
        return False
    return any(
        (lang_dir / "LC_MESSAGES" / f"{DOMAIN}.mo").is_file()
        or (lang_dir / "LC_MESSAGES" / f"{DOMAIN}.po").is_file()
        for lang_dir in localedir.iterdir()
        if lang_dir.is_dir()
    )


def _find_localedir() -> Path | None:
    """Find translations in source, wheel, or portable layouts."""
    here = Path(__file__).resolve().parent
    candidates: list[Path] = []
    for ancestor in (here, *here.parents):
        candidates.append(ancestor / "locale")
        candidates.append(ancestor / "locales")
    candidates.append(Path(sys.prefix) / "share" / "omnicrawler" / "locale")
    seen: set[Path] = set()
    for candidate in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)
        if _has_domain(candidate):
            return candidate
    return None


def set_language(lang: str = "zh_CN") -> None:
    """Set the process translation language, falling back to source text."""
    global _translation, _current_language, _localedir

    _current_language = lang
    if _localedir is None:
        _localedir = _find_localedir()
    if _localedir is None:
        _translation = _gettext.NullTranslations()
        return
    try:
        _translation = _gettext.translation(
            DOMAIN,
            localedir=str(_localedir),
            languages=[lang],
            fallback=True,
        )
    except (OSError, FileNotFoundError):
        _translation = _gettext.NullTranslations()


def _(text: str) -> str:
    """Translate *text*, returning it unchanged when no catalog is available."""
    if _translation is None:
        set_language(_current_language)
    assert _translation is not None
    return _translation.gettext(text)


def ngettext(singular: str, plural: str, n: int) -> str:
    """Translate a plural form."""
    if _translation is None:
        set_language(_current_language)
    assert _translation is not None
    return _translation.ngettext(singular, plural, n)


def get_current_language() -> str:
    return _current_language


def get_available_languages() -> list[str]:
    if _localedir is None:
        return [_current_language]
    languages = [_current_language]
    if _localedir.is_dir():
        for entry in _localedir.iterdir():
            if entry.is_dir() and (entry / "LC_MESSAGES").is_dir():
                languages.append(entry.name)
    return list(dict.fromkeys(languages))


set_language(os.environ.get("OMNICRAWL_LANG", "zh_CN"))
