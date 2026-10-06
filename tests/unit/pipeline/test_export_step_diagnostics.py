"""Delivery diagnostics reflect actual file writes and failures."""
from pathlib import Path

import pytest

from omnicrawler.core.config import DEFAULTS, AppConfig, deep_merge
from omnicrawler.core.models import CrawlRequest, ExtractedRecord
from omnicrawler.pipeline.exporters import export_all
from omnicrawler.state import StateStore


def _config(tmp_path):
    raw = deep_merge(DEFAULTS, {
        "project": {"name": "Export", "task_id": "export-fixture"},
        "source": {"kind": "rest", "seeds": ["https://example.test"]},
        "outputs": {"jsonl": True, "csv": True, "xlsx": False},
    })
    return AppConfig(tmp_path / "task.yaml", tmp_path, raw, tmp_path / "work")


def _seed(store, config):
    run = store.start_run("Export", str(config.path), task_id="export-fixture")
    store.save_records(run, CrawlRequest("https://example.test"), [
        ExtractedRecord("https://example.test", "item", {"price": 0, "in_stock": False}),
    ])
    return run


def test_delivery_step_records_actual_output_size_and_boundaries(tmp_path):
    config = _config(tmp_path)
    with StateStore(config.workspace / "state.sqlite3") as store:
        run = _seed(store, config)
        summary = export_all(config, store, run)
        step = store.checkpoint(run, "export", "step:delivery")
    assert step is not None and step["status"] == "succeeded"
    payload = step["payload"]
    files = {Path(name) for name in summary["files"].values() if Path(name).is_file()}
    assert payload["files"] == len(files)
    assert payload["output_bytes"] == sum(path.stat().st_size for path in files)
    assert payload["records"] == 1
    assert payload["started_at"] <= payload["finished_at"]
    assert payload["duration_seconds"] >= 0


def test_failed_delivery_preserves_failure_class_without_sensitive_message(tmp_path, monkeypatch):
    config = _config(tmp_path)
    with StateStore(config.workspace / "state.sqlite3") as store:
        run = _seed(store, config)

        original_open = Path.open

        def fail(path, *args, **kwargs):
            if path.name == "records.jsonl":
                raise OSError("private-location?token=secret")
            return original_open(path, *args, **kwargs)

        monkeypatch.setattr(Path, "open", fail)
        with pytest.raises(OSError):
            export_all(config, store, run)
        step = store.checkpoint(run, "export", "step:delivery")
    assert step is not None and step["status"] == "failed"
    assert step["payload"]["error_type"] == "OSError"
    assert "secret" not in str(step)
