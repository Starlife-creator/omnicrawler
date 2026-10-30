"""C-#16：``review.run_compare`` 两轮对比闭环与归属校验直接单测（本地 SQLite 状态库）。"""
from __future__ import annotations

from pathlib import Path

import pytest

from omnicrawler.core.models import CrawlRequest, ExtractedRecord
from omnicrawler.review.run_compare import compare_runs
from omnicrawler.state import StateStore

_URL = "https://example.org/list"
_REQUEST = CrawlRequest(_URL)


def _record(rid: int, title: str) -> ExtractedRecord:
    return ExtractedRecord(_URL, "item", {"id": rid, "title": title})


def _run(state: StateStore, records: list[ExtractedRecord]) -> str:
    run_id = state.start_run("compare", "config.yaml")
    state.save_records(run_id, _REQUEST, records)
    return run_id


def test_removed_record_confirmed_between_complete_runs(tmp_path: Path) -> None:
    with StateStore(tmp_path / "state.sqlite3") as state:
        first = _run(state, [_record(1, "A"), _record(2, "B")])
        state.finish_run(first, "succeeded", {})
        second = _run(state, [_record(1, "A")])
        state.finish_run(second, "succeeded", {})

        report = compare_runs(state, first, second)

    assert report["removed"] == 1
    assert report["possibly_removed"] == 0
    removed = next(item for item in report["changes"] if item["change_type"] == "removed")
    assert removed["confirmed"] is True
    assert removed["identity"] == "item|id:2"


def test_unfinished_after_run_downgrades_to_possibly_removed(tmp_path: Path) -> None:
    with StateStore(tmp_path / "state.sqlite3") as state:
        first = _run(state, [_record(1, "A"), _record(2, "B")])
        state.finish_run(first, "succeeded", {})
        interrupted = _run(state, [_record(1, "A")])

        report = compare_runs(state, first, interrupted)

    assert report["removed"] == 0
    assert report["possibly_removed"] == 1
    pending = next(item for item in report["changes"] if item["change_type"] == "possibly_removed")
    assert pending["confirmed"] is False
    assert report["notification_summary"]["requires_review"] is True


def test_modified_record_detected_between_runs(tmp_path: Path) -> None:
    with StateStore(tmp_path / "state.sqlite3") as state:
        first = _run(state, [_record(1, "A")])
        state.finish_run(first, "succeeded", {})
        second = _run(state, [_record(1, "B")])
        state.finish_run(second, "succeeded", {})

        report = compare_runs(state, first, second)

    assert report["modified"] == 1
    modified = next(item for item in report["changes"] if item["change_type"] == "modified")
    assert "title" in modified["modified_fields"]


def test_unknown_run_rejected_before_comparison(tmp_path: Path) -> None:
    with StateStore(tmp_path / "state.sqlite3") as state:
        first = _run(state, [_record(1, "A")])
        state.finish_run(first, "succeeded", {})
        with pytest.raises(ValueError, match="运行不存在"):
            compare_runs(state, first, "missing-run")


def test_runs_of_different_tasks_rejected(tmp_path: Path) -> None:
    with StateStore(tmp_path / "state.sqlite3") as state:
        first = state.start_run("alpha", "config.yaml")
        state.save_records(first, _REQUEST, [_record(1, "A")])
        state.finish_run(first, "succeeded", {})
        second = state.start_run("beta", "config.yaml")
        state.save_records(second, _REQUEST, [_record(1, "A")])
        state.finish_run(second, "succeeded", {})
        with pytest.raises(ValueError, match="不同任务"):
            compare_runs(state, first, second)
