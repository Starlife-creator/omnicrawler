from __future__ import annotations

import copy
from datetime import datetime
from pathlib import Path

import pytest

from omnicrawl.core.config import DEFAULTS, AppConfig
from omnicrawl.core.models import CrawlRequest, FetchResult
from omnicrawl.fetching.browser_fetcher import BrowserFetcher
from omnicrawl.pipeline_ops.pdf_region import extract_region, make_region_rule
from omnicrawl.plugins.plugin_inspector import inspect_plugin
from omnicrawl.quality.artifact_integrity import verify_artifacts
from omnicrawl.runtime.schedule_conditions import evaluate_conditions
from omnicrawl.security.security_audit import pii_summary, scan_config_file
from omnicrawl.services.regression_library import RegressionLibrary, verify_regression_fixtures
from omnicrawl.state import StateStore
from omnicrawl.templates.template_diff import diff_templates, merge_template_upgrade


def _config(tmp_path: Path) -> AppConfig:
    raw = copy.deepcopy(DEFAULTS)
    raw["project"] = {"name": "strengthened", "workspace": str(tmp_path / "work")}
    raw["source"] = {"kind": "static_html", "seeds": ["https://example.com/"]}
    raw["extract"] = {"mode": "generic", "fields": {}}
    path = tmp_path / "config.yaml"
    path.write_text("project:\n  name: strengthened\n", encoding="utf-8")
    return AppConfig(path, tmp_path, raw, tmp_path / "work")


def test_security_audit_and_pii_summary(tmp_path: Path) -> None:
    config_path = tmp_path / "secrets.yaml"
    config_path.write_text(
        "password: visible-secret\napi_key: secret://research/api\ntoken: ${TOKEN}\n",
        encoding="utf-8",
    )
    result = scan_config_file(config_path)
    assert result["ok"] is False
    assert [item["key"].lower() for item in result["findings"]] == ["password"]
    counts = pii_summary(
        [{"email": "student@example.edu", "phone": "13800138000", "id": "11010519491231002X"}]
    )
    assert counts == {"email": 1, "phone": 1, "id_card_candidate": 1}


def test_plugin_static_inspection_and_compatibility(tmp_path: Path) -> None:
    compatible = tmp_path / "safe_plugin.py"
    compatible.write_text(
        "PLUGIN_METADATA = {\n"
        "  'name': 'safe', 'version': '1.0.0', 'api_version': 1,\n"
        "  'permissions': ['network'], 'capabilities': ['fetcher'],\n"
        "  'min_core_version': '0.0.1'\n"
        "}\n"
        "def register(registry):\n    return None\n",
        encoding="utf-8",
    )
    broken = tmp_path / "broken_plugin.py"
    broken.write_text("PLUGIN_METADATA = {'api_version': 99}\n", encoding="utf-8")
    result = inspect_plugin(compatible)
    assert result.compatible is True
    assert result.permissions == ("network",)
    assert result.capabilities == ("fetcher",)
    invalid = inspect_plugin(broken)
    assert invalid.compatible is False
    assert len(invalid.errors) == 2


def test_offline_regression_fixture_capture_and_verify(tmp_path: Path) -> None:
    config = _config(tmp_path)
    result = FetchResult(
        CrawlRequest("https://example.com/"),
        "https://example.com/",
        200,
        {"content-type": "text/html; charset=utf-8", "authorization": "secret"},
        b"<html><head><title>Research</title></head><body><main>Hello</main></body></html>",
        0.1,
    )
    library = RegressionLibrary(config)
    manifest = library.capture(result, records=1, processor="html")
    assert manifest is not None and manifest.is_file()
    loaded = library.load()
    assert loaded[0][1].body == result.body
    assert "authorization" not in loaded[0][0]["headers"]
    report = verify_regression_fixtures(config)
    assert report["ok"] is True
    assert report["fixtures"] == 1


def test_artifact_integrity_detects_change(tmp_path: Path) -> None:
    artifact = tmp_path / "download.bin"
    artifact.write_bytes(b"original")
    result = FetchResult(
        CrawlRequest("https://example.com/download.bin", kind="asset"),
        "https://example.com/download.bin",
        200,
        {"content-type": "application/octet-stream"},
        b"original",
        0.1,
    )
    with StateStore(tmp_path / "state.sqlite3") as state:
        run_id = state.start_run("integrity", "config.yaml")
        state.save_artifact(run_id, result, artifact)
        assert verify_artifacts(state, run_id)["valid"] == 1
        artifact.write_bytes(b"changed")
        report = verify_artifacts(state, run_id)
        assert report["ok"] is False
        assert report["corrupt"] == 1


def test_schedule_allowed_hours_can_defer() -> None:
    from datetime import timezone

    utc_hour = datetime.now(timezone.utc).hour
    disallowed = (utc_hour + 1) % 24
    allowed, reason = evaluate_conditions({"allowed_hours": [disallowed]})
    assert allowed is False
    assert str(utc_hour) in reason  # S2.5.44：与 next_run_at 统一 UTC 基准


def test_pdf_rectangle_rule(tmp_path: Path) -> None:
    fitz = pytest.importorskip("fitz")
    pdf = tmp_path / "sample.pdf"
    document = fitz.open()
    page = document.new_page(width=300, height=200)
    page.insert_text((40, 80), "Selected Research Text")
    document.save(pdf)
    document.close()
    text = extract_region(pdf, 1, (20, 40, 280, 110))  # S3.1.17：1 基页码
    assert "Selected Research Text" in text
    rule = make_region_rule(pdf, "abstract", 1, (20, 40, 280, 110))
    assert rule.page == 1
    assert rule.confidence == 0.95


class _FakeLocator:
    def __init__(self, present: bool, events: list[tuple[str, str]]) -> None:
        self.present = present
        self.events = events

    def count(self) -> int:
        return int(self.present)

    def click(self, timeout: int) -> None:
        self.events.append(("click", str(timeout)))

    def fill(self, value: str) -> None:
        self.events.append(("fill", value))


class _FakePage:
    def __init__(self) -> None:
        self.events: list[tuple[str, str]] = []

    def locator(self, selector: str) -> _FakeLocator:
        return _FakeLocator(selector == "#fallback", self.events)


def test_browser_actions_use_selector_fallback_and_optional_steps() -> None:
    page = _FakePage()
    BrowserFetcher._run_actions(
        page,
        [
            {"action": "click", "selector": "#missing", "selectors": ["#fallback"]},
            {"action": "fill", "selector": "#absent", "value": "ignored", "if_present": True},
            {"action": "unknown", "optional": True},
        ],
    )
    assert page.events == [("click", "10000")]


def test_template_diff_and_three_way_upgrade_merge() -> None:
    base = {"http": {"timeout": 10, "retries": 2}, "extract": {"title": "h1"}}
    user = {"http": {"timeout": 30, "retries": 2}, "extract": {"title": "h1"}}
    update = {"http": {"timeout": 15, "retries": 4}, "extract": {"title": "h1", "date": "time"}}
    changes = diff_templates(base, update)
    assert {item["path"] for item in changes} == {"extract.date", "http.retries", "http.timeout"}
    merged, conflicts = merge_template_upgrade(base, user, update)
    assert merged["http"] == {"retries": 4, "timeout": 30}
    assert merged["extract"]["date"] == "time"
    assert [item.path for item in conflicts] == ["http.timeout"]
