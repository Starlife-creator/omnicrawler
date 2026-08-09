from __future__ import annotations

import copy
import threading
from pathlib import Path

from omnicrawl.core.config import DEFAULTS, AppConfig
from omnicrawl.core.models import CrawlRequest, ExtractedRecord
from omnicrawl.pipeline_ops.preflight import run_preflight
from omnicrawl.quality.error_center import build_error_center, diagnose_error
from omnicrawl.review.run_compare import compare_runs
from omnicrawl.runtime.resource_profiles import effective_concurrency, profile_for
from omnicrawl.runtime.run_control import RunControl
from omnicrawl.services.config_history import ConfigHistory
from omnicrawl.state import StateStore


def _config(tmp_path: Path, profile: str = "balanced") -> AppConfig:
    raw = copy.deepcopy(DEFAULTS)
    raw["project"] = {"name": "enhanced", "workspace": str(tmp_path / "work")}
    raw["source"] = {"kind": "static_html", "seeds": ["https://example.com"]}
    raw["resources"]["profile"] = profile
    path = tmp_path / "config.yaml"
    path.write_text("project:\n  name: enhanced\n", encoding="utf-8")
    return AppConfig(path, tmp_path, raw, tmp_path / "work")


def test_preflight_and_resource_profiles(tmp_path: Path) -> None:
    config = _config(tmp_path, "economy")
    report = run_preflight(config)
    assert report["ok"] is True
    assert profile_for(config).name == "economy"
    assert effective_concurrency(config, 99) <= 2
    assert report["estimate"]["sample_pages_recommended"] == 3


def test_run_control_pause_resume_and_stop(tmp_path: Path) -> None:
    control = RunControl(tmp_path)
    control.reset()
    control.pause()
    threading.Timer(0.1, control.resume).start()
    assert control.wait_if_paused(poll_seconds=0.02) is True
    control.request_stop()
    assert control.wait_if_paused() is False


def test_error_center_compare_and_config_history(tmp_path: Path) -> None:
    config = _config(tmp_path)
    config.workspace.mkdir()
    request = CrawlRequest("https://example.com/item?access_token=url-secret")
    with StateStore(config.workspace / "state.sqlite3") as state:
        before = state.start_run("enhanced", str(config.path))
        state.save_records(
            before, request, [ExtractedRecord(request.url, "item", {"id": 1, "title": "Old"})]
        )
        after = state.start_run("enhanced", str(config.path))
        state.save_records(
            after, request, [ExtractedRecord(request.url, "item", {"id": 1, "title": "New"})]
        )
        state.add_error(after, request, "fetch", TimeoutError("timed out token=error-secret"), retryable=True)
        comparison = compare_runs(state, before, after)
        assert comparison["modified"] == 1
        center = build_error_center(state, config.workspace / "output", after)
        assert center["categories"]["network_transient"] == 1
        assert Path(center["files"]["html"]).is_file()
        rendered = Path(center["files"]["json"]).read_text(encoding="utf-8")
        assert "url-secret" not in rendered
        assert "error-secret" not in rendered
    assert diagnose_error("fetch", "HTTPError", "403 Forbidden").category == "access_policy"

    history = ConfigHistory(tmp_path / "history")
    first = history.snapshot(config.path)
    assert first is not None
    config.path.write_text("project:\n  name: changed\n", encoding="utf-8")
    second = history.snapshot(config.path)
    assert second != first
    assert len(history.list(config.path.stem)) == 2
    history.restore(first, config.path)
    assert "enhanced" in config.path.read_text(encoding="utf-8")
