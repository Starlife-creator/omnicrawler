from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from omnicrawl.core.capabilities import capability_report
from omnicrawl.core.migrations import CURRENT_CONFIG_VERSION, migrate_config, migrate_file
from omnicrawl.services.config_history import ConfigHistory
from omnicrawl.templates.template_diff import (
    compare_template_files,
    diff_templates,
    merge_template_files,
    merge_template_upgrade,
)


@pytest.mark.parametrize(
    ("version", "payload", "expected"),
    [
        (
            1,
            {"seed_urls": ["https://example.org"], "source": {"kind": "rss"}},
            {"source.kind": "feed", "source.seeds": ["https://example.org"]},
        ),
        (2, {"output": {"csv": True}, "outputs": {"jsonl": True}}, {"outputs.csv": True}),
        (
            3,
            {"crawl": {"pagination": {"param": "page"}}},
            {"source.pagination.parameter": "page"},
        ),
        (
            4,
            {"extract": {"mode": "json", "item_selector": "$.items"}},
            {"extract.item_path": "$.items"},
        ),
        (5, {"vendor": {"opaque": [1, 2, 3]}}, {"vendor.opaque": [1, 2, 3]}),
    ],
)
def test_v1_to_v5_migration_matrix(version, payload, expected) -> None:
    original = {"config_version": version, "vendor_keep": {"enabled": True}, **payload}
    migrated, _notes = migrate_config(original)
    assert migrated["config_version"] == CURRENT_CONFIG_VERSION
    assert migrated["vendor_keep"] == {"enabled": True}
    for path, value in expected.items():
        current = migrated
        for key in path.split("."):
            current = current[key]
        assert current == value
    assert original["config_version"] == version


def test_migration_handles_invalid_and_future_versions() -> None:
    migrated, notes = migrate_config({"config_version": "invalid", "custom": 1})
    assert migrated["config_version"] == CURRENT_CONFIG_VERSION
    assert "invalid config_version" in notes[0]

    future = {"config_version": 99, "future": {"keep": True}}
    unchanged, notes = migrate_config(future)
    assert unchanged == future
    assert "newer" in notes[0]


def test_migrate_file_guards_and_overwrite(tmp_path: Path) -> None:
    missing = tmp_path / "missing.yaml"
    with pytest.raises(FileNotFoundError):
        migrate_file(missing, tmp_path / "out.yaml")

    source = tmp_path / "source.yaml"
    target = tmp_path / "target.yaml"
    source.write_text("- not\n- a mapping\n", encoding="utf-8")
    with pytest.raises(ValueError):
        migrate_file(source, target)

    source.write_text("project: {name: demo}\n", encoding="utf-8")
    target.write_text("existing", encoding="utf-8")
    with pytest.raises(FileExistsError):
        migrate_file(source, target)
    path, _notes = migrate_file(source, target, overwrite=True)
    assert path == target.resolve()
    assert yaml.safe_load(target.read_text(encoding="utf-8"))["config_version"] == 5


def test_template_diff_merge_conflicts_and_files(tmp_path: Path) -> None:
    base = {"source": {"kind": "static_html", "seeds": ["old"]}, "remove": True}
    user = {"source": {"kind": "static_html", "seeds": ["user"]}, "remove": True}
    update = {"source": {"kind": "browser", "seeds": ["new"]}, "added": 3}

    changes = diff_templates(base, update)
    assert {item["change_type"] for item in changes} == {"added", "removed", "modified"}
    assert not diff_templates(base, base)

    merged, conflicts = merge_template_upgrade(base, user, update)
    assert merged["source"]["seeds"] == ["user"]
    assert merged["source"]["kind"] == "browser"
    assert merged["added"] == 3
    assert "remove" not in merged
    assert [item.path for item in conflicts] == ["source.seeds"]
    assert conflicts[0].to_dict()["user"] == ["user"]

    paths = []
    for name, value in (("base", base), ("user", user), ("update", update)):
        path = tmp_path / f"{name}.yaml"
        path.write_text(yaml.safe_dump(value), encoding="utf-8")
        paths.append(path)
    report = compare_template_files(paths[0], paths[2])
    assert report["changed"] and report["change_count"] == len(changes)
    file_merged, file_conflicts = merge_template_files(*paths)
    assert file_merged == merged
    assert file_conflicts == conflicts

    invalid = tmp_path / "invalid.yaml"
    invalid.write_text("- list", encoding="utf-8")
    with pytest.raises(ValueError):
        compare_template_files(invalid, paths[2])


