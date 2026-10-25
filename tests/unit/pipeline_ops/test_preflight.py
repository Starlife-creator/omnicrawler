"""C-#16：``pipeline_ops.preflight`` 配置错误汇总 / 依赖口径 / 激进限速告警 / 估计直接单测。"""
from __future__ import annotations

from pathlib import Path

from omnicrawler.core.config import AppConfig, DEFAULTS, deep_merge
from omnicrawler.pipeline_ops.preflight import (
    _install_requirement,
    _required_dependencies,
    run_preflight,
)


def _make_config(tmp_path: Path, **overrides: object) -> AppConfig:
    workspace = tmp_path / "work"
    raw = deep_merge(DEFAULTS, {
        "project": {"name": "preflight", "workspace": str(workspace)},
        "source": {"kind": "crawl", "seeds": ["http://127.0.0.1:1/x"]},
        "crawl": {"max_pages": 2, "max_depth": 0, "same_host": True},
        "http": {"user_agent": "PreflightTest/1.0", "respect_robots": False, "delay_seconds": 1.0},
        **overrides,
    })
    return AppConfig(Path("<m>"), tmp_path, raw, workspace)


def test_install_requirement_parses_pip_hint_and_falls_back_to_label() -> None:
    assert _install_requirement("Playwright", "pip install omnicrawler-platform[browser]") == "omnicrawler-platform[browser]"
    assert _install_requirement("yaml", "") == "yaml"


def test_required_dependencies_by_source_and_pdf(tmp_path: Path) -> None:
    crawl = _make_config(tmp_path)
    labels = [label for label, _, _ in _required_dependencies(crawl)]
    assert labels == ["PyYAML"]

    browser = _make_config(tmp_path, source={"kind": "browser"})
    labels = [label for label, _, _ in _required_dependencies(browser)]
    assert "Playwright" in labels

    pdf = _make_config(tmp_path, processors={"pdf": {"enabled": True}})
    labels = [label for label, _, _ in _required_dependencies(pdf)]
    assert "pdfplumber" in labels and "openpyxl" in labels


def test_preflight_valid_config_reports_ok_with_disk_and_estimate(tmp_path: Path) -> None:
    result = run_preflight(_make_config(tmp_path))
    assert result["ok"] is True
    codes = {check["code"] for check in result["checks"]}
    assert "disk_space" in codes
    assert result["estimate"]["maximum_pages"] == 2
    assert result["estimate"]["sample_pages_recommended"] <= 3


def test_preflight_collects_config_errors_as_error_checks(tmp_path: Path) -> None:
    config = _make_config(tmp_path, source={"kind": "crawl", "seeds": []})
    result = run_preflight(config)
    assert result["ok"] is False
    assert result["errors"] >= 1
    assert any(check["code"].startswith("config_error_") and check["status"] == "error" for check in result["checks"])


def test_aggressive_rate_warning_with_patch_suggestion(tmp_path: Path) -> None:
    config = _make_config(
        tmp_path,
        crawl={"concurrency": 12},
        http={"delay_seconds": 0.1, "user_agent": "PreflightTest/1.0", "respect_robots": False},
    )
    result = run_preflight(config)
    warning = next(check for check in result["checks"] if check["code"] == "aggressive_rate")
    assert warning["status"] == "warning"
    assert warning["fix"]["patch"]["crawl.concurrency"] == 4
    assert warning["fix"]["patch"]["http.delay_seconds"] == 1.0
