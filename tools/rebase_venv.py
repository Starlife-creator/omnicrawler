"""Update the copied Windows venv after the project directory has moved.

CPython's ``pyvenv.cfg`` stores the base interpreter as an absolute path.
This helper is intentionally dependency-free and is run by the bundled base
interpreter before any launcher invokes ``.venv``.
"""

from __future__ import annotations

import sys
from pathlib import Path


def main() -> int:
    project_root = Path(__file__).resolve().parents[1]
    bundled_python = project_root / ".runtime" / "python" / "python.exe"
    venv_python = project_root / ".venv" / "Scripts" / "python.exe"
    config_path = project_root / ".venv" / "pyvenv.cfg"
    if not bundled_python.is_file():
        print(f"[ERROR] Bundled interpreter is missing: {bundled_python}", file=sys.stderr)
        return 1
    if not venv_python.is_file() or not config_path.is_file():
        print("[ERROR] Virtual environment not found. Run setup_windows.bat first.", file=sys.stderr)
        return 1

    bundled = str(bundled_python.resolve())
    expected = {
        "home": str(bundled_python.parent.resolve()),
        "include-system-site-packages": "false",
        "version": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        "executable": bundled,
        "command": f"{bundled} -m venv --copies {venv_python.parent.parent.resolve()}",
    }
    existing = config_path.read_text(encoding="utf-8")
    rendered = "".join(f"{key} = {value}\n" for key, value in expected.items())
    if existing != rendered:
        config_path.write_text(rendered, encoding="utf-8")
        print("[INFO] Rebased .venv for the current project directory.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
