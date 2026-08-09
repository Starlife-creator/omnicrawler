from __future__ import annotations

import json
from pathlib import Path

import pytest

from e2e.render_report import _junit_counts, build_report, main, project_version


@pytest.mark.e2e
def test_report_counts_and_coverage_gate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    junit = tmp_path / "junit.xml"
    junit.write_text('<testsuite tests="3" failures="0" errors="0" skipped="0"/>', encoding="utf-8")
    coverage = tmp_path / "coverage.json"
    coverage.write_text(json.dumps({"totals": {"percent_covered": 100.0}}), encoding="utf-8")
    assert _junit_counts(tmp_path / "missing.xml") == {"tests": 0, "failures": 0, "errors": 0, "skipped": 0}
    assert _junit_counts(junit) == {"tests": 3, "failures": 0, "errors": 0, "skipped": 0}
    report = build_report(
        counts=_junit_counts(junit), total_coverage=100.0, pytest_exit=0,
        coverage_target=95.0, expected_tests=3, browser_enabled=False, version=project_version(),
    )
    assert "核心本地链路" in report
    assert f"OmniCrawler {project_version()}" in report
    assert "**通过**" in report
    output = tmp_path / "report.md"
    monkeypatch.setattr(
        "sys.argv",
        ["render_report.py", "--artifacts", str(tmp_path), "--output", str(output), "--pytest-exit", "0", "--expected-tests", "3"],
    )
    assert main() == 0
    assert "100.00%" in output.read_text(encoding="utf-8")
