"""B-P2-20：数据库记录级留存——按 created_at/observed_at 到期清理 records/evidence，
计划阶段只统计不删除；应用阶段删除到期行并写同一留存审计日志。
"""

from __future__ import annotations

import copy
import sqlite3
from pathlib import Path

from omnicrawler.core.config import DEFAULTS, AppConfig
from omnicrawler.core.utils import utcnow
from omnicrawler.services.retention import apply_database_retention, plan_database_retention

_OLD = "2000-01-01T00:00:00+00:00"

_SCHEMA = """
CREATE TABLE records(
    record_id TEXT PRIMARY KEY, run_id TEXT, request_fingerprint TEXT, source_url TEXT,
    record_type TEXT, data_json TEXT, evidence_json TEXT, created_at TEXT NOT NULL);
CREATE TABLE entity_observations(
    id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT, task_key TEXT, comparison_scope TEXT,
    source_url TEXT, record_type TEXT, identity TEXT, data_json TEXT, observed_at TEXT NOT NULL);
CREATE TABLE semantic_changes(
    id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT, source_url TEXT, record_type TEXT,
    identity TEXT, change_type TEXT, similarity REAL, baseline INTEGER, added_json TEXT,
    removed_json TEXT, modified_json TEXT, before_json TEXT, after_json TEXT, created_at TEXT NOT NULL);
"""


def _config(tmp_path: Path) -> AppConfig:
    raw = copy.deepcopy(DEFAULTS)
    raw["storage"] = {"retention": {"records_days": 30, "evidence_days": 30}}
    return AppConfig(tmp_path / "task.yaml", tmp_path, raw, tmp_path / "work")


def _seed(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(db_path))
    connection.executescript(_SCHEMA)
    connection.execute(
        "INSERT INTO records VALUES ('r-old','run','fp','https://old','item','{}','{}',?)", (_OLD,))
    connection.execute(
        "INSERT INTO records VALUES ('r-new','run','fp','https://new','item','{}','{}',?)", (utcnow(),))
    connection.execute(
        "INSERT INTO entity_observations(run_id,task_key,comparison_scope,source_url,record_type,identity,data_json,observed_at)"
        " VALUES ('run','t','s','https://old','item','id','{}',?)", (_OLD,))
    connection.execute(
        "INSERT INTO semantic_changes(run_id,source_url,record_type,identity,change_type,similarity,baseline,"
        "added_json,removed_json,modified_json,before_json,after_json,created_at)"
        " VALUES ('run','https://old','item','id','removed',0,0,'[]','[]','[]',NULL,NULL,?)", (_OLD,))
    connection.commit()
    connection.close()


def test_plan_counts_expired_rows_without_deleting(tmp_path: Path) -> None:
    config = _config(tmp_path)
    _seed(config.workspace / "state.sqlite3")

    plans = plan_database_retention(config)
    by_table = {plan.table: plan for plan in plans}
    assert by_table["records"].estimated_rows == 1
    assert by_table["entity_observations"].estimated_rows == 1
    assert by_table["semantic_changes"].estimated_rows == 1

    connection = sqlite3.connect(str(config.workspace / "state.sqlite3"))
    assert connection.execute("SELECT COUNT(*) FROM records").fetchone()[0] == 2
    connection.close()


def test_apply_deletes_expired_rows_and_writes_audit(tmp_path: Path) -> None:
    config = _config(tmp_path)
    _seed(config.workspace / "state.sqlite3")

    plans = plan_database_retention(config)
    result = apply_database_retention(config, plans)

    connection = sqlite3.connect(str(config.workspace / "state.sqlite3"))
    remaining = {row[0] for row in connection.execute("SELECT record_id FROM records")}
    connection.close()
    assert remaining == {"r-new"}
    assert result["rows"] == 3

    audit = (config.workspace / "output" / "retention_audit.jsonl").read_text(encoding="utf-8")
    assert "database" in audit
    assert "records" in audit


def test_missing_database_is_a_noop(tmp_path: Path) -> None:
    config = _config(tmp_path)
    plans = plan_database_retention(config)
    assert plans == []
    result = apply_database_retention(config, plans)
    assert result["rows"] == 0
