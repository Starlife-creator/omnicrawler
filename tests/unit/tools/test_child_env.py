"""`tools/child_env.py` 的反向断言：修法本身必须可证伪。

这里钉三件事：①在父环境上**叠加**而不是替换（丢了 PATH 门禁会以另一种方式坏掉）；
②不污染当前进程的 `os.environ`；③真能把子进程按 UTF-8 写这件事变成现实。
"""

from __future__ import annotations

import os
import subprocess
import sys

import pytest

from tools.child_env import UTF8_SETTINGS, utf8_env


def test_settings_are_the_two_documented_variables() -> None:
    """两个变量缺一不可：前者让子解释器进 UTF-8 模式，后者兜住只认它的旧行为。"""
    assert set(UTF8_SETTINGS) == {"PYTHONUTF8", "PYTHONIOENCODING"}
    assert UTF8_SETTINGS["PYTHONUTF8"] == "1"
    assert UTF8_SETTINGS["PYTHONIOENCODING"] == "utf-8"


def test_parent_environment_is_merged_not_replaced(monkeypatch: pytest.MonkeyPatch) -> None:
    """**反向断言**：整份替换会丢掉 PATH 等，让子进程以另一种方式坏掉。"""
    monkeypatch.setenv("PATH", r"C:\stub\bin")
    monkeypatch.setenv("OMNICRAWLER_KEEP_ME", "kept")

    env = utf8_env()

    assert env["PATH"] == r"C:\stub\bin"
    assert env["OMNICRAWLER_KEEP_ME"] == "kept"
    assert env["PYTHONUTF8"] == "1"
    assert env["PYTHONIOENCODING"] == "utf-8"


def test_os_environ_is_not_mutated(monkeypatch: pytest.MonkeyPatch) -> None:
    """返回副本：改它不得反过来污染当前进程。"""
    monkeypatch.delenv("PYTHONUTF8", raising=False)
    env = utf8_env()
    env["PYTHONUTF8"] = "tampered"
    assert os.environ.get("PYTHONUTF8") != "tampered"


def test_overrides_win_over_the_defaults() -> None:
    """显式 overrides 最后叠加 —— 测试要靠它模拟「注入失效」。"""
    env = utf8_env({"PYTHONIOENCODING": "gbk"})
    assert env["PYTHONIOENCODING"] == "gbk"
    assert env["PYTHONUTF8"] == "1"


def test_child_really_reports_utf8_stdout() -> None:
    """正面证据：带上它，子进程自己报告的 stdout 编码必须是 UTF-8。"""
    done = subprocess.run(
        [sys.executable, "-c", "import sys; print(sys.stdout.encoding)"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=utf8_env(),
    )
    reported = done.stdout.strip().lower().replace("-", "")
    assert reported == "utf8", f"子进程报告 {done.stdout.strip()!r}"


@pytest.mark.skipif(sys.platform != "win32", reason="非 Windows 的 Python 默认就是 UTF-8 模式")
def test_without_it_windows_child_falls_back_to_the_code_page() -> None:
    """**反向断言（真机）**：摘掉注入，Windows 子进程回落到控制台代码页 —— 缺陷成因。"""
    stripped = dict(os.environ)
    stripped.pop("PYTHONUTF8", None)
    stripped.pop("PYTHONIOENCODING", None)
    done = subprocess.run(
        [sys.executable, "-c", "import sys; print(sys.stdout.encoding)"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=stripped,
    )
    reported = done.stdout.strip().lower().replace("-", "")
    assert reported != "utf8", f"去掉注入后仍报告 {done.stdout.strip()!r} ⇒ 修复不承重"
