"""`--config` 的位置：父级选项写在子命令之后也要能用（2026-09-13）。

## 背景

`argparse` 的**父解析器**选项必须写在子命令**之前**，于是 `pdfx doctor --config X`
报 `unrecognized arguments: --config X`。从 `omnicrawler pdf …` 转进来的命令行里
用户很自然会写在后面，因此顶层 CLI 曾**完全用不了自定义 PDF 项目**（只能用内置模板）。

处置是**只搬位置**：`_hoist_parent_options()` 把 `--config` 从子命令后提到它前面。
不在顶层 CLI 做"选项提升"（那要复制一份 pdfx 选项知识 ＝ 第二个真源）；
本规则只涉及**一个**选项、**一条**规则，选项定义仍只在 `build_parser`。

本文件同时锁住**向后兼容**：原本能用的写法（写在前面）与 `--help` 都不受影响。
"""

from __future__ import annotations

import subprocess
import sys

import pytest

from omnicrawler.pdfx.cli import _hoist_parent_options, build_parser


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        # 已经写在前面：原样不动
        (["--config", "X", "doctor"], ["--config", "X", "doctor"]),
        # 写在后面：搬到前面
        (["doctor", "--config", "X"], ["--config", "X", "doctor"]),
        (["doctor", "--config=X"], ["--config=X", "doctor"]),
        # 子命令自己的选项不动，相对顺序保持
        (["extract", "--limit", "5", "--config", "X"], ["--config", "X", "extract", "--limit", "5"]),
        # 无关命令行完全不受影响
        (["doctor"], ["doctor"]),
        (["--help"], ["--help"]),
        (["doctor", "--help"], ["doctor", "--help"]),
        # 末尾孤立的 `--config`（缺值）不搬，交给 argparse 报它自己的错
        (["doctor", "--config"], ["doctor", "--config"]),
    ],
)
def test_hoist_is_a_pure_reordering(given: list[str], expected: list[str]) -> None:
    assert _hoist_parent_options(given) == expected


@pytest.mark.parametrize(
    "argv",
    [
        ["--config", "/tmp/x.yaml", "doctor"],
        ["doctor", "--config", "/tmp/x.yaml"],
        ["doctor", "--config=/tmp/x.yaml"],
    ],
)
def test_parser_accepts_config_in_either_position(argv: list[str]) -> None:
    """三种写法都要解析出同一个结果 —— 这是本修复的**唯一**契约。"""
    args = build_parser().parse_args(_hoist_parent_options(argv))
    assert args.command == "doctor"
    assert args.config == "/tmp/x.yaml"


def test_default_template_still_used_when_config_absent() -> None:
    """不写 `--config` 时仍回落内置模板（不能因为搬位置而改变默认值）。"""
    args = build_parser().parse_args(_hoist_parent_options(["doctor"]))
    assert args.config.startswith("builtin:")


@pytest.mark.parametrize(
    "suffix",
    [
        ["--config", "definitely-missing.yaml", "doctor"],
        ["doctor", "--config", "definitely-missing.yaml"],
    ],
)
def test_top_level_cli_reaches_the_config_in_both_positions(suffix: list[str]) -> None:
    """端到端：`omnicrawler pdf …` 两种位置都应真的**读到配置**。

    用一个不存在的配置路径来判定"确实走进了解析器"：两条命令都应报
    "配置文件不存在"，而不是报 `unrecognized arguments`。
    """
    result = subprocess.run(
        [sys.executable, "-m", "omnicrawler", "pdf", *suffix],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )
    output = (result.stdout or "") + (result.stderr or "")
    assert "配置文件不存在" in output, f"未走到读配置这一步：{output[-300:]}"
    assert "unrecognized arguments" not in output, output[-300:]
