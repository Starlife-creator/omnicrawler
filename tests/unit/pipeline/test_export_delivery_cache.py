"""Real delivery files must track changing manifests and detect lost receipts."""
from __future__ import annotations

import json
import os

import pytest

from omnicrawler.core.models import CrawlRequest, FetchResult
from omnicrawler.pipeline import Pipeline
from omnicrawler.pipeline.export_receipts import capture_export_receipts
from omnicrawler.pipeline.exporters import export_all
from tests.unit.pipeline.test_review_export_freshness import config, seed


@pytest.mark.parametrize("damage", ["missing", "same_size_modified"])
def test_cached_export_repairs_missing_or_modified_output(tmp_path, damage):
    with Pipeline(config(tmp_path)) as pipeline:
        run, _ = seed(pipeline)
        first = pipeline._run_exports(run)
        path = pipeline.workspace / "output" / "records.csv"
        original = path.read_bytes()
        if damage == "missing":
            path.unlink()
        else:
            assert b"100" in original
            path.write_bytes(original.replace(b"100", b"900"))
        pipeline._run_exports(run)
        assert path.read_bytes() == original
        assert first["records"] == 1


@pytest.mark.parametrize("change", ["response", "error", "quality"])
def test_delivery_manifest_changes_invalidate_record_unchanged_cache(tmp_path, change):
    with Pipeline(config(tmp_path)) as pipeline:
        run, _ = seed(pipeline)
        before = pipeline._run_exports(run)
        request = CrawlRequest("https://example.org/")
        if change == "response":
            pipeline.state.save_response(run, FetchResult(request, request.url, 200, {}, b"body", 0), None)
        elif change == "error":
            pipeline.state.add_error(run, request, "extract", ValueError("changed manifest"))
        else:
            pipeline.state.add_quality_stats(run, {"amount": {"total": 1, "present": 1, "valid": 1}})
        after = pipeline._run_exports(run)
        if change == "quality":
            assert not before["quality"]["fields"]
            assert after["quality"]["fields"][0]["valid"] == 1
        else:
            key = "responses" if change == "response" else "errors"
            assert before[key] == 0 and after[key] == 1
            manifest = json.loads((pipeline.workspace / "output" / "summary.json").read_text(encoding="utf-8"))
            assert manifest[key] == 1


def test_corrupt_source_changes_integrity_report_without_changing_records(tmp_path):
    with Pipeline(config(tmp_path)) as pipeline:
        run, _ = seed(pipeline)
        path = pipeline.workspace / "raw" / "document.txt"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"first")
        request = CrawlRequest("https://example.org/document.txt")
        pipeline.state.save_artifact(run, FetchResult(request, request.url, 200, {}, path.read_bytes(), 0), path)
        assert pipeline._run_exports(run)["artifact_integrity"]["ok"]
        path.write_bytes(b"other")
        result = pipeline._run_exports(run)
        assert not result["artifact_integrity"]["ok"]
        assert result["artifact_integrity"]["corrupt"] == 1
        assert result["records"] == 1


def test_source_change_during_export_cannot_mark_old_integrity_report_fresh(tmp_path):
    with Pipeline(config(tmp_path)) as pipeline:
        run, _ = seed(pipeline)
        path = pipeline.workspace / "raw" / "document.txt"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"first")
        request = CrawlRequest("https://example.org/document.txt")
        pipeline.state.save_artifact(run, FetchResult(request, request.url, 200, {}, path.read_bytes(), 0), path)
        def exporter(cfg, state, run_id, options):
            result = export_all(cfg, state, run_id)
            path.write_bytes(b"other")
            return result
        pipeline.registry.exporters["default"] = exporter
        with pytest.raises(ValueError, match="源文件已变化"):
            pipeline._run_exports(run)
        assert pipeline.state.export_commit(f"{run}:export:default")["status"] == "failed"


def test_receipt_cannot_read_paths_outside_output_directory(tmp_path):
    outside = tmp_path / "private.txt"
    outside.write_text("never read", encoding="utf-8")
    with pytest.raises(ValueError):
        capture_export_receipts(tmp_path / "work", {"files": {"csv": str(outside)}})


def test_export_read_snapshot_does_not_commit_an_existing_transaction(tmp_path):
    with Pipeline(config(tmp_path)) as pipeline:
        run, record_id = seed(pipeline)
        initial = pipeline.state.export_input_digest(run)
        assert not pipeline.state.conn.in_transaction
        pipeline.state.conn.execute("BEGIN IMMEDIATE")
        pipeline.state.conn.execute(
            "UPDATE records SET data_json=? WHERE record_id=?", ('{"amount":123}', record_id),
        )
        assert pipeline.state.export_input_digest(run) != initial
        assert pipeline.state.conn.in_transaction
        pipeline.state.conn.rollback()
        assert pipeline.state.export_input_digest(run) == initial


def test_pending_frontier_invalidates_delivery_completeness_cache(tmp_path):
    with Pipeline(config(tmp_path)) as pipeline:
        run, _ = seed(pipeline)
        assert pipeline._run_exports(run)["delivery"]["frontier_pending"] == 0
        pipeline.state.enqueue(CrawlRequest("https://example.org/next"))
        assert pipeline._run_exports(run)["delivery"]["frontier_pending"] == 1


@pytest.mark.parametrize("target", ["output", "previous"])
def test_export_cannot_write_through_an_output_directory_link(tmp_path, target):
    with Pipeline(config(tmp_path)) as pipeline:
        run, _ = seed(pipeline)
        outside = tmp_path / "outside"
        outside.mkdir()
        sentinel = outside / "records.csv"
        sentinel.write_bytes(b"preserve outside bytes")
        output = pipeline.workspace / "output"
        output.mkdir(exist_ok=True)
        link = output if target == "output" else output / "previous"
        if link.is_dir():
            link.rmdir()  # Empty test-owned directory only.
        if os.name == "nt":
            import _winapi
            _winapi.CreateJunction(str(outside), str(link))
        else:
            link.symlink_to(outside, target_is_directory=True)
        try:
            with pytest.raises(ValueError, match="越出工作区"):
                export_all(pipeline.config, pipeline.state, run)
            assert sentinel.read_bytes() == b"preserve outside bytes"
        finally:
            if os.name == "nt":
                link.rmdir()  # Remove the junction itself, never its target contents.
            else:
                link.unlink()


def test_error_arriving_during_export_cannot_leave_a_successful_stale_manifest(tmp_path):
    with Pipeline(config(tmp_path)) as pipeline:
        run, _ = seed(pipeline)
        request = CrawlRequest("https://example.org/")
        def exporter(cfg, state, run_id, options):
            result = export_all(cfg, state, run_id)
            state.add_error(run, request, "extract", ValueError("concurrent diagnostic"))
            return result
        pipeline.registry.exporters["default"] = exporter
        with pytest.raises(ValueError, match="交付数据已变化"):
            pipeline._run_exports(run)
        assert pipeline.state.export_commit(f"{run}:export:default")["status"] == "failed"
