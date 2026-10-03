from __future__ import annotations

import json
from dataclasses import replace
from types import SimpleNamespace

import pytest
import yaml

from omnicrawler.core.config import load_config
from omnicrawler.quality.auto_apply import AutoApplyPolicy, AutomationTier, classify_tier
from omnicrawler.quality.shadow_repair import ShadowComparison, candidate_rule
from omnicrawler.services import repair_workflow as workflow


def _fixture(tmp_path, count=1):
    config = tmp_path / "task.yaml"
    config.write_text("project: {name: repair, workspace: work}\nsource: {kind: static_html, seeds: [https://example.test/]}\nextract: {item_selector: .card, fields: {title: {selector: .old}}}\nai: {mode: local, privacy: {allow_page_text: true}}\ncustom: {preserved: true}\n", encoding="utf-8")
    candidate = tmp_path / "candidate.json"
    candidate.write_text(json.dumps({"field": "title", "new_rule": ".new", "confidence": 1}), encoding="utf-8")

    def sample(identity, classes):
        return {"sample_id": identity, "url": "https://example.test/",
                "html": f"<!--{identity}-->" + "".join(f'<div class="card"><b class="{classes}">Title {i}</b></div>' for i in range(count)),
                "expected": [{"title": f"Title {i}"} for i in range(count)]}

    bundle = {"format": 1, "training": sample("training", "new"),
              "current": [sample("held-out", "new")], "historical": [sample("history", "old new")]}
    evidence = tmp_path / "evidence.json"
    evidence.write_text(json.dumps(bundle), encoding="utf-8")
    return config, candidate, evidence, bundle


def test_cli_production_preview_apply_and_rollback_preserve_unknown_fields(tmp_path, capsys):
    from omnicrawler.cli import main

    config, candidate, evidence, _bundle = _fixture(tmp_path)
    before = config.read_bytes()
    main(["repair", "preview", "-c", str(config), "--evidence", str(evidence), "--candidate", str(candidate)])
    report = json.loads(capsys.readouterr().out)
    assert report["candidates"][0]["comparison"]["historical_compatible"]
    assert report["candidates"][0]["comparison"]["new_quality"] > report["candidates"][0]["comparison"]["old_quality"]
    assert config.read_bytes() == before
    result = workflow.execute("apply", config_path=config, evidence=evidence, candidate_path=candidate)
    assert result["status"] == "applied" and result["tier"] == 1
    assert yaml.safe_load(config.read_text(encoding="utf-8"))["custom"] == {"preserved": True}
    assert load_config(config).section("extract")["fields"]["title"]["selector"] == ".new"
    assert workflow.execute("rollback", config_path=config)["status"] == "rolled_back"
    assert config.read_bytes() == before
    assert workflow.execute("rollback", config_path=config)["status"] == "no_active_repair"


def test_history_regression_cannot_modify_active_task(tmp_path):
    config, candidate, evidence, bundle = _fixture(tmp_path)
    before = config.read_bytes()
    bundle["historical"][0]["html"] = bundle["historical"][0]["html"].replace('class="old new"', 'class="old"')
    evidence.write_text(json.dumps(bundle), encoding="utf-8")
    result = workflow.execute("apply", config_path=config, evidence=evidence, candidate_path=candidate)
    assert result["status"] == "evidence_rejected"
    assert config.read_bytes() == before


def test_reusing_training_page_as_holdout_is_rejected(tmp_path):
    config, candidate, evidence, bundle = _fixture(tmp_path)
    bundle["current"][0]["html"] = bundle["training"]["html"]
    evidence.write_text(json.dumps(bundle), encoding="utf-8")
    with pytest.raises(ValueError, match="独立"):
        workflow.execute("apply", config_path=config, evidence=evidence, candidate_path=candidate)


def test_llm_application_observes_across_runs_and_rolls_back_regression(tmp_path, monkeypatch):
    config, _candidate_path, evidence, bundle = _fixture(tmp_path, count=14)
    before = config.read_bytes()
    bundle["training"]["html"] += "<p>TRAIN-ONLY-TEXT</p>"
    bundle["current"][0]["html"] += "<p>HELD-OUT-PRIVATE</p>"
    bundle["historical"][0]["html"] += "<p>HISTORY-PRIVATE</p>"
    evidence.write_text(json.dumps(bundle), encoding="utf-8")
    calls = []
    provider = SimpleNamespace(check_content_allowed=lambda *_: None)
    def generate(messages):
        calls.append(messages)
        provider.budget.consume(tokens=20, cost=0)
        assert "TRAIN-ONLY-TEXT" in str(messages)
        assert "HELD-OUT-PRIVATE" not in str(messages) and "HISTORY-PRIVATE" not in str(messages)
        return SimpleNamespace(text=json.dumps({"rule_type": "css", "selector": ".new"}))
    provider.generate = generate
    monkeypatch.setattr(workflow, "build_provider", lambda *args, **kwargs: provider)
    assert workflow.execute("apply", config_path=config, evidence=evidence, generate=True)["tier"] == 2
    assert len(calls) == 1 and provider.budget.maximum_requests == 1
    repeated = workflow.execute("observe", config_path=config, evidence=evidence)
    assert repeated["observations"][0]["observation_rounds"] == 0
    for index in range(3):
        bundle["current"][0]["html"] += f"<!--fresh snapshot {index}-->"
        evidence.write_text(json.dumps(bundle), encoding="utf-8")
        assert workflow.execute("observe", config_path=config, evidence=evidence)["status"] == ("stable" if index == 2 else "observing")
    assert load_config(config).raw["_repair"]["status"] == "stable"
    bundle["current"][0]["html"] = bundle["current"][0]["html"].replace('class="new"', 'class="missing"')
    evidence.write_text(json.dumps(bundle), encoding="utf-8")
    assert workflow.execute("observe", config_path=config, evidence=evidence)["status"] == "observing"
    repeated = workflow.execute("observe", config_path=config, evidence=evidence)
    assert repeated["observations"][0]["regression_count"] == 1
    bundle["current"][0]["html"] += "<!--new failure snapshot-->"
    evidence.write_text(json.dumps(bundle), encoding="utf-8")
    assert workflow.execute("observe", config_path=config, evidence=evidence)["status"] == "rolled_back"
    assert config.read_bytes() == before


def test_interrupted_publication_is_recoverable_and_manual_edits_are_not_overwritten(tmp_path, monkeypatch):
    config, candidate, evidence, _bundle = _fixture(tmp_path)
    before = config.read_bytes()
    monkeypatch.setattr(workflow, "_publish", lambda *args: (_ for _ in ()).throw(OSError("injected disk failure")))
    with pytest.raises(OSError, match="injected"):
        workflow.execute("apply", config_path=config, evidence=evidence, candidate_path=candidate)
    with pytest.raises(ValueError, match="未完整提交"):
        workflow.execute("apply", config_path=config, evidence=evidence, candidate_path=candidate)
    config.write_bytes(before + b"crawl: {max_pages: 2}\n")
    with pytest.raises(ValueError, match="不能自动覆盖"):
        workflow.execute("rollback", config_path=config)
    config.write_bytes(before)
    assert workflow.execute("rollback", config_path=config)["status"] == "rolled_back"


def test_disabling_llm_blocks_persisted_stable_llm_candidate():
    candidate = replace(candidate_rule("title", "css", "old", "new", ("one",)), origin="llm", observation_rounds=9)
    comparison = ShadowComparison(1, 1, .1, .9, 0, True)
    assert classify_tier(candidate, comparison, AutoApplyPolicy(llm_enabled=False)) is AutomationTier.L0
