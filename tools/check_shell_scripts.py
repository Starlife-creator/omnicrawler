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

**环境能力缺失时以 :data:`tools.gate_registry.SKIP_EXIT_CODE` 退出**（不是失败）：
Git for Windows 的 bash 在被禁命名管道的沙箱里连启动都做不到
（`fatal error - couldn't create signal pipe, Win32 error 5`），于是 `bash -n` 对**每个**
脚本都返回非 0。若不区分，9 个脚本会被同时写成「语法错误」——代码明明没坏，
红的是环境。这类假红会让人开始怀疑一个没坏的仓库，所以这里先探一次「bash 能不能用」，
不能用就跳过语法检查、只跑结构检查，并在末行写明缺的是哪种能力。
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

if __package__ in (None, ""):  # 允许 `python tools/check_shell_scripts.py` 直接执行
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.gate_registry import SKIP_EXIT_CODE  # noqa: E402

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


def _shell_scripts(root: Path = PROJECT_ROOT) -> list[Path]:
    return sorted({*root.glob("*.sh"), *root.glob("*.command")})


def _bash_executable() -> str:
    """解析可用的 bash 绝对路径。

    Windows 的 CreateProcess 先搜 System32 再搜 PATH：裸 ``bash`` 会命中未安装的
    WSL 垫片（rc=1，报错还是 UTF-16LE），把每个脚本都误判成语法错误。
    ``shutil.which`` 只查 PATH，但本机 PATH 里可能只有 ``Git\\cmd``（含 git.exe、
    不含 bash.exe），且 System32 排在它前面，导致 ``shutil.which("bash")`` 命中
    WSL 垫片而非 Git for Windows 的 bash。因此这里在 Windows 上按优先级探测真 bash：

    1. ``shutil.which("bash")`` 命中且不在 System32/WSL 垫片位置时直接采用；
    2. 从 ``git.exe`` 反推同根 ``bin/bash.exe`` 与 ``usr/bin/bash.exe``；
    3. 常见安装路径 ``Program Files\\Git\\bin\\bash.exe`` 与 ``usr\\bin\\bash.exe``。
    """
    def _is_wsl_shim(exe: str) -> bool:
        lowered = exe.lower()
        return "system32" in lowered or "windowsapps" in lowered

    found = shutil.which("bash")
    if found and not _is_wsl_shim(found):
        return found

    if sys.platform == "win32":
        git = shutil.which("git")
        if git:
            git_root = Path(git).resolve().parents[1]
            for rel in ("bin/bash.exe", "usr/bin/bash.exe"):
                candidate = git_root / rel
                if candidate.is_file():
                    return str(candidate)
        for base in (
            Path("C:/Program Files/Git"),
            Path("C:/Program Files (x86)/Git"),
            Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Git",
        ):
            for rel in ("bin/bash.exe", "usr/bin/bash.exe"):
                candidate = base / rel
                if candidate.is_file():
                    return str(candidate)
    return found or "bash"


def _bash_capability(exe: str) -> str | None:
    """探测 ``bash`` 在本进程环境里能否真正跑起来；返回不可用原因，可用则返回 ``None``。

    这一层是**为假红兜底**：Git for Windows 的 bash 在被禁命名管道的沙箱里连启动都做不到，
    ``bash -n`` 会对**每个**脚本返回非 0，若不先探能力，9 个没坏的脚本会被同时写成
    「语法错误」。所以先跑一次无副作用的 ``bash -c "exit 0"``：能退 0 才算可用。
    """
    try:
        proc = subprocess.run(  # noqa: S603 - 命令为固定 bash + 常量表达式
            [exe, "-c", "exit 0"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except OSError as exc:
        return f"无法启动 bash（{exe}）：{exc}"
    if proc.returncode == 0:
        return None
    detail = ((proc.stderr or "") + (proc.stdout or "")).strip().replace("\n", " | ")
    return f"bash 在此环境不可用（{exe}）：退出码 {proc.returncode}；{detail or '无输出'}"


def _syntax_check(path: Path) -> list[str]:
    proc = subprocess.run(  # noqa: S603 - 命令为固定 bash + 本地脚本路径
        [_bash_executable(), "-n", str(path)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if proc.returncode != 0:
        detail = ((proc.stderr or "") + (proc.stdout or "")).strip().replace("\n", " | ")
        return [f"{path.name}: bash -n 语法错误：{detail}"]
    return []


def bash_unavailable_reason() -> str | None:
    """对外暴露「本机 bash 能否用于语法检查」，供测试与自证入口复用。"""
    return _bash_capability(_bash_executable())


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


def check(root: Path = PROJECT_ROOT, *, syntax_check: bool = True) -> list[str]:
    """返回判红项。``syntax_check=False`` 时只跑结构检查（bash 不可用时的降级路径）。"""
    issues: list[str] = []
    for path in _shell_scripts(root):
        text = path.read_text(encoding="utf-8")
        if syntax_check:
            issues.extend(_syntax_check(path))
        issues.extend(_structural_checks(path, text))
    return issues


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="根目录 shell / command 脚本静态检查")
    parser.parse_args(argv)

    # 先探 bash 能力：不可用就只跑结构检查，并以**跳过码**退出（末行写原因）。
    # 这不是放水——结构判据（pipefail / venv 守卫 / 版本断言 / extras 语义）照旧判红；
    # 只是不把「环境建不出管道」记成「脚本语法坏了」。
    unavailable = bash_unavailable_reason()
    issues = check(syntax_check=unavailable is None)
    if issues:
        print("Shell 脚本静态检查失败：", file=sys.stderr)
        for issue in issues:
            print(f"- {issue}", file=sys.stderr)
        return 1

    if unavailable is not None:
        print("Shell 脚本结构检查通过，但语法检查被跳过：", file=sys.stderr)
        print(f"- 已检查 {len(_shell_scripts())} 个脚本的失败保护 / venv 守卫 / 版本断言 / extras 语义", file=sys.stderr)
        # 跳过码 + 末行原因：self_verify 取最后一行作为跳过理由。
        print(f"bash 语法检查无法执行：{unavailable}", file=sys.stderr)
        return SKIP_EXIT_CODE

    print("Shell 脚本静态检查通过（语法 / 失败保护 / venv 守卫 / 版本断言 / extras 语义）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
