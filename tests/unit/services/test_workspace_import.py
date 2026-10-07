import hashlib
import json
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from omnicrawler.commands.workspace import execute
from omnicrawler.core.config import load_config
from omnicrawler.core.models import CrawlRequest, FetchResult
from omnicrawler.services import workspace_import
from omnicrawler.services.workspace import WorkspaceManager
from omnicrawler.state import StateStore


def _package(tmp_path):
    config = tmp_path / "任务.yaml"
    config.write_text("project: {name: movable, task_id: stable, workspace: work}\n"
                      "source: {seeds: [https://example.org]}\n", encoding="utf8")
    manager = WorkspaceManager(load_config(config))
    manager.initialize()
    artifact = manager.root / "attachments" / "证据 文件.txt"
    payload = b"verified artifact"
    artifact.write_bytes(payload)
    (manager.root / "output" / "historic.csv").write_text("id,price\n1,0\n")
    with StateStore(manager.root / "state.sqlite3") as state:
        run = state.start_run("movable", str(config), task_id="stable")
        state.save_artifact(run, FetchResult(CrawlRequest("https://example.org"), "https://example.org", 200,
                                           {"content-type": "text/plain"}, payload, 0.01), artifact)
        state.finish_run(run, "succeeded", {})
    package = tmp_path / "工作区 包.zip"
    manager.package(package, kind="complete")
    return manager, package, run


def test_chinese_spaced_relocation_keeps_task_and_verified_artifact(tmp_path):
    manager, package, run = _package(tmp_path)
    original_db = (manager.root / "state.sqlite3").read_bytes()
    destination = tmp_path / "新 工作区"
    result = execute("", "import", target=str(package), destination=str(destination))
    assert not result["review_required"] and result["exports_included"]
    config = load_config(destination / "config.yaml")
    assert config.workspace == destination / "workspace"
    assert config.section("project")["task_id"] == "stable"
    with StateStore(config.workspace / "state.sqlite3") as state:
        row = state.rows("SELECT local_path,sha256 FROM artifacts WHERE run_id=?", (run,))[0]
        artifact = Path(row["local_path"])
        assert artifact.is_relative_to(config.workspace)
        assert hashlib.sha256(artifact.read_bytes()).hexdigest() == row["sha256"]
        assert state.rows("SELECT task_key FROM run_identities WHERE run_id=?", (run,))[0]["task_key"] == "id:stable"
    assert (config.workspace / "output/historic.csv").is_file()
    assert WorkspaceManager(config).health()["ok"]
    assert (manager.root / "state.sqlite3").read_bytes() == original_db
    with pytest.raises(FileExistsError):
        WorkspaceManager.import_package(package, destination)


@pytest.mark.parametrize("member_suffix", ["证据 文件.txt", "historic.csv"])
def test_tampered_package_does_not_publish_partial_workspace(tmp_path, member_suffix):
    _, package, _ = _package(tmp_path)
    bad = tmp_path / "bad.zip"
    with zipfile.ZipFile(package) as source, zipfile.ZipFile(bad, "w") as archive:
        for name in source.namelist():
            archive.writestr(name, b"tampered" if name.endswith(member_suffix) else source.read(name))
    destination = tmp_path / "never-published"
    with pytest.raises(ValueError, match="哈希校验"):
        WorkspaceManager.import_package(bad, destination)
    assert not destination.exists()
    assert not list(tmp_path.glob(".omnicrawler-import-*"))


def test_db_file_disagreement_rejects_import_even_with_valid_zip_hashes(tmp_path):
    manager, package, _ = _package(tmp_path)
    (manager.root / "attachments/证据 文件.txt").write_bytes(b"changed-after-observation")
    manager.package(package, kind="complete")
    destination = tmp_path / "invalid"
    with pytest.raises(ValueError, match="数据库引用"):
        WorkspaceManager.import_package(package, destination)
    assert not destination.exists()


def test_disk_shortage_and_changed_package_do_not_publish(tmp_path, monkeypatch):
    _, package, _ = _package(tmp_path)
    destination = tmp_path / "not-created"
    with pytest.raises(ValueError, match="包已变化"):
        WorkspaceManager.import_package(package, destination, expected_sha256="0" * 64)
    monkeypatch.setattr(workspace_import.shutil, "disk_usage", lambda _: SimpleNamespace(free=0))
    with pytest.raises(OSError, match="磁盘空间不足"):
        WorkspaceManager.import_package(package, destination)
    assert not destination.exists()


