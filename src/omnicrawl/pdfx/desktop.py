"""多个桌面模块共享的轻量系统交互。"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def suite_root() -> Path:
    configured = os.environ.get("PDF_DATA_SUITE_ROOT", "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    current = Path.cwd().resolve()
    for candidate in (current, *current.parents):
        if (candidate / "pyproject.toml").is_file() and (candidate / "configs").is_dir():
            return candidate
    package = Path(__file__).resolve()
    for candidate in package.parents:
        if (candidate / "pyproject.toml").is_file():
            return candidate
    return current


def open_path(path: str | Path) -> None:
    target = str(Path(path).expanduser().resolve())
    if sys.platform.startswith("win"):
        os.startfile(target)  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", target])
    else:
        subprocess.Popen(["xdg-open", target])
