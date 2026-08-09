"""Privacy classification, export guard and verifiable deletion planning."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..core.utils import utcnow


@dataclass(frozen=True, slots=True)
class SensitiveFinding:
    field: str
    classification: str
    reason: str


_PATTERNS = (
    ("personal", "电子邮箱", re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")),
    ("personal", "手机号码", re.compile(r"^1[3-9]\d{9}$")),
    ("highly_sensitive", "身份证候选", re.compile(r"^\d{17}[\dXx]$")),
)


def detect_sensitive_fields(record: dict[str, Any]) -> tuple[SensitiveFinding, ...]:
    findings = []
    for field, value in record.items():
        text = str(value).strip()
        for classification, reason, pattern in _PATTERNS:
            if pattern.fullmatch(text):
                findings.append(SensitiveFinding(str(field), classification, reason))
                break
    return tuple(findings)


def export_privacy_summary(records: list[dict[str, Any]]) -> dict[str, Any]:
    findings = [finding for record in records for finding in detect_sensitive_fields(record)]
    return {
        "records": len(records), "sensitive_fields": sorted({item.field for item in findings}),
        "classifications": sorted({item.classification for item in findings}),
        "approval_recommended": bool(findings), "redaction_available": True, "watermark_extension": True,
    }


def deletion_manifest(paths: list[Path], workspace: Path, *, categories: dict[str, str] | None = None) -> dict[str, Any]:
    """Plan deletion with hashes; does not delete files or silently remove evidence."""
    root = workspace.resolve()
    items = []
    for path in paths:
        resolved = path.resolve()
        if root not in resolved.parents or not resolved.is_file() or resolved.is_symlink():
            raise ValueError(f"删除目标不在工作区安全范围: {resolved}")
        items.append({
            "path": str(resolved), "category": (categories or {}).get(str(resolved), "derived"),
            "size_bytes": resolved.stat().st_size, "sha256": hashlib.sha256(resolved.read_bytes()).hexdigest(),
        })
    payload = {"created_at": utcnow(), "requires_confirmation": True, "items": items}
    payload["manifest_sha256"] = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    return payload
