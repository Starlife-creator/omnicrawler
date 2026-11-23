"""`services/workbench.py` 的**入口层**守卫：参数解析与无 GUI 环境的降级。

## 为什么只测入口层（此前该模块 7.7% 覆盖）

`workbench.py` 335 行，`main()` 里几乎全部是 tkinter 事件回调与界面构建 ——
在无显示环境（CI）里跑不到，也不该用单测去驱动 GUI。

但**入口层**是纯逻辑且有真实回归史（见模块 docstring）：

> 2026-09-11：入口此前 `main()` 不收参数，于是 `omnicrawler-workbench --help`
> 之类会被**静默忽略**并直接开窗。

所以这里固定三件事：

1. `--help` **必须输出帮助并退出**，不能静默开窗；
2. `--version` 输出带版本号的文本；
3. **没有 tkinter 时要给出可读提示并返回 1**，而不是抛 `ImportError` 栈。

## 边界

不测界面。tkinter 缺失是这里唯一可确定性模拟的分支 ——
真实的窗口行为属 GUI job（`tests/gui/`），且需要显示环境。
"""
from __future__ import annotations

import subprocess
import sys

import pytest

from tools.child_env import utf8_env


def _run(args: list[str], timeout: int = 60) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "omnicrawler.services.workbench", *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        check=False, timeout=timeout, env=utf8_env(),
    )


def test_help_exits_zero_without_opening_window() -> None:
    """**核心守卫**：`--help` 必须打帮助并正常退出（0）。

    历史上 `main()` 不收参数，`--help` 被静默忽略、直接开窗 ——
    在 CI 里那会挂住或失败。这条用例固定住「帮助优先于开窗」。
    """
    completed = _run(["--help"])
    assert completed.returncode == 0, (
        f"--help 未正常退出（{completed.returncode}）——可能又静默开窗了"
    )
    assert "usage" in (completed.stdout or "").lower(), "没有输出用法说明"
    assert "omnicrawler-workbench" in (completed.stdout or ""), "帮助里没有程序名"


def test_version_prints_version_string() -> None:
    """`--version` 必须输出可读的版本行（脚本与安装器靠它判断）。"""
    completed = _run(["--version"])
    assert completed.returncode == 0
    text = (completed.stdout or "").strip()
    assert text, "--version 没有任何输出"
    # 形如 `omnicrawler 1.1.0`
    assert "omnicrawler" in text.lower(), f"版本行不含程序名：{text!r}"
    assert any(ch.isdigit() for ch in text), f"版本行不含数字：{text!r}"


def test_unknown_flag_is_rejected() -> None:
    """未知参数必须以 2 退出（argparse 约定），而不是被忽略后开窗。"""
    completed = _run(["--definitely-not-a-flag"])
    assert completed.returncode == 2, (
        f"未知参数未按约定以 2 退出（实际 {completed.returncode}）"
    )


def test_missing_tkinter_gives_readable_message(monkeypatch: pytest.MonkeyPatch) -> None:
    """**反向断言**：没有 tkinter 时给提示并返回 1，不抛 ImportError 栈。

    真实场景：精简版 Python（未装 Tk）或服务器环境。用户最需要的是
    「无法启动图形界面」这行提示，而不是一段 traceback。
    """
    from omnicrawler.services import workbench

    real_import = __builtins__["__import__"] if isinstance(__builtins__, dict) else __import__

    def _fake_import(name: str, *args: object, **kwargs: object):
        if name == "tkinter" or name.startswith("tkinter."):
            raise ImportError("No module named 'tkinter'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", _fake_import)
    code = workbench.main([])
    assert code == 1, f"缺 tkinter 时应返回 1，实际 {code}"