def test_missing_archived_reference_is_visible_and_rebound_away_from_old_files(tmp_path):
    manager, package, run = _package(tmp_path)
    (manager.root / "attachments/证据 文件.txt").unlink()
    manager.package(package, kind="complete")
    destination = tmp_path / "needs-review"
    result = WorkspaceManager.import_package(package, destination)
    assert result["review_required"]
    assert result["unresolved_references"][0]["reason"] == "referenced_file_not_in_package"
    with StateStore(destination / "workspace/state.sqlite3") as state:
        row = state.rows("SELECT local_path FROM artifacts WHERE run_id=?", (run,))[0]
        assert Path(row["local_path"]).is_relative_to(destination)
    report = json.loads((destination / "workspace/relocation.json").read_text(encoding="utf8"))
    assert report["package_sha256"] == result["package_sha256"]


def test_relative_config_files_are_rebound_and_external_files_need_review(tmp_path):
    manager, package, _ = _package(tmp_path)
    query = manager.root / "raw/query.txt"
    query.write_text("local query")
    with manager.config.path.open("a", encoding="utf8") as stream:
        stream.write("download: {output_dir: work/attachments}\n")
    manager = WorkspaceManager(load_config(manager.config.path))
    manager.package(package, kind="complete")
    destination = tmp_path / "relative"
    result = WorkspaceManager.import_package(package, destination)
    assert not result["review_required"]
    config = load_config(destination / "config.yaml")
    assert config.resolve(config.section("download")["output_dir"]) == config.workspace / "attachments"


def test_declared_plugin_paths_relocate_without_loading_plugin_code(tmp_path):
    import yaml
    manager, package, _ = _package(tmp_path)
    source = manager.root / "raw/plugin fixture.txt"
    source.write_text("plugin input", encoding="utf8")
    raw = yaml.safe_load(manager.config.path.read_text(encoding="utf8"))
    raw["custom_plugin"] = {"inputs": [str(source)], "output": str(manager.root / "output/plugin"),
                            "external": "C:/outside/private.txt", "url": "https://example.test/file.txt"}
    raw["plugins"] = {"workspace_paths": [
        {"pointer": "/custom_plugin/inputs/0", "kind": "file"},
        {"pointer": "/custom_plugin/output", "kind": "directory"},
        {"pointer": "/custom_plugin/external", "kind": "file"},
    ]}
    manager.config.path.write_text(yaml.safe_dump(raw), encoding="utf8")
    manager = WorkspaceManager(load_config(manager.config.path))
    manager.package(package, kind="complete")
    destination = tmp_path / "plugin relocation"
    report = WorkspaceManager.import_package(package, destination)
    config = load_config(destination / "config.yaml")
    settings = config.section("custom_plugin")
    assert config.resolve(settings["inputs"][0]).is_relative_to(config.workspace)
    assert config.resolve(settings["inputs"][0]).read_text(encoding="utf8") == "plugin input"
    assert config.resolve(settings["output"]) == config.workspace / "output/plugin"
    assert settings["url"] == raw["custom_plugin"]["url"]
    assert settings["external"] == raw["custom_plugin"]["external"]
    assert report["review_required"]
    assert {"config_field": "/custom_plugin/external", "reason": "external_or_unknown_origin"} in report["unresolved_references"]


@pytest.mark.parametrize("declaration", [
    {"pointer": "/project/root", "kind": "directory"},
    {"pointer": "/plugins/workspace_paths", "kind": "file"},
    {"pointer": "/custom_plugin/inputs/00", "kind": "file"},
    {"pointer": "/custom_plugin/inputs/9", "kind": "file"},
    {"pointer": "/custom_plugin/missing", "kind": "file"},
    {"pointer": "/custom_plugin/inputs", "kind": "file"},
    {"pointer": "/custom_plugin/~2", "kind": "file"},
    {"pointer": "/custom_plugin/inputs/0", "kind": []},
])
def test_invalid_plugin_path_declarations_fail_closed(declaration):
    from omnicrawler.core.workspace_paths import declared_paths
    raw = {"project": {"root": "."}, "custom_plugin": {"inputs": ["work/input"]},
           "plugins": {"workspace_paths": [declaration]}}
    with pytest.raises(ValueError, match="workspace_paths"):
        declared_paths(raw)


def test_pointer_escaping_and_duplicate_bounds():
    from omnicrawler.core.workspace_paths import declared_paths
    item = {"pointer": "/custom_plugin/a~1b~0c", "kind": "file"}
    raw = {"custom_plugin": {"a/b~c": "work/raw.txt"}, "plugins": {"workspace_paths": [item]}}
    node, key, _, _ = declared_paths(raw)[0]
    assert node is raw["custom_plugin"] and key == "a/b~c"
    raw["plugins"]["workspace_paths"] = [item, item]
    with pytest.raises(ValueError, match="重复"):
        declared_paths(raw)
    raw["plugins"]["workspace_paths"] = [item] * 129
    with pytest.raises(ValueError, match="128"):
        declared_paths(raw)
