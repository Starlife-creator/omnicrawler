"""component_worker._guarded_main：stdout 必须有 JSON 的承诺。

为什么要有这个文件：worker 的父进程靠 stdout 上的**一行 JSON** 判断结果。
该函数自称「保证 stdout 一定有 JSON」，但此前对 `SystemExit` 直接 `raise`：

    except SystemExit:
        raise          # ← stdout 完全为空

于是 worker 内部任何一次主动退出（参数校验失败、前置检查不通过…）都不产出 JSON。
真实 CI 见到的形态正是这个：「已校验文件 14819 个」「退出码 1」而 stdout 没有任何 JSON，
父进程无从判断是哪一步失败的（那一轮为此排查了很久）。

本文件钉住「异常 / 主动退出 / 正常返回」三条路径的可观测性契约。
"""

from __future__ import annotations

import json
import sys

import pytest

from omnicrawler.services import component_worker as cw


def _guarded(monkeypatch: pytest.MonkeyPatch, fake, capsys: pytest.CaptureFixture[str]):
    """换掉 main，跑一次兜底；返回 (退出码, 解析出的 JSON 或 None)。"""
    monkeypatch.setattr(cw, "main", fake)
    code = cw._guarded_main()
    out = capsys.readouterr().out.strip()
    payload = None
    if out:
        try:
            payload = json.loads(out)
        except json.JSONDecodeError:
            payload = None
    return code, payload


def test_worker_internal_exit_still_emits_json(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    """worker 自己 sys.exit(3) ⇒ 仍必须有 JSON，且退出码保留 3。"""
    def _exit() -> int:
        sys.exit(3)

    code, payload = _guarded(monkeypatch, _exit, capsys)
    assert payload is not None, "主动退出也必须产出 JSON —— 否则父进程什么也看不到"
    assert payload["ok"] is False
    assert payload["stage"] == "worker_exit"
    assert code == 3, "退出码语义不应被兜底改变"


def test_worker_exit_with_a_message_maps_to_code_one(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    """sys.exit("消息") 没有整数码 ⇒ 按 1，且消息要留在 JSON 里。"""
    def _exit() -> int:
        sys.exit("config missing")

    code, payload = _guarded(monkeypatch, _exit, capsys)
    assert payload is not None
    assert "config missing" in payload["error"]
    assert code == 1


def test_unexpected_exception_emits_json_with_traceback(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    """未预期异常 ⇒ JSON 带 traceback，便于定位。"""
    def _boom() -> int:
        raise ValueError("bad payload")

    code, payload = _guarded(monkeypatch, _boom, capsys)
    assert payload is not None
    assert payload["stage"] == "worker_bootstrap"
    assert "ValueError" in payload["error"]
    assert payload["traceback"], "traceback 是定位的关键，不能为空"
    assert code == 1


def test_successful_run_emits_nothing_extra(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    """正常返回时兜底**不得**额外打印 —— 协议是 stdout 只有一行 JSON（由 main 自己写）。"""
    def _ok() -> int:
        print(json.dumps({"ok": True}))
        return 0

    code, payload = _guarded(monkeypatch, _ok, capsys)
    assert code == 0
    assert payload == {"ok": True}, "兜底不该在成功路径再写一行"


def test_worker_json_error_and_stderr_are_both_reported() -> None:
    """父进程的报错必须同时给出「哪一步」与「为什么」。

    这是本轮改动引入后又修掉的一个回归：主动退出开始产出 JSON 之后，
    `_extract_worker_error` 优先采用了 JSON 的 `error`（`SystemExit: 3`），
    于是组件自己写到 stderr 的原因被吞 —— 原本能看到的 `boom` 消失了。
    用「哪一步」替换「为什么」不是改进，是信息量净损失。
    """
    from omnicrawler.services.component_runner import _extract_worker_error, _meaningful_stderr

    stdout = json.dumps({"ok": False, "stage": "worker_exit", "error": "SystemExit: 3"})
    stderr = "boom"

    reported = _extract_worker_error(stdout)
    assert "worker_exit" in reported
    assert _meaningful_stderr(stderr) == "boom"


def test_startup_noise_is_filtered_out_of_stderr() -> None:
    """启动期「某可选件缺失」的告警不得挤掉真正的原因（2026-11-20 CI 踩过）。"""
    from omnicrawler.services.component_runner import _meaningful_stderr

    stderr = """内置 Tesseract 缺失: /x/tesseract\n内置 ChromeDriver 缺失\n真正的错误：ImportError: numpy"""
    assert _meaningful_stderr(stderr) == "真正的错误：ImportError: numpy"


def test_stderr_tail_is_bounded_to_ten_lines() -> None:
    """只取末 10 行：崩溃栈够用，又不至于淹没报告。"""
    from omnicrawler.services.component_runner import _meaningful_stderr

    stderr = "\n".join(f"line {i}" for i in range(30))
    lines = _meaningful_stderr(stderr).splitlines()
    assert len(lines) == 10
    assert lines[-1] == "line 29"
