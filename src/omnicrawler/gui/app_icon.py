"""The application's own icon, as opposed to the UI glyphs in icon_registry.

`icon_registry` renders theme-coloured 24 px toolbar glyphs from inline SVG strings; this
module is about the product's identity, which must not follow the theme.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtGui import QIcon

from ..core.runtime_paths import package_resource

_DIR = ("omnicrawler", "gui", "branding")
_LADDER = (256, 128, 64, 48, 32, 24, 16)


def app_icon() -> QIcon:
    """Return the branded application icon.

    Both forms are registered on purpose. The multi-frame .ico is what Windows asks for --
    title bar, taskbar button, Alt-Tab and Explorer each request a different size -- while
    Linux and macOS pick from the PNG ladder. Qt silently skips an entry it cannot decode,
    so one code path serves every platform.
    """
    root = Path(package_resource(*_DIR))
    icon = QIcon()
    for candidate in (root / "omnicrawler.ico",
                      *(root / f"omnicrawler-icon-{size}.png" for size in _LADDER)):
        if candidate.is_file():
            icon.addFile(str(candidate))
    return icon
