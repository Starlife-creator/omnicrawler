"""插件未安装时，PDF 相关的**用户可见输出**必须干净。

## 这条守卫防的是什么

2026-11-17 实测：用户跑 `omnicrawler pdf --help`（只是想看看有什么功能），
看到的是一段 Python traceback：

```
Traceback (most recent call last):
  ...
RuntimeError: 文档工作台插件未安装，无法执行该操作（触发操作：pdf.cli）。
```

原因是 `cli/_main.py:138` 的**转发路径**直接调 `_dispatch_pdf`，
绕过 `_handlers` 注册表，异常没人接。

同时 `cli/_parsers/pdf.py` 的 help 文案还写着「等价 pdfx 命令」——
而 `pdfx` 这个 console script 已随迁移移除。

## 判据

1. `omnicrawler pdf --help`（未装插件）→ 退码非 0，但**不得出现 Traceback**；
   消息里要点名 `document-workbench` 并给出下一步怎么做；
2. `omnicrawler --help` 的 pdf 条目**不得再提 `pdfx`**（那个命令不存在了）。
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[3]


def _run_cli(args: list[str], tmp_path: Path) -> tuple[int, str]:
    """跑一次 `omnicrawler` 并返回（退出码, 合并输出）。

    本沙箱禁命名管道 ⇒ 不用 capture_output，重定向到文件。
    """
    log = tmp_path / "cli.log"
    env = {
        **os.environ,
        "PYTHONPATH": str(_REPO / "src"),
        "PYTHONUTF8": "1",
        "PYTHONIOENCODING": "utf-8",
        "LOCALAPPDATA": str(tmp_path / "localappdata"),
        # 明确"插件未安装"：把显式路径指到不存在的目录
        "OMNICRAWL_PDF_PLUGIN_DIR": str(tmp_path / "no-such-plugin"),
    }
    env.pop("OMNICRAWL_PLUGIN_SIGNATURE_POLICY", None)
    (tmp_path / "localappdata").mkdir(exist_ok=True)

    with log.open("w", encoding="utf-8") as handle:
        completed = subprocess.run(
            [sys.executable, "-m", "omnicrawler", *args],
            stdout=handle,
            stderr=subprocess.STDOUT,
            cwd=str(_REPO),
            env=env,
            timeout=180,
            check=False,
        )
    return completed.returncode, log.read_text(encoding="utf-8", errors="replace")


def test_pdf_help_without_plugin_is_a_clean_message(tmp_path: Path) -> None:
    """未装插件时 `pdf --help` 必须是干净提示，不是 traceback。"""
    code, output = _run_cli(["pdf", "--help"], tmp_path)

    assert code != 0, "未装插件却退码 0 —— 脚本会误以为成功"
    assert "Traceback" not in output, (
        f"用户看到的是 Python 栈，不是提示：\n{output[-800:]}"
    )
    assert "document-workbench" in output, (
        f"提示没点名要装哪个插件：\n{output[-500:]}"
    )
    # 给出下一步怎么做
    assert "插件市场" in output or "安装" in output, (
        f"提示没告诉用户怎么办：\n{output[-500:]}"
    )


def test_top_level_help_does_not_mention_removed_pdfx(tmp_path: Path) -> None:
    """`omnicrawler --help` 的 pdf 条目不得再提已移除的 `pdfx` 命令。"""
    code, output = _run_cli(["--help"], tmp_path)
    assert code == 0, f"--help 失败：\n{output[-500:]}"

    pdf_lines = [line for line in output.splitlines() if "PDF 子系统" in line]
    assert pdf_lines, "没找到 pdf 的 help 行——解析可能失效"
    for line in pdf_lines:
        assert "pdfx" not in line, (
            f"help 仍在提已移除的 `pdfx` 命令：{line.strip()}"
        )


@pytest.mark.skipif(
    not (_REPO.parent / "OmniCrawler-market").is_dir(),
    reason="需要 market 仓（插件已装场景）",
)
def test_pdf_help_works_when_plugin_installed(tmp_path: Path) -> None:
    """装了插件后 `pdf --help` 必须正常输出子命令列表。"""
    plugin_dir = (
        _REPO.parent
        / "OmniCrawler-market"
        / "submissions"
        / "plugins"
        / "4c3014804d85de2568f85e62a3048429"
        / "document-workbench"
    )
    if not plugin_dir.is_dir():
        pytest.skip(f"找不到插件目录：{plugin_dir}")

    log = tmp_path / "cli.log"
    env = {
        **os.environ,
        "PYTHONPATH": str(_REPO / "src"),
        "PYTHONUTF8": "1",
        "PYTHONIOENCODING": "utf-8",
        "LOCALAPPDATA": str(tmp_path / "localappdata"),
        "OMNICRAWL_PLUGIN_SIGNATURE_POLICY": "developer",
        "OMNICRAWL_PDF_PLUGIN_DIR": str(plugin_dir),
    }
    (tmp_path / "localappdata").mkdir(exist_ok=True)

    with log.open("w", encoding="utf-8") as handle:
        completed = subprocess.run(
            [sys.executable, "-m", "omnicrawler", "pdf", "--help"],
            stdout=handle,
            stderr=subprocess.STDOUT,
            cwd=str(_REPO),
            env=env,
            timeout=180,
            check=False,
        )
    output = log.read_text(encoding="utf-8", errors="replace")

    assert completed.returncode == 0, f"装了插件却失败：\n{output[-800:]}"
    assert "Traceback" not in output
    # 子命令列表应当出现（pdfx 的 CLI 有这些）
    assert "extract" in output or "doctor" in output, (
        f"没看到子命令列表：\n{output[-600:]}"
    )
