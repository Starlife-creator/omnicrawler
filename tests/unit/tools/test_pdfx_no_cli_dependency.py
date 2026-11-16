"""条目 7：解开 `pdfx → cli` 循环，让 pdfx 可独立组件化。

**为什么这是 pdfx 组件化的硬障碍**

`pdfx` 作为独立组件时，宿主只应给它 PNG 输入、收文本与结构化结果；
它不该依赖宿主的命令行入口。实测原先唯一的反向依赖是：

    src/omnicrawler/pdfx/__main__.py:1    from ..cli import main

这行让 `python -m omnicrawler.pdfx` 能转发到主 CLI——**是个便利入口**，
却把整个 CLI 拉进了 pdfx 的依赖闭包。一旦 pdfx 被打成组件、
而组件里没有 CLI，这个 import 就会炸。

**判据**

1. `pdfx` 的**任何模块**都不得在**模块顶层**导入 `omnicrawler.cli`
   （函数内的惰性导入可以，因为那时才真的需要转发）；
2. `pdfx` 必须能在**不导入 CLI** 的前提下完成核心功能导入
   （`pdfx.config` / `pdfx.database` / `pdfx.parser` / `pdfx.ocr`）；
3. 便利入口的行为必须保留：`python -m omnicrawler.pdfx --help` 仍应能工作。

第 3 条是**防止我为了解环而删掉功能**——解环不是砍功能。
"""

from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
_PDFX = _REPO_ROOT / "src" / "omnicrawler" / "pdfx"

#: pdfx 的核心模块：组件化后这些必须在没有 CLI 的环境里可导入。
_CORE_MODULES = ("config", "database", "parser", "ocr", "ocr_result", "exporter")


def test_pdfx_does_not_import_cli_at_module_level() -> None:
    """pdfx 任何模块都不得在**模块顶层**导入 CLI。"""
    offenders: list[str] = []
    for path in sorted(_PDFX.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in tree.body:  # 只看模块顶层；函数内惰性导入允许
            modules: list[str] = []
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules = [node.module]
            for module in modules:
                if module == "cli" or module.startswith("cli.") or module.startswith("..cli"):
                    offenders.append(f"{path.relative_to(_REPO_ROOT).as_posix()}:{node.lineno}")
    assert offenders == [], (
        "以下位置在模块顶层导入了宿主的 CLI：\n  " + "\n  ".join(offenders) +
        "\npdfx 组件化后没有 CLI 可用，这会让组件直接 ImportError。"
        "如需保留 `python -m omnicrawler.pdfx` 的转发，请改为函数内惰性导入。"
    )


def test_pdfx_core_imports_without_cli(tmp_path: Path) -> None:
    """核心模块必须能在**不加载 CLI** 的情况导入（用子进程隔离验证）。

    输出写文件而非 capture_output：本沙箱禁命名管道（同上）。
    """
    script = (
        "import sys\n"
        f"sys.path.insert(0, {str(_REPO_ROOT / 'src')!r})\n"
        "import importlib\n"
        f"for name in {list(_CORE_MODULES)!r}:\n"
        "    importlib.import_module('omnicrawler.pdfx.' + name)\n"
        "loaded = [m for m in sys.modules if m.startswith('omnicrawler.cli')]\n"
        "print('CLI_LOADED=' + repr(loaded))\n"
    )
    out = tmp_path / "probe.txt"
    with out.open("w", encoding="utf-8") as handle:
        result = subprocess.run(
            [sys.executable, "-c", script],
            stdout=handle, stderr=subprocess.STDOUT,
            timeout=120, check=False,
        )
    text = out.read_text(encoding="utf-8", errors="replace")
    assert result.returncode == 0, (
        f"导入 pdfx 核心模块失败：\n{text[-1500:]}"
    )
    marker = "CLI_LOADED="
    line = next((ln for ln in text.splitlines() if ln.startswith(marker)), "")
    loaded = line[len(marker):]
    assert loaded == "[]", (
        f"导入 pdfx 核心模块时把 CLI 一起加载了：{loaded}。"
        "组件化后宿主不会提供 CLI，这会成为隐性依赖。"
    )


def test_pdfx_module_entrypoint_still_works(tmp_path: Path) -> None:
    """解环不得砍功能：`python -m omnicrawler.pdfx` 仍必须可用。

    **行为契约**：这个入口历史上是"转发到主 CLI 的 pdf 子命令"，
    所以解环后它可能有两种合理形态——
      a) 仍然是转发（但改成**惰性**导入，不在模块顶层拉 CLI）；
      b) 直接调用 pdfx 自己的 `cli.main()`（更自洽）。
    两者都必须能打印出包含 pdf 子命令的帮助，否则说明功能被砍了。

    **为什么把输出写到文件而不是 capture_output**：本沙箱禁命名管道，
    `subprocess.run(capture_output=True)` 会因 reader thread 拿不到管道而崩
    （仓库既有门禁 `check_shell_scripts` 记录过同源限制）。
    """
    out = tmp_path / "help.txt"
    with out.open("w", encoding="utf-8") as handle:
        result = subprocess.run(
            [sys.executable, "-m", "omnicrawler.pdfx", "--help"],
            stdout=handle, stderr=subprocess.STDOUT,
            timeout=120, check=False,
            cwd=str(_REPO_ROOT),
            env={**os.environ, "PYTHONPATH": str(_REPO_ROOT / "src")},
        )
    combined = out.read_text(encoding="utf-8", errors="replace")
    assert result.returncode == 0, (
        f"`python -m omnicrawler.pdfx --help` 失败（退出码 {result.returncode}）：\n"
        f"{combined[-1500:]}"
    )
    # 历史行为是**转发到主 CLI**，所以 `--help` 打的是顶层帮助——
    # 判据是"帮助里仍然列着 pdf 这个子命令"，而不是"打出 pdf 子命令的帮助"。
    lowered = combined.lower()
    assert "pdf" in lowered, (
        "帮助里看不到 pdf 子命令，转发链路可能被砍：\n"
        f"{combined[:1200]}"
    )
