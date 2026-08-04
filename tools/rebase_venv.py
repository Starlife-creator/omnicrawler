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
                replaced = text.replace(str(old_root), str(project_root))
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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
