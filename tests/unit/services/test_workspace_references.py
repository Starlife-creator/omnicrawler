from pathlib import Path

import pytest
import yaml

from omnicrawler.core.config import load_config
from omnicrawler.services.workspace_references import apply, inspect, preview


def _config(tmp_path):
    path = tmp_path / "task.yaml"
    path.write_text("project: {name: portable, task_id: stable, workspace: work}\n"
                    "source: {seeds: [https://example.org]}\n"
                    "plugins: {workspace_paths: [{pointer: /custom/input, kind: file}, {pointer: /custom/output, kind: directory}]}\n"
                    "custom: {input: missing.txt, output: old-directory}\n"
                    "http: {headers: {Authorization: '${TEST_REFERENCE_SECRET}'}}\n", encoding="utf8")
    return path


def test_copy_and_rebind_preserve_identity_and_secret_references(tmp_path, monkeypatch):
    monkeypatch.setenv("TEST_REFERENCE_SECRET", "NeverSerializeResolvedSecret")
    path = _config(tmp_path)
    original = path.read_bytes()
    source = tmp_path / "外部 文件.txt"
    source.write_bytes(b"verified data")
    assert inspect(path)["references"][0]["exists"] is False
    plan = preview(path, "/custom/input", source, "copy")
    result = apply(path, "/custom/input", source, "copy", binding=plan["binding"])
    repaired = load_config(result["config"])
    assert repaired.section("project")["task_id"] == "stable" and path.read_bytes() == original
    assert Path(repaired.raw["custom"]["input"]).is_relative_to(repaired.workspace)
    assert Path(repaired.raw["custom"]["input"]).read_bytes() == source.read_bytes()
    payload = Path(result["config"]).read_text(encoding="utf8")
    assert "NeverSerializeResolvedSecret" not in payload and "${TEST_REFERENCE_SECRET}" in payload
    directory = tmp_path / "外部目录"
    directory.mkdir()
    (directory / "evidence.txt").write_text("original")
    next_plan = preview(Path(result["config"]), "/custom/output", directory, "rebind")
    rebound = apply(Path(result["config"]), "/custom/output", directory, "rebind", binding=next_plan["binding"])
    assert yaml.safe_load(Path(rebound["config"]).read_text(encoding="utf8"))["custom"]["output"] == str(directory)


def test_preview_rejects_stale_source_and_undeclared_field(tmp_path, monkeypatch):
    monkeypatch.setenv("TEST_REFERENCE_SECRET", "dummy")
    path = _config(tmp_path)
    source = tmp_path / "new.txt"
    source.write_text("before")
    plan = preview(path, "/custom/input", source, "copy")
    source.write_text("after")
    with pytest.raises(ValueError, match="重新预览"):
        apply(path, "/custom/input", source, "copy", binding=plan["binding"])
    assert list(tmp_path.glob("*.repaired-*.yaml")) == []
    with pytest.raises(ValueError, match="已声明"):
        preview(path, "/http/headers/Authorization", source, "copy")
