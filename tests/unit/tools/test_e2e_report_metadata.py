"""e2e/render_report.py 生成的报告必须带版本元数据行。

为什么要有这个文件：`docs/` 下的现状文档都要带 `> 适用版本：X.Y.Z` 行
（由 `tools/gates/check_docs_consistency.py` 校验），而
`docs/E2E_TEST_REPORT.md` 是**由 e2e 运行生成并被 git 跟踪**的。

实测（2026-11-24）：生成器此前**不输出这一行**，于是「跑一次 e2e」→
报告被覆盖 → 文档门禁判红。而跑 e2e 是 CI 与本地的常规动作，
等于这个门禁会被一个正常操作弄红。

本文件钉住：生成器的输出**本身**就满足门禁要求，不依赖事后手改。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_RENDERER = _REPO_ROOT / "e2e" / "render_report.py"


def _load_renderer() -> ModuleType:
    spec = importlib.util.spec_from_file_location("_e2e_render", _RENDERER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def rendered() -> str:
    module = _load_renderer()
    return module.build_report(
        version="1.1.1",
        pytest_exit=0,
        counts={"tests": 11, "failures": 0, "errors": 0, "skipped": 0},
        expected_tests=11,
        total_coverage=99.0,
        coverage_target=95.0,
        browser_enabled=False,
    )


def test_report_carries_the_version_metadata_line(rendered: str) -> None:
    """门禁要求的那一行必须在，且版本号可解析。"""
    lines = rendered.splitlines()
    metadata = [ln for ln in lines if ln.startswith("> 适用版本：")]
    assert metadata, f"缺少版本元数据行；报告开头：{lines[:4]}"
    assert "1.1.1" in metadata[0]


def test_report_matches_the_gate_parser(rendered: str) -> None:
    """直接用门禁自己的正则验一遍 —— 比手写断言更能防「格式微调后失效」。"""
    from tools.gates.check_docs_consistency import _MARKER_VERSION

    match = _MARKER_VERSION.search(rendered)
    assert match is not None, "门禁的正则解析不到版本号"
    assert match.group(1) == "1.1.1"


def test_committed_report_is_still_well_formed() -> None:
    """仓库里那份已提交的报告也要合规（防止有人绕过生成器手改）。"""
    from tools.gates.check_docs_consistency import _MARKER_VERSION

    committed = _REPO_ROOT / "docs" / "E2E_TEST_REPORT.md"
    assert committed.is_file()
    assert _MARKER_VERSION.search(committed.read_text(encoding="utf-8")) is not None
