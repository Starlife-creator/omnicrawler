"""`check_agent_surface_help` 门禁的**反向断言**：它必须真的能抓到缺失。

## 为什么给门禁本身写测试

`AGENTS.md` 的判据纪律写着「新守卫必须做反向断言；只绿不红等于没装护栏」。
这条同样适用于门禁脚本自己 —— 一个永远返回 0 的门禁看起来也是绿的。

2026-11-23 晚，`help` 覆盖率从 **68 处缺失降到 0**（全项目每个选项都有
`help`，基线锁定在 0）。这之后**门禁的价值完全取决于它能不能抓到新增**：
基线是 0，所以**任何**新加的无 help 选项都必须让它变红。

## 判据

1. 当前基线是 0，且实际缺失也是 0（防止有人把基线悄悄调回去）；
2. 给一个 parser 加无 help 选项 ⇒ `collect_missing_help()` 必须报出来；
3. `--update` 拒绝上调基线（只能降）。
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

import pytest

from tools.child_env import utf8_env
from tools.gates.check_agent_surface_help import collect_missing_help

_ROOT = Path(__file__).resolve().parents[3]


def test_project_has_zero_missing_help() -> None:
    """当前项目必须**零**缺失 —— 基线已锁 0，任何新增都会被下一条抓到。"""
    missing = collect_missing_help()
    assert not missing, (
        f"有 {len(missing)} 个选项缺 help：{missing[:10]}"
    )


def test_baseline_is_zero() -> None:
    """基线必须是 0。有人把它调高就等于悄悄降低了标准。"""
    baseline_path = _ROOT / "tools" / "agent_surface_baseline.json"
    assert baseline_path.is_file(), "基线文件不存在"
    value = json.loads(baseline_path.read_text(encoding="utf-8"))["max_missing_help"]
    assert value == 0, (
        f"基线被调高到 {value} —— 这个门禁只许降低，不许放宽"
    )


def test_gate_detects_a_newly_added_option_without_help(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """**核心反向断言**：往真实 parser 里塞一个无 help 选项 ⇒ 必须被报出。

    不修改任何源文件 —— 用 monkeypatch 包住 `build_parser`，在它返回后
    给顶层 parser 加一个选项。这样测的是「门禁的探测能力」，
    而不是「有没有改坏代码」。
    """
    from omnicrawler.cli import _main

    real_build = _main.build_parser

    def _build_with_extra() -> argparse.ArgumentParser:
        parser = real_build()
        parser.add_argument("--zzz-reverse-assert-probe")  # 故意不带 help
        return parser

    monkeypatch.setattr(_main, "build_parser", _build_with_extra)
    missing = collect_missing_help()
    flags = [flag for _cmd, flag in missing]
    assert "--zzz-reverse-assert-probe" in flags, (
        f"门禁没有报出新增的无 help 选项：{missing}"
    )


def test_update_refuses_to_raise_baseline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--update` 在「当前缺失 > 基线」时必须拒绝（只许降低）。"""
    from tools.gates import check_agent_surface_help as gate

    baseline = tmp_path / "baseline.json"
    baseline.write_text(json.dumps({"max_missing_help": 0}), encoding="utf-8")
    monkeypatch.setattr(gate, "_BASELINE", baseline)
    # 伪造「有 3 个缺失」
    monkeypatch.setattr(gate, "collect_missing_help", lambda: [("cmd", "--x")] * 3)

    code = gate.main(["--update"])
    assert code == 1, "基线上调未被拒绝"
    assert json.loads(baseline.read_text(encoding="utf-8"))["max_missing_help"] == 0


def test_gate_cli_passes_on_clean_tree() -> None:
    """端到端：干净树上运行门禁脚本必须退出 0。"""
    completed = subprocess.run(
        [sys.executable, "tools/gates/check_agent_surface_help.py"],
        cwd=_ROOT, capture_output=True, text=True, encoding="utf-8",
        errors="replace", check=False, timeout=180, env=utf8_env(),
    )
    assert completed.returncode == 0, (
        f"门禁在干净树上失败：{completed.stdout[-300:]}{completed.stderr[-300:]}"
    )
    assert "[OK]" in (completed.stdout or "")
