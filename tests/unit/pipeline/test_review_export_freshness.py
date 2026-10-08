"""Default export must reflect reviews and refuse concurrent forced delivery."""
from __future__ import annotations

import csv
from pathlib import Path

import pytest

from omnicrawler.core.config import load_config
from omnicrawler.core.models import CrawlRequest, ExtractedRecord
from omnicrawler.pipeline import Pipeline
from omnicrawler.pipeline.exporters import export_all
from omnicrawler.state import StateStore


def config(tmp_path: Path):
    path = tmp_path / "task.yaml"
    path.write_text(
        "project: {name: review-export, workspace: work}\n"
        "source: {kind: static_html, seeds: [https://example.org/]}\n"
        "outputs: {jsonl: false, csv: true, xlsx: false}\n",
        encoding="utf-8",
    )
    return load_config(path)


def seed(pipeline):
    run = pipeline.state.start_run("review-export", "task.yaml")
    pipeline.state.save_records(run, CrawlRequest("https://example.org/"), [
        ExtractedRecord("https://example.org/", "item", {"amount": 100}),
    ])
    record_id = pipeline.state.rows("SELECT record_id FROM records")[0]["record_id"]
    return run, record_id


def test_review_changes_refresh_default_export_and_unchanged_inputs_are_cached(tmp_path):
    with Pipeline(config(tmp_path)) as pipeline:
        run, record_id = seed(pipeline)
        calls = []
        def exporter(cfg, state, run_id, options):
            calls.append(run_id)
            return export_all(cfg, state, run_id)
        pipeline.registry.exporters["default"] = exporter
        pipeline._run_exports(run)
        pipeline._run_exports(run)
        assert len(calls) == 1
        pipeline.state.edit_record(record_id, "amount", 101)
        pipeline._run_exports(run)
        rows = list(csv.DictReader((pipeline.workspace / "output" / "records.csv").open(encoding="utf-8-sig")))
        assert rows[0]["amount"] == "101"
        assert len(calls) == 2
        pipeline._run_exports(run)
        assert len(calls) == 2
        pipeline.config.section("outputs")["csv"] = False
        pipeline._run_exports(run)
        assert len(calls) == 3


def test_edit_during_export_cannot_be_marked_fresh(tmp_path):
    with Pipeline(config(tmp_path)) as pipeline:
        run, record_id = seed(pipeline)
        def exporter(cfg, state, run_id, options):
            result = export_all(cfg, state, run_id)
            with StateStore(pipeline.workspace / "state.sqlite3") as reviewer:
                reviewer.edit_record(record_id, "amount", 102)
            return result
        pipeline.registry.exporters["default"] = exporter
        with pytest.raises(ValueError, match="导出过程中.*已变化"):
            pipeline._run_exports(run)
        assert pipeline.state.export_commit(f"{run}:export:default")["status"] == "failed"
        pipeline.registry.exporters["default"] = lambda cfg, state, run_id, options: export_all(cfg, state, run_id)
        pipeline._run_exports(run)
        rows = list(csv.DictReader((pipeline.workspace / "output" / "records.csv").open(encoding="utf-8-sig")))
        assert rows[0]["amount"] == "102"


def test_force_cannot_execute_an_already_running_exporter(tmp_path):
    with Pipeline(config(tmp_path)) as pipeline:
        run = pipeline.state.start_run("review-export", "task.yaml")
        calls = []
        pipeline.registry.register_exporter("boom", lambda *args: calls.append(1))
        pipeline.config.section("outputs")["exporter"] = "boom"
        assert pipeline.state.begin_export(run, "boom", f"{run}:export:boom")
        with pytest.raises(RuntimeError, match="未完成提交"):
            pipeline._run_exports(run, force=True)
        assert not calls
        assert pipeline.state.export_commit(f"{run}:export:boom")["status"] == "running"
