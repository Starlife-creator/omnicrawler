"""A-P3-1：变更检测器错误表 / 检查结果表 FIFO 上限淘汰契约回归测试。"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from omnicrawler.scheduling import change_detector as detector_module
from omnicrawler.scheduling.change_detector import ChangeDetector


class _BoomFetcher:
    """fetch 始终抛错，触发检测器的错误登记路径。"""

    def fetch(self, _request: object) -> object:
        raise RuntimeError("boom")


def test_fetch_errors_table_is_bounded(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(detector_module, "_FETCH_ERRORS_MAX", 4)
    detector = ChangeDetector(data_dir=tmp_path / "monitor", fetcher=_BoomFetcher())
    for index in range(10):
        asyncio.run(detector._fetch_content(f"https://site-{index}.example.com/"))
    assert len(detector._fetch_errors) == 4
    assert "https://site-0.example.com/" not in detector._fetch_errors
    assert "https://site-9.example.com/" in detector._fetch_errors


def test_check_results_table_is_bounded(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(detector_module, "_CHECK_RESULTS_MAX", 4)
    detector = ChangeDetector(data_dir=tmp_path / "monitor")
    for index in range(10):
        detector._check_results[f"rule-{index}"] = {"status": "not_checked"}
        detector._bound_dict(detector._check_results, detector_module._CHECK_RESULTS_MAX)
    assert len(detector._check_results) == 4
    assert "rule-0" not in detector._check_results
    assert "rule-9" in detector._check_results


def test_bound_dict_static_helper_fifo_eviction() -> None:
    table = {f"k{index}": index for index in range(6)}
    ChangeDetector._bound_dict(table, 3)
    assert list(table.keys()) == ["k3", "k4", "k5"]
