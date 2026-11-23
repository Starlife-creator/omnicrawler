"""`--json` 输出的守卫：**stdout 必须是纯净可解析的 JSON**。

## 为什么这个测试存在（2026-11-23 调研结论）

clig.dev 的两条硬要求：

> **Send output to `stdout`.** Anything that is machine readable should also go to `stdout`.
> **Send messaging to `stderr`.** Log messages, errors, and so on should all be sent to `stderr`.

实测本项目此前 **55 个命令只有 3 个支持 `--json`**。现在加了全局 `--json`
与统一 envelope，但随之而来的风险是：**日志混进 stdout**，那会让
`json.loads()` 直接失败 —— 比没有 JSON 更糟（agent 会误判为崩溃）。

本测试固定三件事：

1. stdout **只有一行、且是合法 JSON**（日志、进度条都不许混进来）；
2. envelope 有 `ok` / `command` / `exit_code` 三个必备字段；
3. 失败时 `error` 带 `suggestion` 与 **`is_retryable`** ——
   agent 靠后者决定「原样重试 / 改输入 / 放弃」。

## 边界

不验 `output` 字段的内容结构（那是各 handler 逐步升级的事）。
"""
from __future__ import annotations

import json
import subprocess
import sys

import pytest

from omnicrawler.cli import _json_envelope as env
from tools.child_env import utf8_env


def test_build_payload_success_shape() -> None:
    """成功 envelope 的字段齐全，`error` 为 None。"""
    payload = env.build_payload("status", ok=True, output="ok", exit_code=0)
    assert payload["ok"] is True
    assert payload["command"] == "status"
    assert payload["exit_code"] == 0
    assert payload["error"] is None


def test_build_payload_failure_has_three_pieces() -> None:
    """失败时必须给 message / suggestion / is_retryable 三件套。"""
    exc = FileNotFoundError(2, "No such file", "task.yaml")
    payload = env.build_payload("run", ok=False, output="", exit_code=1, exc=exc)
    err = payload["error"]
    assert err["message"], "没有 message"
    assert err["suggestion"], "没有 suggestion —— agent 无从下手"
    assert isinstance(err["is_retryable"], bool)


@pytest.mark.parametrize(
    ("exc", "retryable"),
    [
        (ValueError("配置缺少 seeds"), False),
        (KeyError("template"), False),
        (FileNotFoundError(2, "x", "a.yaml"), False),
        (ConnectionError("网络中断"), True),
        (TimeoutError("超时"), True),
    ],
)
def test_is_retryable_matches_error_nature(exc: Exception, retryable: bool) -> None:
    """**核心语义**：输入类错误不可重试，网络/超时类可重试。

    这条错了会让 agent 要么徒劳重试、要么该重试时放弃。
    """
    payload = env.build_payload("cmd", ok=False, output="", exit_code=1, exc=exc)
    assert payload["error"]["is_retryable"] is retryable


def test_permission_error_suggestion_names_the_path() -> None:
    """文件权限错误的建议必须指出路径（与 `_print_error_hint` 同源原则）。"""
    path = r"C:\data\.registry.lock"
    payload = env.build_payload(
        "components", ok=False, output="", exit_code=1,
        exc=PermissionError(13, "denied", path),
    )
    assert path in payload["error"]["suggestion"]


