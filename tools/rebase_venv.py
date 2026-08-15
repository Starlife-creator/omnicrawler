"""Update the copied Windows venv after the project directory has moved.

CPython's ``pyvenv.cfg`` stores the base interpreter as an absolute path.
This helper is intentionally dependency-free and is run by the bundled base
interpreter before any launcher invokes ``.venv``.

It also reconciles the installed ``omnicrawl-platform`` metadata with the
source tree: if the editable install points at a stale directory or version
(e.g. the whole project folder was moved, or ``bump_version.py`` advanced the
version), this helper re-runs ``pip install -e .`` so the environment
self-heals on startup.  The optional dependency extras are intentionally not
reinstalled here (``pip install -e .`` alone only refreshes the project's own
metadata/scripts and leaves already-satisfied dependencies untouched).
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

_SOURCE_VERSION_RE = re.compile(r'^__version__\s*=\s*"([^"]+)"', re.MULTILINE)


def read_source_version(project_root: Path) -> str | None:
    """Read __version__ from src/omnicrawl/__init__.py (dependency-free)."""
    init_path = project_root / "src" / "omnicrawl" / "__init__.py"
    try:
        match = _SOURCE_VERSION_RE.search(init_path.read_text(encoding="utf-8"))
    except OSError:
        return None
    return match.group(1) if match else None


def read_installed_version(venv_python: Path) -> str | None:
    """Read the installed editable omnicrawl-platform version (or None)."""
    try:
        result = subprocess.run(
            [str(venv_python), "-c",
             "import importlib.metadata; print(importlib.metadata.version('omnicrawl-platform'))"],
            capture_output=True, text=True, timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def _rebase_pth_lines(text: str, old: str, new: str) -> str:
    """B12-006：.pth 行级精确替换，替代全局 ``replace``。

    只替换**以旧路径为前缀**的整行路径，避免不同虚拟环境含同名路径片段时
    全局误替；同时兼容反斜杠/正斜杠两种路径分隔符形态，并保留行尾换行。
    """
    variants = (old, old.replace("\\", "/")) if "\\" in old else (old,)
    lines: list[str] = []
    for line in text.splitlines(keepends=True):
        body = line.rstrip("\r\n")
        ending = line[len(body):]
        stripped = body.strip()
        replaced = line
        for prefix in variants:
            if stripped.startswith(prefix):
                indent = body[: len(body) - len(body.lstrip())]
                suffix = stripped[len(prefix):]
                replaced = f"{indent}{new}{suffix}{ending}"
                break
        lines.append(replaced)
    return "".join(lines)


def reinstall_editable(venv_python: Path, project_root: Path) -> bool:
    """Re-run pip install -e . so dist-info/direct_url/scripts track the source.

    Deliberately bare `pip install -e .` (no extras): the goal is metadata
    reconciliation, not a dependency reinstall. Already-satisfied packages are
    untouched, so this is fast and offline-friendly when the venv is complete.
    """
    try:
        result = subprocess.run(
            [str(venv_python), "-m", "pip", "install", "-e", str(project_root)],
            capture_output=True, text=True, timeout=600,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


_DIST_INFO_RE = re.compile(r"omnicrawl_platform-[^.]+\.dist-info$", re.IGNORECASE)
_METADATA_VERSION_RE = re.compile(r"^Version:\s*.*$", re.MULTILINE)


def reconcile_installed_version(venv_python: Path, project_root: Path) -> None:
    """Ensure installed omnicrawl-platform metadata matches the source version.

    Runs on every launch; when the source version is unavailable (broken tree)
    it silently no-ops; when it differs from the installed metadata it first
    tries ``pip install -e .`` and falls back to directly rewriting the
    dist-info METADATA/direct_url records.
    """
    src_version = read_source_version(project_root)
    if not src_version:
        return
    installed = read_installed_version(venv_python)
    if installed == src_version:
        return
    print(
        f"[INFO] 版本不一致: installed={installed!r} source={src_version!r}，"
        f"自动重装 editable 元数据…"
    )
    if reinstall_editable(venv_python, project_root):
        refreshed = read_installed_version(venv_python)
        if refreshed == src_version:
            print(f"[INFO] editable 元数据已对齐到 {src_version}。")
            return
        print(f"[WARN] pip 重装后仍不一致 (installed={refreshed!r})，尝试直接覆写 dist-info。")
    site_packages = project_root / ".venv" / "Lib" / "site-packages"
    dist_info = next(site_packages.glob("omnicrawl_platform-*.dist-info"), None) if site_packages.is_dir() else None
    if dist_info is None:
        print("[ERROR] 找不到 omnicrawl_platform-*.dist-info，无法对齐版本。请手动重跑 pip install -e .")
        return
    metadata_path = dist_info / "METADATA"
    direct_url = dist_info / "direct_url.json"
    try:
        metadata = metadata_path.read_text(encoding="utf-8")
        if _METADATA_VERSION_RE.search(metadata):
            metadata_path.write_text(
                _METADATA_VERSION_RE.sub(f"Version: {src_version}", metadata, count=2),
                encoding="utf-8",
            )
        if direct_url.is_file():
            payload = {"dir_info": {"editable": True}, "url": project_root.as_uri()}
            direct_url.write_text(json.dumps(payload), encoding="utf-8")
        print(f"[INFO] 已直接覆写 {dist_info.name} 的 METADATA/direct_url.json → {src_version}")
    except OSError as exc:
        print(f"[ERROR] 覆写 dist-info 失败: {exc}")


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
    # F52：解析原文件只更新需变更的键，不再整体覆盖（保留 prompt 等既有键）
    updated: dict[str, str] = {}
    for line in existing.splitlines():
        if " = " in line:
            key, _, value = line.partition(" = ")
            updated[key.strip()] = value.strip()
    old_home = updated.get("home")
    updated.update(expected)
    rendered = "".join(f"{key} = {value}\n" for key, value in updated.items())
    if existing != rendered:
        config_path.write_text(rendered, encoding="utf-8")
        print("[INFO] Rebased .venv for the current project directory.")

    # F51：一并重写 site-packages 内 editable/绝对路径引用（__editable__*.pth 等），
    # 否则 pyvenv.cfg 已 rebase 但 import omnicrawl 仍指旧位置
    if old_home:
        old_runtime = Path(old_home)
        old_root = (
            old_runtime.parent.parent.parent
            if old_runtime.parent.name == "python" and old_runtime.parent.parent.name == ".runtime"
            else None
        )
        site_packages = project_root / ".venv" / "Lib" / "site-packages"
        if old_root is not None and site_packages.is_dir():
            rewritten = 0
            for pth in site_packages.glob("*.pth"):
                try:
                    text = pth.read_text(encoding="utf-8")
                except (OSError, UnicodeError):
                    continue
                replaced = _rebase_pth_lines(text, str(old_root), str(project_root))
                if replaced != text:
                    try:
                        pth.write_text(replaced, encoding="utf-8")
                        rewritten += 1
                    except OSError:
                        pass
            if rewritten:
                print(f"[INFO] 重写了 {rewritten} 个 site-packages .pth 路径引用。")
        # 二进制 .exe 内嵌解释器路径无法安全改写——搬迁后若 import 仍指向旧路径，
        # 提示重新安装 editable 包
        if old_root is not None and old_root != project_root:
            print("[INFO] 若 `import omnicrawl` 仍解析到旧位置，请重新执行: pip install -e .")

    # F53：版本对账——installed 元数据与源码 __version__ 不一致时自动收敛。
    reconcile_installed_version(venv_python, project_root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
