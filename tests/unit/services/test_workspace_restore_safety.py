import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import time
import zipfile

import pytest

from omnicrawler.core.config import load_config
from omnicrawler.services.workspace import WorkspaceManager
from omnicrawler.state import StateStore


def manager(tmp_path):
    config = tmp_path / "task.yaml"
    config.write_text(f"project: {{name: original, workspace: '{tmp_path / '工作区 data'}'}}\n"
                      "source: {kind: static_html, seeds: [https://example.org/]}\n", encoding="utf-8")
    value = WorkspaceManager(load_config(config))
    value.initialize()
    return value


def test_invalid_snapshot_config_cannot_replace_current_config(tmp_path):
    value = manager(tmp_path)
    original = value.config.path.read_bytes()
    bad = value.root / "snapshots" / "invalid.zip"
    with zipfile.ZipFile(bad, "w") as archive:
        archive.writestr("config.yaml", "project: [invalid")
    with pytest.raises(ValueError):
        value.rollback(bad)
    assert value.config.path.read_bytes() == original


def test_invalid_database_cannot_be_published(tmp_path):
    value = manager(tmp_path)
    database = value.root / "state.sqlite3"
    with StateStore(database):
        pass
    original = value.config.path.read_bytes()
    bad = value.root / "snapshots" / "invalid-db.zip"
    with zipfile.ZipFile(bad, "w") as archive:
        archive.writestr("config.yaml", original)
        archive.writestr("state.sqlite3", b"invalid database")
    with pytest.raises((ValueError, sqlite3.DatabaseError)):
        value.rollback(bad)
    with sqlite3.connect(database) as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_active_state_store_blocks_rollback_before_mutation(tmp_path):
    value = manager(tmp_path)
    database = value.root / "state.sqlite3"
    with StateStore(database):
        pass
    snapshot = value.snapshot("before")
    original = value.config.path.read_bytes()
    changed = original.replace(b"original", b"current")
    value.config.path.write_bytes(changed)
    with StateStore(database):
        with pytest.raises(RuntimeError, match="使用|占用"):
            value.rollback(snapshot)
    assert value.config.path.read_bytes() == changed


def test_full_package_contains_committed_database_without_live_wal(tmp_path):
    value = manager(tmp_path)
    with StateStore(value.root / "state.sqlite3") as state:
        run = state.start_run("test", "test.yaml")
        package = tmp_path / "full.zip"
        value.package(package)
        with zipfile.ZipFile(package) as archive:
            assert not any(name.endswith(("-wal", "-shm", ".lock")) for name in archive.namelist())
            payload = archive.read("project/workspace/state.sqlite3")
    restored = tmp_path / "restored.sqlite3"
    restored.write_bytes(payload)
    with sqlite3.connect(restored) as connection:
        assert connection.execute("SELECT run_id FROM runs").fetchone()[0] == run


def test_interrupted_rollback_restores_original_pair_before_store_open(tmp_path):
    from omnicrawler.core.database_lease import pending_restore_path

    value = manager(tmp_path)
    database = value.root / "state.sqlite3"
    with StateStore(database) as state:
        original_run = state.start_run("original", "test.yaml")
    original_config = value.config.path.read_bytes()
    preserved = value.snapshot("before interrupted rollback")
    value.config.path.write_bytes(original_config.replace(b"original", b"changed"))
    with StateStore(database) as state:
        state.start_run("changed", "test.yaml")
    pending = pending_restore_path(database)
    pending.write_text(json.dumps({"preserved": preserved.name,
                                   "sha256": hashlib.sha256(preserved.read_bytes()).hexdigest()}), encoding="utf-8")
    with pytest.raises(RuntimeError, match="回滚"):
        StateStore(database)
    assert value.health()["ok"]
    assert value.config.path.read_bytes() == original_config
    assert not pending.exists()
    with StateStore(database) as state:
        assert [row["run_id"] for row in state.rows("SELECT run_id FROM runs")] == [original_run]


def test_other_process_lease_blocks_then_releases_after_crash(tmp_path):
    value = manager(tmp_path)
    database = value.root / "state.sqlite3"
    with StateStore(database):
        pass
    snapshot = value.snapshot("original")
    ready = tmp_path / "ready"
    script = """from pathlib import Path
import sys
from omnicrawler.state import StateStore
with StateStore(Path(sys.argv[1])):
    Path(sys.argv[2]).write_text('ready')
    sys.stdin.readline()
"""
    process = subprocess.Popen([sys.executable, "-c", script, str(database), str(ready)],
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    try:
        deadline = time.monotonic() + 10
        while not ready.exists() and time.monotonic() < deadline and process.poll() is None:
            time.sleep(0.01)
        assert ready.exists(), "子进程未成功持有数据库"
        with pytest.raises(RuntimeError, match="使用|占用"):
            value.rollback(snapshot)
    finally:
        process.kill()
        process.communicate(timeout=10)
    assert value.rollback(snapshot)["restored"] == str(snapshot.resolve())


def test_failed_package_keeps_previous_archive(tmp_path, monkeypatch):
    value = manager(tmp_path)
    target = tmp_path / "full.zip"
    target.write_bytes(b"previous archive")

    def fail(archive, name, payload, *args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(zipfile.ZipFile, "writestr", fail)
    with pytest.raises(OSError, match="disk full"):
        value.package(target)
    assert target.read_bytes() == b"previous archive"