def test_config_history_deduplicates_prunes_lists_and_restores(tmp_path: Path) -> None:
    config = tmp_path / "task.yaml"
    history = ConfigHistory(tmp_path / "history", keep=2)
    assert history.snapshot(config) is None

    versions = []
    for index in range(3):
        config.write_text(f"project: {{name: version-{index}}}\n", encoding="utf-8")
        version = history.snapshot(config, reason=f"edit-{index}")
        assert version is not None
        versions.append(version)
    assert len(history.list("task.yaml")) == 2

    latest = history.snapshot(config)
    assert latest == versions[-1]
    meta = latest.with_suffix(".json")
    meta.write_text("not-json", encoding="utf-8")
    assert any(item["reason"] == "unknown" for item in history.list("task"))

    restored = history.restore(latest, tmp_path / "restored" / "task.yaml")
    assert "version-2" in restored.read_text(encoding="utf-8")
    with pytest.raises(ValueError):
        history.restore(config, tmp_path / "unsafe.yaml")


def test_gui_yaml_round_trip_preserves_all_advanced_sections() -> None:
    pytest.importorskip("ruamel.yaml")
    from omnicrawl.gui.core.config_serializer import format_yaml, from_yaml, to_yaml

    original = """
vendor_extension: {keep: true}
project: {name: demo, workspace: work/demo, intent: topic_pdf}
source:
  kind: browser
  seeds: [https://example.org/list]
  pagination: {type: page, parameter: p, start: 1, end: 5}
crawl: {max_pages: 20, concurrency: 3}
http:
  user_agent: TestAgent
  respect_robots: true
  delay_seconds: 0.5
  headers: {X-Custom: keep}
extract:
  mode: html
  fields:
    title: {selector: h1, required: true}
    link:
      selector: a.download
      selectors: [{selector: a.download}, {xpath: "//a[@class='download']"}]
      attr: href
      regex: 'id=(\\d+)'
selection:
  topic:
    include_any: [alpha]
    include_all: [policy]
    exclude: [draft]
    keep_uncertain: false
download: {enabled: true, extensions: [.pdf], output_dir: artifacts}
processors:
  pdf: {enabled: true, skip_ocr: false, ocr_backend: tesseract}
updates: {enabled: true}
incremental: {skip_unchanged: true, since_date: '2026-01-01'}
outputs: {jsonl: true, csv: false, xlsx: true, parquet: false, duckdb: false}
ai:
  mode: custom
  default_provider: local
  providers:
    local: {type: openai_compatible, base_url: http://localhost:8000/v1, model: demo, api_key: secret://ai}
resources: {profile: economy}
"""
    config = from_yaml(original)
    rendered = to_yaml(config)
    loaded = from_yaml(rendered)

    assert loaded.project_name == "demo"
    assert loaded.source_kind == "browser"
    assert loaded.pagination["parameter"] == "p"
    assert loaded.fields[1].fallback_xpath == "//a[@class='download']"
    assert loaded.topic_include_all == ["policy"]
    assert loaded.keep_uncertain_topics is False
    assert loaded.process_pdf and loaded.pdf_ocr == "tesseract"
    assert loaded.monitor_same_url and loaded.incremental
    assert loaded.output_formats == ["jsonl", "xlsx"]
    assert loaded.ai_mode == "custom" and loaded.ai_api_key_ref == "secret://ai"
    assert loaded.resource_profile == "economy"
    assert loaded.passthrough["vendor_extension"]["keep"] is True
    assert loaded.passthrough["http"]["headers"]["X-Custom"] == "keep"
    assert "project:" in format_yaml(rendered)
    assert format_yaml("not: [valid") == "not: [valid"


def test_capability_report_shape_and_portable_paths(monkeypatch, tmp_path: Path) -> None:
    chrome = tmp_path / "app" / "runtime" / "chrome.exe"
    chrome.parent.mkdir(parents=True)
    chrome.write_bytes(b"binary")
    external = tmp_path / "external" / "driver.exe"
    external.parent.mkdir()
    external.write_bytes(b"driver")

    monkeypatch.setenv("OMNICRAWL_CHROME_BINARY", str(chrome))
    monkeypatch.setenv("OMNICRAWL_SELENIUM_DRIVER", str(external))
    monkeypatch.setattr("sys.executable", str(tmp_path / "app" / "omnicrawl.exe"))
    report = capability_report(verify_imports=False, portable_paths=True)

    assert report["version"]
    assert report["modules"]["core_yaml"]["installed"] is True
    assert report["native"]["chromium"]["path"].startswith("${APP_DIR}/")
    assert report["native"]["selenium_driver"]["path"].startswith("${EXTERNAL}/")
    json.dumps(report)


def test_task_capability_report_checks_only_requested_features() -> None:
    report = capability_report(mode="task", require_features=["core"])

    assert report["check"]["mode"] == "task"
    assert report["check"]["requested_features"] == ["core"]
    assert report["check"]["imported_modules"] == ["core_yaml"]
    assert report["check"]["features"]["core"]["ready"] is True
    with pytest.raises(ValueError, match="未知能力名称"):
        capability_report(mode="task", require_features=["not-a-feature"])
