from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from ..core.config import AppConfig
from ..core.utils import utcnow


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
