from __future__ import annotations

import hashlib
import json
from types import SimpleNamespace

import pytest

from omnicrawler.services import archive_analysis as analysis


def _inputs(tmp_path):
    source = tmp_path / "selected.txt"
    source.write_text("Observed price: 12.\n\nObserved date: Monday.", encoding="utf-8")
    (tmp_path / "unselected.txt").write_text("PRIVATE-UNSELECTED", encoding="utf-8")
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"format": 1, "sources": [{"id": "one", "path": source.name,
        "sha256": hashlib.sha256(source.read_bytes()).hexdigest(), "source_url": "https://example.test/item"}]}), encoding="utf-8")
    return source, manifest, tmp_path / "report"


def test_cli_ai_configuration_is_explicit_and_separate_from_task_config(monkeypatch, capsys):
    from pathlib import Path

    from omnicrawler.cli import main

    calls = []
    monkeypatch.setattr(analysis, "execute", lambda *args, **kwargs: calls.append((args, kwargs)) or {"status": "review"})
    main(["analyze-archive", "--manifest", "manifest.json", "-o", "report", "--ai", "--ai-config", "model.yaml"])
    assert json.loads(capsys.readouterr().out)["status"] == "review"
    assert calls[0][1] == {"config_path": Path("model.yaml"), "use_ai": True}


