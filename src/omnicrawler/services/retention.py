from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from ..core.config import AppConfig
from ..core.utils import utcnow

# B-P2-20：数据库记录级留存规则。列名取自 state schema；days 取 storage.retention 下同名字段。
_DB_RETENTION_RULES: tuple[dict[str, str], ...] = (
    {"category": "records", "table": "records", "timestamp_column": "created_at",
     "setting": "records_days"},
    {"category": "evidence", "table": "entity_observations", "timestamp_column": "observed_at",
     "setting": "evidence_days"},
    {"category": "evidence", "table": "semantic_changes", "timestamp_column": "created_at",
     "setting": "evidence_days"},
)


@dataclass(frozen=True, slots=True)
class RetentionCandidate:
    category: str
    path: Path
    age_days: float
    size_bytes: int


def plan_retention(config: AppConfig, *, now: float | None = None) -> list[RetentionCandidate]:
    now = time.time() if now is None else now
    settings = config.section("storage").get("retention", {})
    if not isinstance(settings, dict):
        return []
    policies = {
        "raw": settings.get("raw_days"),
        "artifacts": settings.get("artifacts_days"),
        "diagnostics": settings.get("diagnostics_days"),
    }
    workspace = config.workspace.resolve()
    candidates: list[RetentionCandidate] = []
    for category, days_value in policies.items():
        if days_value is None:
            continue
        days = float(days_value)
        if days < 0:
            raise ValueError(f"storage.retention.{category}_days cannot be negative")
        directory = (workspace / category).resolve()
        if workspace not in directory.parents or not directory.is_dir():
            continue
        for path in directory.rglob("*"):
            if not path.is_file() or path.is_symlink():
                continue
            resolved = path.resolve()
            if workspace not in resolved.parents:
                continue
            stat = resolved.stat()
            age_days = (now - stat.st_mtime) / 86400
            if age_days >= days:
                candidates.append(RetentionCandidate(category, resolved, age_days, stat.st_size))
    return sorted(candidates, key=lambda item: (item.category, str(item.path)))


def apply_retention(config: AppConfig, candidates: list[RetentionCandidate]) -> dict[str, Any]:
    workspace = config.workspace.resolve()
    deleted: list[dict[str, Any]] = []
    for candidate in candidates:
        resolved = candidate.path.resolve()
        if workspace not in resolved.parents or not resolved.is_file() or resolved.is_symlink():
            raise ValueError(f"Retention target is not a safe workspace file: {resolved}")
        resolved.unlink()
        deleted.append({
            "category": candidate.category,
            "path": str(resolved),
            "age_days": round(candidate.age_days, 2),
            "size_bytes": candidate.size_bytes,
        })
    audit_path = workspace / "output" / "retention_audit.jsonl"
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    event = {"created_at": utcnow(), "deleted": deleted, "total_bytes": sum(item["size_bytes"] for item in deleted)}
    with audit_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, ensure_ascii=False) + "\n")
    return {**event, "audit_path": str(audit_path)}


def serialize_plan(candidates: list[RetentionCandidate]) -> list[dict[str, Any]]:
    return [
        {**asdict(item), "path": str(item.path), "age_days": round(item.age_days, 2)}
        for item in candidates
    ]


# ── B-P2-20：数据库记录级留存（records / evidence 观测与变更证据按 created_at 到期清理）─────


@dataclass(frozen=True, slots=True)
class DatabaseRetentionPlan:
    category: str
    table: str
    timestamp_column: str
    days: float
    cutoff: str
    estimated_rows: int


def _db_cutoff(days: float, now: float) -> str:
    return (datetime.fromtimestamp(now, tz=UTC) - timedelta(days=days)).isoformat(timespec="microseconds")


def plan_database_retention(config: AppConfig, *, now: float | None = None) -> list[DatabaseRetentionPlan]:
    """扫描 ``state.sqlite3`` 中到期记录的库内留存计划（不删除，供 dry-run 展示）。"""
    settings = config.section("storage").get("retention", {})
    if not isinstance(settings, dict):
        return []
    db_path = config.workspace / "state.sqlite3"
    if not db_path.is_file():
        return []
    now = time.time() if now is None else now
    plans: list[DatabaseRetentionPlan] = []
    connection = sqlite3.connect(str(db_path), timeout=5.0)
    try:
        for rule in _DB_RETENTION_RULES:
            raw_days = settings.get(rule["setting"])
            if raw_days is None:
                continue
            days = float(raw_days)
            if days < 0:
                raise ValueError(f"storage.retention.{rule['setting']} cannot be negative")
            cutoff = _db_cutoff(days, now)
            try:
                cursor = connection.execute(
                    f"SELECT COUNT(*) FROM {rule['table']} WHERE {rule['timestamp_column']} < ?",
                    (cutoff,),
                )
                estimated_rows = int(cursor.fetchone()[0])
            except sqlite3.OperationalError:
                # 旧版库缺对应表时跳过该规则，不阻断目录留存。
                continue
            plans.append(DatabaseRetentionPlan(
                rule["category"], rule["table"], rule["timestamp_column"],
                days, cutoff, estimated_rows,
            ))
    finally:
        connection.close()
    return plans


def apply_database_retention(config: AppConfig, plans: list[DatabaseRetentionPlan]) -> dict[str, Any]:
    """按计划删除到期库内记录，并把删除明细追加进同一留存审计日志。"""
    db_path = config.workspace / "state.sqlite3"
    deleted: list[dict[str, Any]] = []
    removed_rows = 0
    if db_path.is_file():
        connection = sqlite3.connect(str(db_path), timeout=5.0)
        try:
            connection.execute("PRAGMA foreign_keys=ON")
            for plan in plans:
                cursor = connection.execute(
                    f"DELETE FROM {plan.table} WHERE {plan.timestamp_column} < ?",
                    (plan.cutoff,),
                )
                removed_rows += cursor.rowcount
                deleted.append({"category": plan.category, "table": plan.table, "rows": cursor.rowcount})
            connection.commit()
        finally:
            connection.close()
    audit_path = config.workspace.resolve() / "output" / "retention_audit.jsonl"
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    event = {"created_at": utcnow(), "database": {"deleted": deleted, "rows": removed_rows}}
    with audit_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, ensure_ascii=False) + "\n")
    return {"deleted": deleted, "rows": removed_rows, "audit_path": str(audit_path)}