def test_cli_json_stdout_is_pure_json() -> None:
    """端到端：`--json` 时 stdout 必须只有一行合法 JSON。

    这是最容易回退的地方 —— 任何 `print()` 混进 stdout 都会破坏它。
    """
    completed = subprocess.run(
        [sys.executable, "-m", "omnicrawler", "--json", "status", "-c", "__no_such__.yaml"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        check=False, timeout=120, env=utf8_env(),
    )
    stdout = (completed.stdout or "").strip()
    assert stdout, "stdout 为空 —— envelope 没输出"
    lines = stdout.splitlines()
    assert len(lines) == 1, f"stdout 有多行（日志混进来了？）：{lines[:3]}"
    payload = json.loads(lines[0])  # 不合法会抛
    assert set(payload) >= {"ok", "command", "exit_code", "error"}


def test_cli_json_reports_retryable_false_for_missing_file() -> None:
    """端到端：缺配置文件应报 `is_retryable=false`（改输入才能解决）。"""
    completed = subprocess.run(
        [sys.executable, "-m", "omnicrawler", "--json", "status", "-c", "__no_such__.yaml"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        check=False, timeout=120, env=utf8_env(),
    )
    payload = json.loads((completed.stdout or "").strip().splitlines()[0])
    assert payload["ok"] is False
    assert payload["error"]["is_retryable"] is False
    assert payload["error"]["suggestion"]

def test_command_own_json_is_passed_through_not_nested() -> None:
    """**穿透**：handler 自己输出 JSON 时不许再套一层 envelope。

    早先 `capabilities` 的整段 JSON 被塞进 `output` 字符串里，
    agent 得多解析一层。根因是 `SystemExit(0)` 分支没走透传判断。
    """
    completed = subprocess.run(
        [sys.executable, "-m", "omnicrawler", "--json", "capabilities"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        check=False, timeout=180, env=utf8_env(),
    )
    # 透传的是**多行** JSON（原样），所以整体解析而不是取首行。
    payload = json.loads((completed.stdout or "").strip())
    assert "command" not in payload, (
        f"被套了 envelope（应透传命令自己的 JSON）：{sorted(payload)[:6]}"
    )
    assert "ok" in payload, "命令自己的 JSON 里应有 ok 字段"


def test_argparse_error_also_emits_json() -> None:
    """**参数错误也要是 JSON**：这是最常见的失败，agent 同样需要结构化输出。

    此前只打 usage 文本，`--json` 模式下 `json.loads` 直接失败。
    """
    completed = subprocess.run(
        [sys.executable, "-m", "omnicrawler", "--json", "run"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        check=False, timeout=120, env=utf8_env(),
    )
    assert completed.returncode == 2, f"参数错误应以 2 退出，实际 {completed.returncode}"
    stdout = (completed.stdout or "").strip()
    assert stdout, "--json 模式下参数错误没有输出 JSON"
    payload = json.loads(stdout.splitlines()[0])
    assert payload["ok"] is False
    assert payload["exit_code"] == 2
    assert payload["error"]["type"] == "UsageError"
    assert payload["error"]["is_retryable"] is False
    # usage 文本仍要在 stderr（保持人读行为不变）
    assert "usage" in (completed.stderr or "").lower()

def test_process_exit_code_matches_envelope() -> None:
    """**核心契约**：进程退出码必须等于 envelope 里的 `exit_code`。

    早先 `_dispatch` 丢弃了 `run_with_envelope` 的返回值，于是真实失败
    （envelope 说 exit_code=1）进程却以 0 退出 —— agent 会当成功，
    脚本 `&&` 链也不中断。这是最危险的一类不一致。
    """
    completed = subprocess.run(
        [sys.executable, "-m", "omnicrawler", "--json", "status", "-c", "__nope__.yaml"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        check=False, timeout=120, env=utf8_env(),
    )
    payload = json.loads((completed.stdout or "").strip().splitlines()[0])
    assert payload["ok"] is False
    assert completed.returncode == payload["exit_code"], (
        f"进程退出码 {completed.returncode} 与 envelope 的 "
        f"{payload['exit_code']} 不一致 —— agent 会误判成败"
    )

@pytest.mark.parametrize(
    ("message", "must_contain"),
    [
        ("the following arguments are required: --output/-o", "--output/-o"),
        ("unrecognized arguments: --badflag x", "--badflag"),
        ("argument --mode: invalid choice: 'wrong'", "允许范围"),
    ],
)
def test_usage_suggestion_names_the_specific_problem(message: str, must_contain: str) -> None:
    """**建议必须点名具体问题**，不能只说「检查参数拼写」。

    argparse 的 message 里已经有参数名了。把它抽出来写进 suggestion，
    agent 就能直接照做，不必再去读一遍 `--help`。
    """
    from omnicrawler.cli._main import _usage_suggestion

    suggestion = _usage_suggestion(message)
    assert must_contain in suggestion, (
        f"建议没有点名具体问题（缺 {must_contain!r}）：{suggestion!r}"
    )


def test_usage_suggestion_falls_back_when_unparseable() -> None:
    """无法识别的文案要给通用兜底，不能返回空串。"""
    from omnicrawler.cli._main import _usage_suggestion

    assert _usage_suggestion("某种没见过的错误")
