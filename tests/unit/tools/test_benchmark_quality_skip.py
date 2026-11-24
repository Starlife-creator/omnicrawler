"""`tools/benchmarks/benchmark_quality.py` 的环境跳过契约。

为什么要有这个文件：同族的 `benchmark_quality.py` 此前没有任何测试，于是
「临时目录建不出来」与「抽取不满足交付契约」混成同一个红 —— 读报告的人无从
区分是环境坏了还是代码坏了。实测（2026-11-24）本机沙箱里 `TEMP` 不可写，
该门禁每次都以 `PermissionError` 崩溃，看起来像是基准本身坏了。

本文件只钉入口契约，不跑真实任务：

* 工作目录不可写 ⇒ 以 `SKIP_EXIT_CODE` 退出，stdout **不留半张表**
* 末行必须写明跳过原因（`self_verify` 取最后一行当理由）
* 工作目录可写 ⇒ 不得返回跳过码（否则「跳过」会变成永远的后门）
* 探测必须**真写一次**：只看 `mkdir` 会漏掉「能建目录、不能写文件」
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

from tools.gate_registry import SKIP_EXIT_CODE

_REPO_ROOT = Path(__file__).resolve().parents[3]
_TOOL_PATH = _REPO_ROOT / "tools" / "benchmarks" / "benchmark_quality.py"


def _load_tool() -> ModuleType:
    spec = importlib.util.spec_from_file_location("_bench_quality_tool", _TOOL_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_unwritable_workdir_skips_with_reason(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """环境缺能力时用跳过码，并让末行成为可读理由。"""
    tool = _load_tool()

    def _deny(self: Path, *args: object, **kwargs: object) -> None:
        raise PermissionError(13, "拒绝访问。", str(self))

    monkeypatch.setattr(Path, "mkdir", _deny, raising=False)
    code = tool.main(["--workdir", str(tmp_path / "not-creatable")])

    assert code == SKIP_EXIT_CODE, "临时目录不可写必须走跳过码，不能混成失败"
    captured = capsys.readouterr()
    assert captured.out.strip() == "", "跳过时 stdout 不应留下半张表"
    last_line = captured.err.strip().splitlines()[-1]
    assert "临时目录不可写" in last_line, f"末行必须写清跳过原因，实际为：{last_line!r}"


def test_writable_workdir_is_not_reported_as_skip(tmp_path: Path) -> None:
    """能力具备时不得判为不可用 —— 否则「跳过」会变成永远的后门。"""
    tool = _load_tool()
    probe = tool._probe_workdir(str(tmp_path / "bench"))
    assert probe.error is None, f"可写目录被误判为不可用：{probe.error}"
    assert probe.target.is_dir()


def test_probe_does_a_real_write(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """探测必须真写一次：只看 mkdir 会漏掉「能建目录、不能写文件」。"""
    tool = _load_tool()

    def _deny_write(self: Path, *args: object, **kwargs: object) -> int:
        raise PermissionError(13, "拒绝访问。", str(self))

    monkeypatch.setattr(Path, "write_text", _deny_write, raising=False)
    probe = tool._probe_workdir(str(tmp_path / "bench2"))
    assert probe.error is not None, "能建目录但写不进文件时，必须判为不可用"
    assert "PermissionError" in probe.error
