from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from omnicrawler.services import archive_analysis
from omnicrawler.services.task_tools import TaskAction, execute, repair_binding


def _config(tmp_path):
    path = tmp_path / "task.yaml"
    path.write_text("project: {name: tools, workspace: work}\nsource: {kind: static_html, seeds: [https://example.test/]}\nextract: {item_selector: .card, fields: {title: {selector: .old}}}\n", encoding="utf-8")
    return path


def _action(path, name, **arguments):
    return TaskAction(name, path, hashlib.sha256(path.read_bytes()).hexdigest(), arguments)


def test_saved_config_change_rejected_before_dispatch(tmp_path, monkeypatch):
    config = _config(tmp_path)
    action = _action(config, "failures")
    config.write_bytes(config.read_bytes() + b"# edited\n")
    from omnicrawler.commands import recovery
    monkeypatch.setattr(recovery, "execute", lambda *a, **kw: pytest.fail("changed config dispatched"))
    with pytest.raises(ValueError, match="配置已变化"):
        execute(action)


def test_selected_analysis_never_reads_unselected_content(tmp_path, monkeypatch):
    config = _config(tmp_path)
    selected = tmp_path / "chosen.txt"
    selected.write_text("Delivery title.\n\nObserved delivery.", encoding="utf-8")
    manifest = tmp_path / "manifest.json"
    # The unselected path and digest are intentionally invalid. Selection must
    # happen before file reads, parsing and optional AI delivery.
    manifest.write_text(json.dumps({"format": 1, "sources": [
        {"id": "chosen", "path": selected.name, "sha256": hashlib.sha256(selected.read_bytes()).hexdigest()},
        {"id": "private", "path": "PRIVATE-NOT-READ.txt", "sha256": "invalid"},
    ]}), encoding="utf-8")
    listed = execute(_action(config, "sources", manifest=str(manifest)))
    monkeypatch.setattr(archive_analysis, "build_provider", lambda *a, **kw: pytest.fail("implicit AI"))
    arguments = {"manifest": str(manifest), "manifest_sha256": listed["manifest_sha256"],
                 "output": str(tmp_path / "report"), "selected_ids": ["chosen"]}
    result = execute(_action(config, "analyze", **arguments))
    assert result["status"] == "completed_local"
    report = json.loads(Path(result["report"]).read_text(encoding="utf-8"))
    assert [document["id"] for document in report["documents"]] == ["chosen"]
    assert "PRIVATE-NOT-READ" not in str(report)
    for ids in ([], None, ["unknown"], ["chosen", "chosen"]):
        with pytest.raises(ValueError, match="选择|选定"):
            execute(_action(config, "analyze", **{**arguments, "selected_ids": ids}))
    manifest.write_bytes(manifest.read_bytes() + b"\n")
    with pytest.raises(ValueError, match="清单已变化"):
        execute(_action(config, "analyze", **arguments))


def test_verified_download_jsonl_reuses_archive_checks(tmp_path):
    source = tmp_path / "paper.txt"
    source.write_text("Delivery title.\n\nDownloaded evidence.", encoding="utf-8")
    manifest = tmp_path / "source_manifest.jsonl"
    row = {"verification": "verified_file", "file_path": str(source.resolve()),
           "sha256": hashlib.sha256(source.read_bytes()).hexdigest(), "source_url": "https://example.test/paper"}
    manifest.write_text(json.dumps(row) + "\n", encoding="utf-8")
    listed = archive_analysis.read_sources(manifest)
    result = archive_analysis.execute(manifest, tmp_path / "report", selected_ids=[listed[0]["id"]])
    assert result["status"] == "completed_local"
    source.write_text("tampered", encoding="utf-8")
    with pytest.raises(ValueError, match="哈希"):
        archive_analysis.execute(manifest, tmp_path / "report", selected_ids=[listed[0]["id"]])
    row["file_path"] = str(tmp_path.parent / "outside.txt")
    manifest.write_text(json.dumps(row), encoding="utf-8")
    with pytest.raises(ValueError, match="越出"):
        archive_analysis.read_sources(manifest)


@pytest.mark.parametrize("args", [{}, {"fingerprints": []}, {"fingerprints": ["one"]}, {"fingerprints": ["one"], "confirmed": False}])
def test_retry_requires_explicit_nonempty_confirmation(tmp_path, args):
    with pytest.raises(ValueError, match="选择"):
        execute(_action(_config(tmp_path), "retry", **args))


def test_retry_forwards_only_selected_fingerprints(tmp_path, monkeypatch):
    from omnicrawler.commands import recovery
    calls = []
    monkeypatch.setattr(recovery, "execute", lambda *a, **kw: calls.append((a, kw)) or {"retried": 1})
    config = _config(tmp_path)
    assert execute(_action(config, "retry", fingerprints=["chosen"], confirmed=True))["retried"] == 1
    assert calls == [((str(config), "retry-failed"), {"fingerprints": ["chosen"]})]


def test_reviewed_repair_preview_apply_rollback_and_changed_candidate(tmp_path):
    config = _config(tmp_path)
    before = config.read_bytes()
    candidate = tmp_path / "candidate.json"
    candidate.write_text(json.dumps({"field": "title", "new_rule": ".new"}), encoding="utf-8")
    def sample(identity, classes):
        return {"sample_id": identity, "url": "https://example.test/", "html":
                f'<!--{identity}--><div class="card"><b class="{classes}">Title</b></div>', "expected": [{"title": "Title"}]}
    evidence = tmp_path / "evidence.json"
    evidence.write_text(json.dumps({"format": 1, "training": sample("train", "new"),
        "current": [sample("holdout", "new")], "historical": [sample("history", "old new")]}), encoding="utf-8")
    binding = repair_binding(evidence, candidate)[0]
    args = {"evidence": str(evidence), "candidate": str(candidate), "preview_binding": binding}
    preview = execute(_action(config, "repair:preview", **args))
    assert preview["candidates"][0]["comparison"]["improves_safely"] is True
    assert preview["preview_binding"] == binding and config.read_bytes() == before
    candidate_before = candidate.read_bytes()
    candidate.write_bytes(candidate_before + b"\n")
    with pytest.raises(ValueError, match="重新预览"):
        execute(_action(config, "repair:apply", **args, confirmed=True))
    assert config.read_bytes() == before
    candidate.write_bytes(candidate_before)
    with pytest.raises(ValueError, match="确认"):
        execute(_action(config, "repair:apply", **args))
    assert execute(_action(config, "repair:apply", **args, confirmed=True))["status"] == "applied"
    assert execute(_action(config, "repair:rollback", confirmed=True))["status"] == "rolled_back"
    assert config.read_bytes() == before