def test_cli_local_report_has_stable_references_and_no_provider(tmp_path, monkeypatch, capsys):
    from omnicrawler.cli import main

    source, manifest, output = _inputs(tmp_path)
    monkeypatch.setattr(analysis, "build_provider", lambda *_args, **_kwargs: pytest.fail("local report contacted AI"))
    main(["analyze-archive", "--manifest", str(manifest), "-o", str(output)])
    assert json.loads(capsys.readouterr().out)["status"] == "completed_local"
    report = json.loads((output / "analysis.json").read_text(encoding="utf-8"))
    assert report["documents"][0]["sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()
    locator = report["evidence"][0]["locator"]
    assert {key: locator[key] for key in ("paragraph", "page")} == {"paragraph": 1, "page": None}
    assert locator["node_id"].startswith("node-")
    assert locator["source_sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()
    assert report["interpretations"] == [] and report["requires_review"]
    assert "PRIVATE-UNSELECTED" not in str(report)
    evidence = report["evidence"]
    monkeypatch.setattr(analysis, "parse_document", lambda *_: pytest.fail("unchanged stage reparsed"))
    analysis.execute(manifest, output)
    assert json.loads((output / "analysis.json").read_text(encoding="utf-8"))["evidence"] == evidence


def test_changed_delivery_rejected_before_cached_stage(tmp_path):
    source, manifest, output = _inputs(tmp_path)
    analysis.execute(manifest, output)
    source.write_text("changed", encoding="utf-8")
    with pytest.raises(ValueError, match="哈希"):
        analysis.execute(manifest, output)


def test_corrupted_stage_is_rebuilt(tmp_path):
    _source, manifest, output = _inputs(tmp_path)
    analysis.execute(manifest, output)
    path = output / "facts.json"
    cached = json.loads(path.read_text(encoding="utf-8"))
    cached["evidence"][0]["quote"] = "invented"
    path.write_text(json.dumps(cached), encoding="utf-8")
    analysis.execute(manifest, output)
    assert "invented" not in (output / "analysis.json").read_text(encoding="utf-8")


def test_model_cannot_invent_quote_and_retry_reuses_facts(tmp_path, monkeypatch):
    _source, manifest, output = _inputs(tmp_path)
    config = tmp_path / "task.yaml"
    config.write_text("project: {name: analysis, workspace: work}\nsource: {kind: static_html, seeds: [https://example.test/]}\nai: {mode: local, privacy: {allow_page_text: true}}\n", encoding="utf-8")
    provider = SimpleNamespace(check_content_allowed=lambda *_: None)
    calls = []
    invented = True

    def generate(messages):
        calls.append(messages)
        assert "PRIVATE-UNSELECTED" not in str(messages)
        facts = json.loads((output / "facts.json").read_text(encoding="utf-8"))
        item = facts["evidence"][0]
        provider.budget.consume(tokens=10, cost=0)
        return SimpleNamespace(text=json.dumps({"interpretations": [{"text": "A price was observed.",
            "uncertainty": "One observation; not a trend.", "citations": [{"evidence_id": item["id"],
            "quote": "invented" if invented else item["quote"]}]}], "suggestions": []}), provider="test", model="test")

    provider.generate = generate
    monkeypatch.setattr(analysis, "build_provider", lambda *_args, **_kwargs: provider)
    assert analysis.execute(manifest, output, config_path=config, use_ai=True)["status"] == "paused"
    assert json.loads((output / "analysis.json").read_text(encoding="utf-8"))["interpretations"] == []
    monkeypatch.setattr(analysis, "parse_document", lambda *_: pytest.fail("retry reparsed facts"))
    invented = False
    assert analysis.execute(manifest, output, config_path=config, use_ai=True)["status"] == "completed_with_interpretations"
    assert len(calls) == 2 and provider.budget.maximum_requests == 1


def test_character_budget_pauses_before_request(tmp_path, monkeypatch):
    _source, manifest, output = _inputs(tmp_path)
    config = tmp_path / "task.yaml"
    config.write_text("project: {name: analysis, workspace: work}\nsource: {kind: static_html, seeds: [https://example.test/]}\nai: {mode: local, privacy: {allow_page_text: true}, budget: {maximum_input_characters: 5}}\n", encoding="utf-8")
    provider = SimpleNamespace(check_content_allowed=lambda *_: None,
        generate=lambda *_: pytest.fail("exceeded character budget"))
    monkeypatch.setattr(analysis, "build_provider", lambda *_args, **_kwargs: provider)
    assert analysis.execute(manifest, output, config_path=config, use_ai=True)["status"] == "paused"
    assert (output / "facts.json").is_file()


def test_manifest_outside_path_rejected(tmp_path):
    _source, manifest, output = _inputs(tmp_path)
    value = json.loads(manifest.read_text(encoding="utf-8"))
    value["sources"][0]["path"] = "../outside.txt"
    manifest.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(ValueError, match="越出"):
        analysis.execute(manifest, output)


def test_bounded_facts_remain_resumable(tmp_path):
    source, manifest, output = _inputs(tmp_path)
    source.write_text("\n\n".join("Long paragraph " + "x" * 1900 for _ in range(500)), encoding="utf-8")
    value = json.loads(manifest.read_text(encoding="utf-8"))
    value["sources"][0]["sha256"] = hashlib.sha256(source.read_bytes()).hexdigest()
    manifest.write_text(json.dumps(value), encoding="utf-8")
    analysis.execute(manifest, output)
    analysis.execute(manifest, output)
    facts = json.loads((output / "facts.json").read_text(encoding="utf-8"))
    assert facts["statistics"]["evidence_characters"] <= 100000
    assert facts["statistics"]["evidence_paragraphs"] < facts["statistics"]["paragraphs"]


def test_each_selected_document_receives_bounded_local_evidence(tmp_path):
    source, manifest, output = _inputs(tmp_path)
    source.write_text("\n\n".join("Paragraph " + "x" * 1900 for _ in range(100)), encoding="utf-8")
    second = tmp_path / "second.txt"
    second.write_text("Second document\n\nIndependent evidence from the second document.", encoding="utf-8")
    value = json.loads(manifest.read_text(encoding="utf-8"))
    value["sources"][0]["sha256"] = hashlib.sha256(source.read_bytes()).hexdigest()
    value["sources"].append({"id": "two", "path": "second.txt", "sha256": hashlib.sha256(second.read_bytes()).hexdigest()})
    manifest.write_text(json.dumps(value), encoding="utf-8")
    analysis.execute(manifest, output)
    facts = json.loads((output / "facts.json").read_text(encoding="utf-8"))
    assert {item["source_id"] for item in facts["evidence"]} == {"one", "two"}
    assert facts["statistics"]["evidence_characters"] <= 100000
