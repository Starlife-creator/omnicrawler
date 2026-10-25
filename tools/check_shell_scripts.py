#!/usr/bin/env python3
"""根目录 shell / command 脚本静态检查（C-#17 / C-#18 / C-#11）。

覆盖范围：仓库根 ``*.sh`` 与 ``*.command``（``packaging/linux/`` 交付件由
``tools/check_linux_delivery.py`` 单独校验，本脚本不重复扫描）。

校验项：

1. **语法**：``bash -n`` 静态解析，不执行脚本；
2. **失败保护**：bash 脚本必须含 ``set -euo pipefail``，POSIX sh 脚本必须含 ``set -eu``；
3. **启动器**：``run_*`` 必须先检查 ``.venv/bin/python`` 存在性，错误提示写到 stderr；
4. **安装脚本**：必须含 Python ≥3.12 版本断言，不得再引用已改名的旧模型下载工具；
5. **构建脚本**：构建 venv 不得安装 ``dev`` extra（测试/lint 工具），必须安装 ``build`` extra。

用法::

    python tools/check_shell_scripts.py
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]

#: 源码目录启动器：必须有 venv 存在性守卫与 stderr 错误提示。
LAUNCHER_SCRIPTS = {
    "run_linux.sh",
    "run_gui_linux.sh",
    "run_macos.sh",
    "run_gui_macos.command",
    "run_workbench_linux.sh",
}

#: 源码环境初始化脚本：必须做 Python 版本断言。
SETUP_SCRIPTS = {"setup_linux.sh", "setup_macos.command"}

#: 便携包构建脚本：构建 venv 只装运行时 extras + build extra。
BUILD_SCRIPTS = {"build_linux.sh", "build_macos.sh"}

#: 已改名/废弃的工具引用（出现即判红，避免静默装到不存在的脚本）。
STALE_TOOL_REFS = ("download_ocr_models.py",)


def _shell_scripts(root: Path) -> list[Path]:
    return sorted({*root.glob("*.sh"), *root.glob("*.command")})


def _syntax_check(path: Path) -> list[str]:
    proc = subprocess.run(  # noqa: S603 - 命令为固定 bash + 本地脚本路径
        ["bash", "-n", str(path)],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).strip().replace("\n", " | ")
        return [f"{path.name}: bash -n 语法错误：{detail}"]
    return []


def _structural_checks(path: Path, text: str) -> list[str]:
    issues: list[str] = []
    name = path.name
    first_line = text.splitlines()[0] if text.splitlines() else ""

    if "bash" in first_line:
        if "set -euo pipefail" not in text:
            issues.append(f"{name}: bash 脚本缺少 `set -euo pipefail`（C-#17 失败保护）")
    elif first_line.startswith("#!"):
        if "set -eu" not in text:
            issues.append(f"{name}: POSIX sh 脚本缺少 `set -eu`（C-#17 失败保护）")

    if name in LAUNCHER_SCRIPTS:
        guard = re.search(r"if\s+\[.*\.venv/bin/python", text)
        if guard is None:
            issues.append(f"{name}: 启动器缺少 .venv/bin/python 存在性守卫（C-#17）")
        if ">&2" not in text:
            issues.append(f"{name}: 错误提示未写入 stderr（>&2）（C-#17）")

    if name in SETUP_SCRIPTS:
        if "version_info >= (3, 12)" not in text:
            issues.append(f"{name}: 缺少 Python ≥3.12 版本断言（C-#18）")
        for stale in STALE_TOOL_REFS:
            if stale in text:
                issues.append(f"{name}: 引用已废弃工具 {stale}（请改用 tools/download_and_smoke_test.py）")

    if name in BUILD_SCRIPTS:
        if "--extra dev" in text:
            issues.append(f"{name}: 构建 venv 不应安装 `dev` extra（C-#11 语义拆分，测试/lint 工具不进生产构建环境）")
        if "--extra build" not in text:
            issues.append(f"{name}: 构建 venv 必须显式安装 `build` extra（PyInstaller 打包工具）")

    return issues


def check(root: Path = PROJECT_ROOT) -> list[str]:
    issues: list[str] = []
    for path in _shell_scripts(root):
        text = path.read_text(encoding="utf-8")
        issues.extend(_syntax_check(path))
        issues.extend(_structural_checks(path, text))
    return issues


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="根目录 shell / command 脚本静态检查")
    parser.parse_args(argv)
    issues = check()
    if issues:
        print("Shell 脚本静态检查失败：", file=sys.stderr)
        for issue in issues:
            print(f"- {issue}", file=sys.stderr)
        return 1
    print("Shell 脚本静态检查通过（语法 / 失败保护 / venv 守卫 / 版本断言 / extras 语义）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
