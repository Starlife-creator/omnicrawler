from pathlib import Path

import pytest

from omnicrawler.core.models import CrawlRequest, ExtractedRecord
from omnicrawler.review.run_compare import compare_runs
from omnicrawler.state import StateStore


def observed(state, task_id, prices, *, explicit=False, scope="same"):
    run = state.start_run("same name", "config.yaml", task_id=task_id)
    state.save_checkpoint(run, "setup", "setup", {
        "comparison_scope": scope,
        "semantic_settings": {"identity_fields": ["sku"] if explicit else []},
    })
    for url, price in prices:
        record = ExtractedRecord(url, "product", {"id": "A", "sku": "A", "price": price})
        state.track_semantic_changes(run, [record], identity_fields=("sku",) if explicit else ())
        state.save_records(run, CrawlRequest(url), [record])
    state.finish_run(run, "succeeded", {})
    return run


def test_different_tasks_cannot_be_compared(tmp_path: Path):
    with StateStore(tmp_path / "state.sqlite3") as state:
        first = observed(state, "A", [("https://example.com/1", 100)])
        second = observed(state, "B", [("https://example.com/1", 80)])
        with pytest.raises(ValueError, match="任务"):
            compare_runs(state, first, second)


def test_automatic_identity_stays_source_scoped(tmp_path: Path):
    with StateStore(tmp_path / "state.sqlite3") as state:
        first = observed(state, "A", [("https://example.com/1", 100), ("https://example.com/2", 200)])
        second = observed(state, "A", [("https://example.com/1", 80), ("https://example.com/2", 200)])
        report = compare_runs(state, first, second)
        assert report["modified"] == 1
        assert report["changes"][0]["before"]["price"] == 100
        assert report["changes"][0]["after"]["price"] == 80


def test_explicit_identity_can_move_between_pages(tmp_path: Path):
    with StateStore(tmp_path / "state.sqlite3") as state:
        first = observed(state, "A", [("https://example.com/1", 100)], explicit=True)
        second = observed(state, "A", [("https://example.com/2", 80)], explicit=True)
        report = compare_runs(state, first, second)
        assert report["modified"] == 1
        assert report["added"] == 0


def test_identity_contract_change_requires_review(tmp_path: Path):
    with StateStore(tmp_path / "state.sqlite3") as state:
        first = observed(state, "A", [("https://example.com/1", 100)])
        second = observed(state, "A", [("https://example.com/1", 80)], explicit=True)
        report = compare_runs(state, first, second)
        assert report["notification_summary"]["requires_review"]
        assert all(not item["confirmed"] for item in report["changes"])
