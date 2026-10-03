from __future__ import annotations

import json

import pytest
import yaml

from omnicrawler.core.config import load_config
from omnicrawler.templates.capture import capture, trial_reference
from omnicrawler.templates.template_catalog import bundled_template_catalog


def _fixture(tmp_path):
    path = tmp_path / "task.yaml"
    path.write_text("project: {name: demo, workspace: work}\nsource: {kind: static_html, seeds: ['https://example.test/?token=SEED-SECRET']}\ncrawl: {max_pages: 5}\nhttp: {headers: {X-Custom-Key: HEADER-SECRET}, proxy: ''}\nai: {providers: {local: {api_key: AI-SECRET}}}\ncustom: {unrecognized: UNKNOWN-SECRET}\negress: {allowed_domains: [example.test], maximum_requests: 20}\nextract: {mode: fields, item_selector: .card, fields: {title: {selector: .title, type: text}}}\n", encoding="utf-8")
    config = load_config(path)
    proof = tmp_path / "acceptance.json"
    result = {"run_id": "trial_1", "status": "succeeded", "processed": 1, "records": 1, "failed": 0,
              "plugins": {"plugins": ["demo@1.0.0"]}}
    reference = trial_reference(config, result, [{"request_fingerprint": "a" * 64,
        "content_sha256": "b" * 64, "fetched_at": "2026-10-03T10:00:00Z"}], [{"name": "ocr", "version": "1.0.0"}])
    proof.write_text(json.dumps(reference), encoding="utf-8")
    return config, proof, tmp_path / "templates" / "demo.yaml"


def test_actual_cli_capture_render_validation_round_trip_no_secrets(tmp_path, monkeypatch, capsys):
    from omnicrawler.cli import main

    config, proof, output = _fixture(tmp_path)
    monkeypatch.chdir(tmp_path)
    main(["templates", "capture", "user/demo", "-c", str(config.path), "--acceptance", str(proof), "-o", str(output)])
    assert json.loads(capsys.readouterr().out)["historical_reference_only"]
    payload = output.read_text(encoding="utf-8")
    for secret in ("SEED-SECRET", "HEADER-SECRET", "AI-SECRET", "UNKNOWN-SECRET"):
        assert secret not in payload
    data = yaml.safe_load(payload)
    assert data["template"]["acceptance_reference"]["versions"]["components"] == [{"name": "ocr", "version": "1.0.0"}]
    assert data["template"]["acceptance_reference"]["historical_reference_only"]
    assert "custom" not in data and "session" not in data
    assert data["egress"]["allowed_domains"] == ["example.test"]
    target = tmp_path / "new.yaml"
    main(["templates", "render", "user/demo", "--set", "seed_url_1=https://example.test/new", "-o", str(target)])
    capsys.readouterr()
    rendered = load_config(target)
    assert rendered.section("source")["seeds"] == ["https://example.test/new"]
    assert rendered.section("project")["template_binding"]["historical_reference_only"]
    assert "trial_passed" not in str(rendered.raw)
    assert "custom" in config.raw  # original local configuration survives capture


def test_capture_requires_matching_trial_not_old_or_changed_config(tmp_path):
    config, proof, output = _fixture(tmp_path)
    config.raw["extract"]["fields"]["title"]["selector"] = ".new"
    with pytest.raises(ValueError, match="绑定"):
        capture(config, proof, output, template_id="user/demo")
    assert not output.exists()


def test_changed_component_versions_reject_capture(tmp_path):
    config, proof, output = _fixture(tmp_path)
    reference = json.loads(proof.read_text(encoding="utf-8"))
    reference["versions"]["components_consistent"] = False
    proof.write_text(json.dumps(reference), encoding="utf-8")
    with pytest.raises(ValueError, match="组件版本"):
        capture(config, proof, output, template_id="user/demo")
    assert not output.exists()


def test_typed_capture_parameters_and_missing_value_fail(tmp_path):
    config, proof, output = _fixture(tmp_path)
    parameters = tmp_path / "parameters.json"
    parameters.write_text(json.dumps({"pages": {"path": "crawl.max_pages", "type": "integer", "required": True,
        "minimum": 1, "maximum": 10, "default": 5}}), encoding="utf-8")
    capture(config, proof, output, template_id="user/demo", parameter_path=parameters)
    catalog = bundled_template_catalog([output.parent])
    with pytest.raises(ValueError, match="Missing"):
        catalog.render("user/demo", {})
    assert catalog.render("user/demo", {"seed_url_1": "https://example.test/new", "pages": "7"})["crawl"]["max_pages"] == 7
    with pytest.raises(ValueError, match="range"):
        catalog.render("user/demo", {"seed_url_1": "https://example.test/new", "pages": "11"})


def test_failed_empty_trial_and_unsafe_parameter_rejected(tmp_path):
    config, proof, output = _fixture(tmp_path)
    parameters = tmp_path / "parameters.json"
    parameters.write_text(json.dumps({"credential": {"path": "http.headers.X-Custom-Key", "type": "string"}}), encoding="utf-8")
    with pytest.raises(ValueError, match="可分享"):
        capture(config, proof, output, template_id="user/demo", parameter_path=parameters)
    reference = json.loads(proof.read_text(encoding="utf-8"))
    reference["samples"] = []
    proof.write_text(json.dumps(reference), encoding="utf-8")
    with pytest.raises(ValueError, match="快照"):
        capture(config, proof, output, template_id="user/demo")


def test_capture_overwrite_retains_history(tmp_path):
    config, proof, output = _fixture(tmp_path)
    capture(config, proof, output, template_id="user/demo")
    original = output.read_bytes()
    with pytest.raises(FileExistsError):
        capture(config, proof, output, template_id="user/updated")
    assert output.read_bytes() == original
    capture(config, proof, output, template_id="user/updated", force=True)
    assert any(path.read_bytes() == original for path in (config.root / ".config_history").rglob("*.yaml"))


def test_sample_entry_writes_reference_from_current_response_rows(tmp_path, monkeypatch):
    import sqlite3
    from types import SimpleNamespace

    from omnicrawler.pipeline_ops.preflight import run_sample

    config, _proof, _output = _fixture(tmp_path)
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE responses (id INTEGER, run_id TEXT, request_fingerprint TEXT, content_sha256 TEXT, fetched_at TEXT)")
    conn.execute("INSERT INTO responses VALUES (1, 'old_run', ?, ?, 'old')", ("c" * 64, "d" * 64))
    conn.execute("INSERT INTO responses VALUES (2, 'current_run', ?, ?, 'now')", ("a" * 64, "b" * 64))

    class SamplePipeline:
        def __init__(self, cfg):
            self.state = SimpleNamespace(conn=conn)
            assert cfg.workspace != config.workspace
        def __enter__(self):
            return self
        def __exit__(self, *_):
            pass
        def run(self, **_):
            return {"run_id": "current_run", "status": "succeeded", "processed": 1, "records": 1}

    monkeypatch.setattr("omnicrawler.pipeline.Pipeline", SamplePipeline)
    monkeypatch.setattr("omnicrawler.core.runtime_paths.portable_data_root", lambda: tmp_path / "portable")
    try:
        result = run_sample(config, pages=1)
        reference = json.loads(__import__("pathlib").Path(result["acceptance_reference"]).read_text(encoding="utf-8"))
        assert reference["samples"] == [{"request_fingerprint": "a" * 64, "content_sha256": "b" * 64, "fetched_at": "now"}]
        assert reference["historical_reference_only"]
    finally:
        conn.close()
