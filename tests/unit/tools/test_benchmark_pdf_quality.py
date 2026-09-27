"""`tools/benchmark_pdf_quality.py` 的入口契约守卫。

为什么要有这个文件：仓库里**同族的 `tools/benchmark_quality.py` 自己就没有任何测试**，
这正是 `services/pdf_quality_benchmark.py`（596 行、5 个用例、逐字门限判据）长期
"有测试、无入口"的原因——没人跑它，也就没有人接它。所以新入口必须自带守卫。

本文件**不跑真实 PDF 管线**（那属于 `tests/integration/pdf/` 的职责），只钉入口契约：

* 选中 0 个用例 ⇒ 判红（退出码 2）
* 选中用例一个都没跑起来（环境全缺）⇒ 判红（退出码 3）
* 有用例跑完但未达门槛 ⇒ 判红（退出码 1）
* 全部达门槛 ⇒ 绿（退出码 0），且报告里 `skipped` 如实登记
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest

from omnicrawler.services.pdf_quality_benchmark import PdfQualityScore

_REPO_ROOT = Path(__file__).resolve().parents[3]
_TOOL_PATH = _REPO_ROOT / "tools" / "benchmark_pdf_quality.py"

_TEXT_CASE = "digital-text-layer"
_OCR_CASE = "scanned-image-only"


def _load_tool() -> ModuleType:
    spec = importlib.util.spec_from_file_location("_bench_pdf_tool", _TOOL_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def tool() -> ModuleType:
    return _load_tool()


def _score(case: str, *, ok: bool) -> PdfQualityScore:
    return PdfQualityScore(
        case=case,
        kind="text_layer",
        expected_fields=3,
        observed_fields=3,
        matched_fields=3 if ok else 2,
        field_accuracy=1.0 if ok else 2 / 3,
        evidence_ratio=1.0,
        review_violations=0,
        unreviewed_errors=0,
        missing_fields=() if ok else ("amount",),
    )


# ── 空集合必须判红（AGENTS.md 二）────────────────────────────────


def test_unknown_case_name_is_red(tool: ModuleType, capsys: pytest.CaptureFixture[str]) -> None:
    assert tool.main(["--case", "no-such-case"]) == 2
    assert "没有匹配的用例" in capsys.readouterr().err


def test_all_cases_skipped_is_red(
    tool: ModuleType, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """环境全部不可达 ⇒ 实际跑起来 0 个 ⇒ 判红，且原因要写进 stderr。"""
    monkeypatch.setattr(tool, "_missing_env_reason", lambda _case, _tess: "环境不可达（测试注入）")

    assert tool.main(["--case", _TEXT_CASE]) == 3
    captured = capsys.readouterr()
    # 判红结论走 stderr（与 tools/benchmark_quality.py 同口径）；逐用例明细走 stdout
    assert "一个都没跑起来" in captured.err
    assert "环境不可达（测试注入）" in captured.out


# ── 判红方向不得反转（fail-open 反向断言）──────────────────────


@pytest.mark.parametrize(
    ("ok", "expected_exit"),
    [(True, 0), (False, 1)],
    ids=["meets-gate", "misses-gate"],
)
def test_exit_code_tracks_gate_not_execution(
    tool: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    ok: bool,
    expected_exit: int,
) -> None:
    """★ 反向断言：退出码必须跟着**门限**走，不能只看"跑没跑"。

    `ok=False` 那条是 fail-open 的反向用例——若实现只按"无异常"判绿，
    门限未达会静默通过，这条必须转红。
    """
    monkeypatch.setattr(tool, "_missing_env_reason", lambda _case, _tess: "")
    monkeypatch.setattr(
        tool, "run_case", lambda case, **_kw: _score(case.name, ok=ok), raising=True
    )

    assert tool.main(["--case", _TEXT_CASE]) == expected_exit


# ── 报告形状 ───────────────────────────────────────────────────


def test_json_report_records_skips_and_selection(
    tool: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """跳过的用例必须带原因进报告，且不得被算成通过。"""
    monkeypatch.setattr(
        tool, "_missing_env_reason", lambda case, _t: "缺 tesseract（测试注入）" if case.needs_ocr else ""
    )
    monkeypatch.setattr(
        tool, "run_case", lambda case, **_kw: _score(case.name, ok=True), raising=True
    )
    report = tmp_path / "report.json"

    assert tool.main(["--case", _TEXT_CASE, "--case", _OCR_CASE, "--json", str(report)]) in (0, 3)

    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["selected"] == [_TEXT_CASE, _OCR_CASE]
    assert [item["case"] for item in payload["skipped"]] == [_OCR_CASE]
    assert payload["skipped"][0]["reason"]
    assert payload["failed"] == []
    # 跑起来的那条才有分数
    assert [item["case"] for item in payload["cases"]] == [_TEXT_CASE]


def test_tesseract_resolution_prefers_explicit_then_bundled(
    tool: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """定位顺序：显式参数 → 仓库自带 → PATH。"""
    explicit = tmp_path / "tess"
    explicit.write_text("", encoding="utf-8")
    assert tool._resolve_tesseract(str(explicit)) == explicit

    monkeypatch.setattr(tool, "BUNDLED_TESSERACT", tmp_path / "absent.exe")
    monkeypatch.setattr(tool.shutil, "which", lambda _name: str(explicit))
    assert tool._resolve_tesseract(None) == explicit

    monkeypatch.setattr(tool.shutil, "which", lambda _name: None)
    assert tool._resolve_tesseract(None) is None


def test_missing_env_reason_ignores_non_ocr_cases(tool: ModuleType) -> None:
    """非 OCR 用例不该被字体/tesseract 缺口拦住。"""
    text_case = next(case for case in tool.PDF_CASES if not case.needs_ocr)
    assert tool._missing_env_reason(text_case, None) == ""


def test_missing_env_reason_reports_ocr_gaps(tool: ModuleType) -> None:
    """OCR 用例缺 tesseract 时必须给出**具体**原因，不能含糊。"""
    ocr_cases = [case for case in tool.PDF_CASES if case.needs_ocr]
    assert ocr_cases, "内置用例里应当有 needs_ocr 的形态"

    monkey_reason = tool._missing_env_reason(ocr_cases[0], None)
    assert "tesseract" in monkey_reason
