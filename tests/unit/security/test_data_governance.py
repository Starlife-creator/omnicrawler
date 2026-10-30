"""A-P3-9 清单哈希自引用修复 + A-P3-17 嵌套 / 变体敏感字段扫描回归测试。"""
from __future__ import annotations

from pathlib import Path

import pytest

from omnicrawler.security.data_governance import (
    deletion_manifest,
    detect_sensitive_fields,
    verify_manifest,
)


def test_manifest_hash_excludes_own_field_and_verifies(tmp_path: Path) -> None:
    target = tmp_path / "evidence.txt"
    target.write_text("payload", encoding="utf-8")
    manifest = deletion_manifest([target], tmp_path)

    assert "manifest_sha256" in manifest
    assert len(manifest["manifest_sha256"]) == 64
    assert verify_manifest(manifest) is True


def test_manifest_detects_tampered_items(tmp_path: Path) -> None:
    target = tmp_path / "evidence.txt"
    target.write_text("payload", encoding="utf-8")
    manifest = deletion_manifest([target], tmp_path)

    manifest["items"][0]["size_bytes"] = 10**9
    assert verify_manifest(manifest) is False


def test_manifest_detects_tampered_created_at(tmp_path: Path) -> None:
    target = tmp_path / "evidence.txt"
    target.write_text("payload", encoding="utf-8")
    manifest = deletion_manifest([target], tmp_path)

    manifest["created_at"] = "2000-01-01T00:00:00+00:00"
    assert verify_manifest(manifest) is False


def test_deletion_manifest_rejects_outside_workspace(tmp_path: Path) -> None:
    outside = Path("/tmp/omni-a-outside-evidence.txt")
    outside.write_text("x", encoding="utf-8")
    with pytest.raises(ValueError, match="工作区安全范围"):
        deletion_manifest([outside], tmp_path)


def test_detect_scans_nested_dict_and_list_paths() -> None:
    record = {
        "title": "ok",
        "contact": {"email": "user@example.com", "backup_phone": "13800138000"},
        "tags": [{"note": "plain"}],
    }
    findings = {item.field: item for item in detect_sensitive_fields(record)}
    assert "contact.email" in findings
    assert "contact.backup_phone" in findings
    # 非敏感嵌套路径不误报
    assert "title" not in findings
    assert "tags[0].note" not in findings


def test_detect_normalizes_case_and_underscore_variants() -> None:
    record = {
        "Email_Address": "plain",
        "API-Key": "plain",
        "password_hash": "plain",
        "User_Token": "plain",
    }
    findings = {item.field: item.classification for item in detect_sensitive_fields(record)}
    assert findings["Email_Address"] == "personal"
    assert findings["API-Key"] == "highly_sensitive"
    assert findings["password_hash"] == "highly_sensitive"
    assert findings["User_Token"] == "highly_sensitive"


def test_detect_keeps_scalar_value_patterns() -> None:
    # 字段名非敏感时按值模式识别（邮箱 / 身份证候选）。
    record = {"contact": "boss@example.org", "national_id": "110101199003077758"}
    findings = {item.field: item.reason for item in detect_sensitive_fields(record)}
    assert findings["contact"] == "电子邮箱"
    assert findings["national_id"] == "身份证候选"
