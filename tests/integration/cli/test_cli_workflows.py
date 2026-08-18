from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from omnicrawler import cli


def _config(tmp_path: Path) -> Path:
    path = tmp_path / "task.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "project": {"name": "cli-test", "workspace": str(tmp_path / "workspace")},
                "source": {"kind": "static_html", "seeds": ["https://example.org"]},
                "http": {"resolve_dns": False, "respect_robots": False},
                "extract": {"mode": "html", "fields": {"title": {"selector": "title"}}},
                "retention": {"enabled": True, "keep_runs": 2},
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    return path


def _run_success(args: list[str]) -> None:
    try:
        cli.main(args)
    except SystemExit as exc:
        assert exc.code == 0


def test_cli_offline_template_workflows(tmp_path: Path, capsys) -> None:
    _run_success(["templates", "list", "--query", "PDF"])
    assert "templates" not in capsys.readouterr().err

    body = tmp_path / "body.html"
    body.write_text("<script>wp-json</script>", encoding="utf-8")
    payload = tmp_path / "payload.json"
    payload.write_text('[{"id": 1, "title": {"rendered": "Hello"}}]', encoding="utf-8")
    _run_success(
        [
            "templates",
            "recommend",
            "https://example.org/wp-json/wp/v2/posts",
            "--header",
            "Content-Type:application/json",
            "--body-file",
            str(body),
            "--json-file",
            str(payload),
        ]
    )

    rendered = tmp_path / "rendered.yaml"
    _run_success(
        [
            "templates",
            "render",
            "generic/single-page",
            "--set",
            "seed_url=https://example.org/page",
            "--output",
            str(rendered),
        ]
    )
    assert rendered.is_file()
    with pytest.raises(SystemExit) as duplicate:
        cli.main(
            [
                "templates",
                "render",
                "generic/single-page",
                "--set",
                "seed_url=https://example.org/page",
                "--output",
                str(rendered),
            ]
        )
    assert duplicate.value.code == 1

    updated = tmp_path / "updated.yaml"
    value = yaml.safe_load(rendered.read_text(encoding="utf-8"))
    value.setdefault("http", {})["delay_seconds"] = 2
    updated.write_text(yaml.safe_dump(value), encoding="utf-8")
    _run_success(["templates", "diff", str(rendered), str(updated)])

    merged = tmp_path / "merged.yaml"
    _run_success(
        [
            "templates",
            "merge",
            str(rendered),
            str(rendered),
            str(updated),
            "--output",
            str(merged),
        ]
    )
    assert yaml.safe_load(merged.read_text(encoding="utf-8"))["http"]["delay_seconds"] == 2

    pack = tmp_path / "templates.zip"
    _run_success(
        [
            "templates",
            "export-pack",
            "generic/single-page",
            "generic/list-detail",
            "--output",
            str(pack),
        ]
    )
    imported = tmp_path / "imported"
    _run_success(["templates", "import-pack", str(pack), "--target", str(imported)])
    assert list(imported.rglob("*.yaml"))
    _run_success(["templates", "validate", "--include-legacy"])


def test_cli_project_migration_field_and_api_workflows(tmp_path: Path) -> None:
    created = tmp_path / "created"
    _run_success(["init", "demo", "--template", "static_html", "--output", str(created)])
    config = created / "demo.yaml"
    assert config.is_file()

    migrated = tmp_path / "migrated.yaml"
    _run_success(["migrate", "--config", str(config), "--output", str(migrated)])
    assert yaml.safe_load(migrated.read_text(encoding="utf-8"))["config_version"] == 5

    html = tmp_path / "sample.html"
    html.write_text(
        "<html><head><title>Demo</title></head><body><h1>Heading</h1><time>2026-01-01</time></body></html>",
        encoding="utf-8",
    )
    fields = tmp_path / "fields.yaml"
    _run_success(["field-suggest", str(html), "--output", str(fields)])
    assert "fields" in yaml.safe_load(fields.read_text(encoding="utf-8"))
    _run_success(["field-suggest", str(html), "--limit", "3"])

    capture = tmp_path / "capture.json"
    capture.write_text(
        json.dumps(
            {
                "api_responses": [
                    {
                        "url": "https://example.org/api/items?page=1",
                        "method": "GET",
                        "status": 200,
                        "content_type": "application/json",
                        "json": {"items": [{"id": 1, "title": "One"}]},
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    discovery = tmp_path / "discovery"
    _run_success(["api-discover", str(capture), "--output", str(discovery)])
    assert list(discovery.rglob("*"))

    invalid = tmp_path / "invalid-capture.json"
    invalid.write_text('{"api_responses": "not-list"}', encoding="utf-8")
    with pytest.raises(SystemExit) as failed:
        cli.main(["api-discover", str(invalid), "--output", str(tmp_path / "bad")])
    assert failed.value.code == 1


def test_cli_local_status_control_schedule_and_quality_commands(tmp_path: Path) -> None:
    config = _config(tmp_path)
    _run_success(["validate", "--config", str(config)])
    _run_success(["status", "--config", str(config)])
    _run_success(["control", "--config", str(config), "status"])
    _run_success(["control", "--config", str(config), "pause"])
    _run_success(["control", "--config", str(config), "resume"])
    _run_success(["control", "--config", str(config), "stop"])
    _run_success(["cleanup", "--config", str(config)])
    _run_success(["plugins"])

    database = tmp_path / "schedules.sqlite3"
    _run_success(
        [
            "schedule",
            "--database",
            str(database),
            "add",
            "--config",
            str(config),
            "--name",
            "daily",
            "--every-seconds",
            "86400",
            "--require-network",
        ]
    )
    _run_success(["schedule", "--database", str(database), "list"])

    _run_success(["capabilities", "--portable-paths"])


def test_cli_helpers_and_wizard(tmp_path: Path, monkeypatch) -> None:
    assert cli._key_values(["A=1", "B=two=parts"], "=") == {"A": "1", "B": "two=parts"}
    with pytest.raises(ValueError):
        cli._key_values(["missing"], "=")
    with pytest.raises(ValueError):
        cli._key_values(["=empty"], "=")

    answers = iter(["wizard-demo", "crawl", "https://example.org", "12", "owner@example.org"])
    monkeypatch.setattr("builtins.input", lambda _prompt: next(answers))
    output = tmp_path / "wizard.yaml"
    cli._wizard(output)
    generated = yaml.safe_load(output.read_text(encoding="utf-8"))
    assert generated["project"]["name"] == "wizard-demo"
    assert generated["crawl"]["max_pages"] == 12

    answers = iter(["demo", "crawl", "", "", ""])
    monkeypatch.setattr("builtins.input", lambda _prompt: next(answers))
    with pytest.raises(ValueError):
        cli._wizard(tmp_path / "missing-seed.yaml")
