import sqlite3
from contextlib import closing

import pytest

from omnicrawler.state import state_store
from omnicrawler.state.state_store import StateStore


def legacy_database(path):
    with closing(sqlite3.connect(path)) as conn:
        conn.execute("CREATE TABLE preserved (id INTEGER PRIMARY KEY, content TEXT)")
        conn.execute("INSERT INTO preserved VALUES (1, 'original')")
        conn.commit()


def test_legacy_upgrade_creates_consistent_backup_and_is_idempotent(tmp_path):
    path = tmp_path / "state.sqlite"
    legacy_database(path)
    with StateStore(path) as store:
        assert store.conn.execute("PRAGMA user_version").fetchone()[0] == 1
        backup = store.conn.execute("SELECT backup_name FROM schema_migrations").fetchone()[0]
        assert store.conn.execute("SELECT content FROM preserved").fetchone()[0] == "original"
    with closing(sqlite3.connect(tmp_path / backup)) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 0
        assert conn.execute("SELECT content FROM preserved").fetchone()[0] == "original"
    with StateStore(path) as store:
        assert store.conn.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0] == 1
    assert len(list(tmp_path.glob(".state.sqlite.pre-v0-*.sqlite"))) == 1


def test_failed_migration_rolls_back_all_schema_changes(tmp_path, monkeypatch):
    path = tmp_path / "state.sqlite"
    legacy_database(path)
    monkeypatch.setattr(state_store, "SCHEMA", state_store.SCHEMA + "\nINVALID SQL;\n")
    with pytest.raises(sqlite3.Error):
        StateStore(path)
    with closing(sqlite3.connect(path)) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 0
        assert conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall() == [("preserved",)]
        assert conn.execute("SELECT content FROM preserved").fetchone()[0] == "original"


def test_future_database_is_rejected_before_mutation(tmp_path):
    path = tmp_path / "state.sqlite"
    legacy_database(path)
    with closing(sqlite3.connect(path)) as conn:
        conn.execute("PRAGMA user_version=999")
    before = path.read_bytes()
    with pytest.raises(RuntimeError, match="版本"):
        StateStore(path)
    assert path.read_bytes() == before


def test_backup_failure_prevents_upgrade(tmp_path, monkeypatch):
    from omnicrawler.state import migrations

    path = tmp_path / "state.sqlite"
    legacy_database(path)

    def full_disk(*args):
        raise OSError("disk full")

    monkeypatch.setattr(migrations, "_backup", full_disk)
    with pytest.raises(OSError):
        StateStore(path)
    with closing(sqlite3.connect(path)) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 0
        assert conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall() == [("preserved",)]
