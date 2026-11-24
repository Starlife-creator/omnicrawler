"""`--json` 位置无关对 `pdf` 转发路径同样成立。

为什么要有这个文件：`pdf` 是一条**绕过 `_handlers` 注册表**的转发路径。
实测（2026-11-24）：

```
omnicrawler   --json pdf --help   ->  rc=1, ok=false   ← 走了普通子命令路径
omnicrawler   pdf --json --help   ->  rc=0, ok=true    ← 走了转发路径
```

同一件事两种结果。根因是分发判断写成 `argv[0] in {pdf, ...}`，
前置 `--json` 时匹配失败，`pdf` 落到 argparse 处理。

★ 关于**退出码**：`pdf --help` 在缺插件时**必须非零** —— 帮助请求的「结果」
是看到子命令列表，而缺插件时并没有看到。这一点由
`test_pdf_cli_user_surface.py::test_pdf_help_without_plugin_is_a_clean_message`
钉住（本文件不重复断言，只断言「两种写法结果相同」）。
"""

from __future__ import annotations

import json
import subprocess
import sys

import pytest

from tools.child_env import utf8_env


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "omnicrawler", *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=120,
        env=utf8_env(),
    )


@pytest.mark.parametrize(
    "args",
    [
        ("--json", "pdf", "--help"),
        ("pdf", "--json", "--help"),
    ],
)
def test_json_flag_position_does_not_change_the_result(args: tuple[str, ...]) -> None:
    """两种写法必须给出**相同**的退出码与 envelope。"""
    completed = _run(*args)
    payload = json.loads((completed.stdout or "").strip().splitlines()[-1])
    # 缺插件 ⇒ 两种情况都应失败，且都应是结构化 envelope（不是空 stdout）
    assert payload["exit_code"] == completed.returncode
    assert payload["ok"] is (completed.returncode == 0)


def test_both_positions_agree_with_each_other() -> None:
    """直接比两次运行的结果，而不是各自对常量断言。"""
    first = _run("--json", "pdf", "--help")
    second = _run("pdf", "--json", "--help")
    assert first.returncode == second.returncode, (
        f"--json 位置改变了退出码：{first.returncode} vs {second.returncode}"
    )
    left = json.loads((first.stdout or "").strip().splitlines()[-1])
    right = json.loads((second.stdout or "").strip().splitlines()[-1])
    assert left["ok"] == right["ok"]
    assert left["exit_code"] == right["exit_code"]


def test_pdf_execution_still_exits_nonzero_without_plugin() -> None:
    """真正执行仍要报失败 —— 别把「帮助」和「执行」混为一谈。"""
    completed = _run("pdf", "doctor")
    assert completed.returncode != 0


def test_pdf_help_without_plugin_is_not_ok() -> None:
    """缺插件时帮助请求未被满足 ⇒ envelope 必须 ok=false。

    （我曾把它改成 ok=true / 退出 0，被既有测试以「脚本会误以为成功」驳回。）
    """
    completed = _run("--json", "pdf", "--help")
    payload = json.loads((completed.stdout or "").strip().splitlines()[-1])
    assert payload["ok"] is False


def test_plugin_missing_is_marked_not_retryable() -> None:
    """插件未安装是配置问题：原样重试永远不会成功。"""
    completed = _run("--json", "pdf", "doctor")
    payload = json.loads((completed.stdout or "").strip().splitlines()[-1])
    assert payload["error"]["is_retryable"] is False
