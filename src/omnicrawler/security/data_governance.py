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

# A-P3-17：敏感字段名关键词表（归一化后匹配，覆盖大小写 / 下划线 / 连字符 / 空格变体）。
_FIELD_KEYWORDS: dict[str, str] = {
    "email": "personal", "mail": "personal", "phone": "personal", "mobile": "personal",
    "telephone": "personal", "tel": "personal",
    "idcard": "highly_sensitive", "idnumber": "highly_sensitive", "idno": "highly_sensitive",
    "password": "highly_sensitive", "passwd": "highly_sensitive", "pwd": "highly_sensitive",
    "token": "highly_sensitive", "secret": "highly_sensitive",
    "authorization": "highly_sensitive", "auth": "highly_sensitive",
    "cookie": "highly_sensitive", "apikey": "highly_sensitive", "apisecret": "highly_sensitive",
}


def _normalize_field(name: str) -> str:
    """归一化字段名：小写 + 剔除下划线 / 连字符 / 空白，便于变体匹配。"""
    return re.sub(r"[_\-\s]+", "", str(name).casefold())


def _leaf_name(path: str) -> str:
    leaf = path.rsplit(".", 1)[-1]
    return leaf.rsplit("[", 1)[0]


def _match_field_keyword(normalized: str) -> str | None:
    """归一化字段名与关键词双向包含匹配（如 ``Email_Address`` → ``email``）。"""
    for keyword, classification in _FIELD_KEYWORDS.items():
        if keyword in normalized or normalized in keyword:
            return classification
    return None


def _scan_value(path: str, value: Any, findings: list[SensitiveFinding]) -> None:
    """A-P3-17：递归扫描嵌套 dict / list，敏感位置以点分路径记录。"""
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}" if path else str(key)
            _scan_value(child_path, child, findings)
        return
    if isinstance(value, list):
        for index, child in enumerate(value):
            _scan_value(f"{path}[{index}]", child, findings)
        return

    # 字段名变体命中（嵌套 / 列表项同样适用）
    normalized = _normalize_field(_leaf_name(path))
    classification = _match_field_keyword(normalized)
    if classification is not None:
        findings.append(SensitiveFinding(path, classification, f"字段名变体 {normalized}"))
        return

    text = str(value).strip()
    if not text:
        return
    for classification, reason, pattern in _PATTERNS:
        if pattern.fullmatch(text):
            findings.append(SensitiveFinding(path, classification, reason))
            return


def detect_sensitive_fields(record: dict[str, Any]) -> tuple[SensitiveFinding, ...]:
    findings: list[SensitiveFinding] = []
    _scan_value("", record, findings)
    return tuple(findings)


def export_privacy_summary(records: list[dict[str, Any]]) -> dict[str, Any]:
    findings = [finding for record in records for finding in detect_sensitive_fields(record)]
    return {
        "records": len(records), "sensitive_fields": sorted({item.field for item in findings}),
        "classifications": sorted({item.classification for item in findings}),
        "approval_recommended": bool(findings), "redaction_available": True, "watermark_extension": True,
    }


def _manifest_digest(payload: dict[str, Any]) -> str:
    """对除 ``manifest_sha256`` 外的全部字段计算规范化 JSON 的 SHA-256（A-P3-9）。"""
    material = {key: value for key, value in payload.items() if key != "manifest_sha256"}
    canonical = json.dumps(material, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def deletion_manifest(paths: list[Path], workspace: Path, *, categories: dict[str, str] | None = None) -> dict[str, Any]:
    """Plan deletion with hashes; does not delete files or silently remove evidence.

    A-P3-9：先计算除 ``manifest_sha256`` 外全部字段的哈希再回填，避免哈希覆盖
    自身定义字段（自引用）；配套 :func:`verify_manifest` 可离线复核清单未被改写。
    """
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
    payload: dict[str, Any] = {"created_at": utcnow(), "requires_confirmation": True, "items": items}
    payload["manifest_sha256"] = _manifest_digest(payload)
    return payload


def verify_manifest(payload: dict[str, Any]) -> bool:
    """复核删除清单哈希：重算除 ``manifest_sha256`` 外全部字段并与回填值比对。"""
    recorded = payload.get("manifest_sha256")
    if not isinstance(recorded, str) or not recorded:
        return False
    return _manifest_digest(payload) == recorded
