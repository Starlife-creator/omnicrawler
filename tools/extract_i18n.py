#!/usr/bin/env python3
"""Extract translatable strings from OmniCrawler GUI source files.

Scans ``src/omnicrawler/gui/`` for ``_()`` and ``ngettext()`` calls,
outputs a gettext ``.pot`` template, and optionally generates an
English ``.po`` file with auto-translations.

Usage::

    python tools/extract_i18n.py                    # -> locale/omnicrawler-gui.pot
    python tools/extract_i18n.py --gen-po en_US     # -> locale/en_US/LC_MESSAGES/
"""

from __future__ import annotations

import ast
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
GUI_DIR = PROJECT_ROOT / "src" / "omnicrawler" / "gui"
LOCALE_DIR = PROJECT_ROOT / "locale"


def _iter_py_files(root: Path) -> list[Path]:
    return sorted(
        p for p in root.rglob("*.py")
        if "__pycache__" not in str(p) and p.stem != "__init__"
    )


def _extract_strings(filepath: Path) -> list[tuple[int, str]]:
    """Parse *filepath* and return (lineno, msgid) pairs for _() calls."""
    results: list[tuple[int, str]] = []
    try:
        tree = ast.parse(filepath.read_text(encoding="utf-8"))
    except SyntaxError:
        return results

    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func_id = _get_func_name(node.func)
            if func_id not in ("_", "ngettext"):
                continue
            if not node.args:
                continue
            first = node.args[0]
            if isinstance(first, ast.Constant) and isinstance(first.value, str):
                s = first.value.strip()
                if s:
                    results.append((first.lineno, s))
    return results


def _get_func_name(node: ast.expr) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def generate_pot(output: Path) -> int:
    """Generate .pot template. Returns number of unique strings."""
    seen: dict[str, list[tuple[Path, int]]] = {}
    for py_file in _iter_py_files(GUI_DIR):
        for lineno, msgid in _extract_strings(py_file):
            seen.setdefault(msgid, []).append((py_file, lineno))

    output.parent.mkdir(parents=True, exist_ok=True)
    with open(output, "w", encoding="utf-8") as f:
        f.write("# OmniCrawler GUI translation template\n")
        f.write(f"# Generated: {datetime.now().isoformat()}\n")
        f.write("#\n")
        f.write('msgid ""\n')
        f.write('msgstr ""\n')
        f.write('"Content-Type: text/plain; charset=UTF-8\\n"\n\n')

        for msgid in sorted(seen, key=str.casefold):
            locations = seen[msgid]
            for loc_file, loc_line in locations[:3]:
                rel = loc_file.relative_to(PROJECT_ROOT)
                f.write(f"#: {rel}:{loc_line}\n")
            escaped = msgid.replace('"', '\\"')
            f.write(f'msgid "{escaped}"\n')
            f.write('msgstr ""\n\n')

    return len(seen)


if __name__ == "__main__":
    pot_path = LOCALE_DIR / "omnicrawler-gui.pot"
    count = generate_pot(pot_path)
    print(f"Extracted {count} unique strings -> {pot_path}")
