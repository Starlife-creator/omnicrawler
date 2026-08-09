from __future__ import annotations

import copy
import json
import zipfile
from pathlib import Path

from omnicrawl.core.config import DEFAULTS, AppConfig
from omnicrawl.core.models import CrawlRequest, ExtractedRecord, FetchResult
from omnicrawl.quality.quality_report import build_quality_report
from omnicrawl.services.research_package import create_research_package, restore_package
from omnicrawl.state import StateStore
from omnicrawl.templates.template_monitor import TemplateMonitor


def _config(tmp_path: Path) -> AppConfig:
    raw = copy.deepcopy(DEFAULTS)
    raw["project"] = {"name": "test", "workspace": str(tmp_path / "workspace")}
    raw["source"] = {"kind": "static_html", "seeds": ["https://example.com"]}
    raw["extract"]["fields"] = {"title": {"selector": "h1", "required": True}}
    path = tmp_path / "config.yaml"
    path.write_text(
        "project:\n  name: test\nhttp:\n  headers:\n    Authorization: Bearer private\n",
        encoding="utf-8",
    )
    return AppConfig(path, tmp_path, raw, tmp_path / "workspace")


def test_semantic_versions_quality_report_and_package(tmp_path: Path) -> None:
    config = _config(tmp_path)
    config.workspace.mkdir()
    state_path = config.workspace / "state.sqlite3"
    request = CrawlRequest("https://example.com/item")
    with StateStore(state_path) as state:
        first = state.start_run("test", str(config.path))
        record = ExtractedRecord(request.url, "item", {"id": 1, "title": "Old"})
        assert state.track_semantic_changes(first, [record])[0]["change_type"] == "added"
        state.save_records(first, request, [record])
        second = state.start_run("test", str(config.path))
        changed = ExtractedRecord(request.url, "item", {"id": 1, "title": "New"})
        changes = state.track_semantic_changes(second, [changed])
        assert changes[0]["modified_fields"] == ("title",)
        changed.evidence["_quality"] = {"score": 1.0, "review_required": False}
        state.save_records(second, request, [changed])
        report = build_quality_report(config, state, second)
        assert report["semantic_changes"]["modified"] == 1
        assert Path(report["files"]["html"]).is_file()

    package = tmp_path / "research.zip"
    result = create_research_package(config, package)
    assert result["files"] >= 4
    with zipfile.ZipFile(package) as archive:
        packaged_config = archive.read("project/config.redacted.yaml").decode("utf-8")
    assert "Bearer private" not in packaged_config
    restored = restore_package(package, tmp_path / "restored")
    assert restored["verified"] is True
    assert (tmp_path / "restored" / "project" / "state.sqlite3").is_file()


def test_template_monitor_detects_breaking_structure(tmp_path: Path) -> None:
    config = _config(tmp_path)
    monitor = TemplateMonitor(config)
    request = CrawlRequest("https://example.com")
    first = FetchResult(
        request,
        request.url,
        200,
        {"content-type": "text/html"},
        b"<html><body><main id='content'><h1>Title</h1></main></body></html>",
        0.1,
    )
    assert monitor.observe(first, [ExtractedRecord(request.url, "page", {"title": "Title"})], {"title": {}}).status == "healthy"
    second = FetchResult(
        request,
        request.url,
        200,
        {"content-type": "text/html"},
        b"<html><frameset><frame src='elsewhere'></frameset></html>",
        0.1,
    )
    observation = monitor.observe(second, [], {"title": {}})
    assert observation is not None
    assert observation.invalidated is True
    latest = json.loads((config.workspace / "template_health" / "latest.json").read_text(encoding="utf-8"))
    assert latest["status"] == "invalid"


def test_template_monitor_none_content_type_and_data(tmp_path: Path) -> None:
    """S1.5.6：content_type=None / record.data=None 不抛 TypeError。"""
    config = _config(tmp_path)
    monitor = TemplateMonitor(config)
    request = CrawlRequest("https://example.com")
    result = FetchResult(
        request,
        request.url,
        200,
        {},  # 无 content-type 头 → content_type 属性空串
        b"<html><body><h1>Title</h1></body></html>",
        0.1,
    )
    record = ExtractedRecord(request.url, "page", None)  # type: ignore[arg-type]
    observation = monitor.observe(result, [record], {"title": {}})
    assert observation is not None
    assert observation.status in {"healthy", "warning", "invalid"}
