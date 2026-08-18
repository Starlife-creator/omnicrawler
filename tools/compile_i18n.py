#!/usr/bin/env python3
"""Compile gettext .po files to .mo for OmniCrawler GUI.

Usage::

    python tools/compile_i18n.py              # builds all .po under locale/
    python tools/compile_i18n.py en_US        # builds only en_US
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
LOCALE_DIR = PROJECT_ROOT / "locale"


def compile_po(po_path: Path, mo_path: Path) -> bool:
    """Run msgfmt on *po_path*, write to *mo_path*."""
    mo_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.run(
            ["msgfmt", "--check-format", "-o", str(mo_path), str(po_path)],
            check=True,
            capture_output=True,
        )
        return True
    except subprocess.CalledProcessError as exc:
        print(f"Error compiling {po_path}: {exc.stderr.decode()}", file=sys.stderr)
        return False
    except FileNotFoundError:
        print("msgfmt not found. Install gettext tools.", file=sys.stderr)
        return False


def compile_all(lang: str | None = None) -> int:
    """Compile all .po files under locale/. Returns count of compiled files."""
    compiled = 0
    for po_path in sorted(LOCALE_DIR.rglob("*.po")):
        if lang and po_path.parent.parent.name != lang:
            continue
        mo_path = po_path.parent / "LC_MESSAGES" / "omnicrawler-gui.mo"
        if compile_po(po_path, mo_path):
            compiled += 1
            print(f"  {po_path.relative_to(PROJECT_ROOT)} -> {mo_path.relative_to(PROJECT_ROOT)}")
    return compiled


if __name__ == "__main__":
    lang = sys.argv[1] if len(sys.argv) > 1 else None
    print("Compiling translations...")
    count = compile_all(lang)
    print(f"Done: {count} .mo file(s) compiled.")
