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


# B07-001：可展示文件扩展名白名单。Windows os.startfile 按扩展名关联执行，
# 拒绝 .exe/.bat/.cmd 等可执行件——防输出目录混入非预期文件被双击/自动执行。
_OPENABLE_EXTENSIONS = {
    ".txt", ".md", ".csv", ".xlsx", ".xls", ".json", ".jsonl", ".pdf",
    ".html", ".htm", ".png", ".jpg", ".jpeg", ".log", ".yaml", ".yml",
}


def open_path(path: str | Path) -> None:
    target = Path(path).expanduser().resolve()
    # 目录放行（文件管理器中打开安全）；文件必须落在可展示扩展名白名单。
    if not target.is_dir() and target.suffix.lower() not in _OPENABLE_EXTENSIONS:
        raise ValueError(
            f"拒绝打开非展示型文件: {target}（扩展名 {target.suffix or '无'} 不在白名单）"
        )
    target_text = str(target)
    if sys.platform.startswith("win"):
        os.startfile(target_text)  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", target_text])
    else:
        subprocess.Popen(["xdg-open", target_text])
