"""S2.2.2：导出前明文凭据扫描（scan_config_text + 工作区包拒绝导出）。

验收：快照/导出无明文凭据落盘；secret:// 引用与 ${VAR} 放行；命中拒绝并提示行号。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from omnicrawl.core.config import load_config
from omnicrawl.security.security_audit import scan_config_text
from omnicrawl.services.workspace import WorkspaceManager


def _config(tmp_path: Path, *, http_headers=None, ai_key: str | None = None) -> Path:
    extra = ""
    if ai_key is not None:
        extra += f"ai: {{mode: cloud, default_provider: openai, providers: {{openai: {{api_key: '{ai_key}'}}}}}}\n"
    elif http_headers is not None:
        extra += f"http: {{headers: {{{http_headers}}}}}\n"
    path = tmp_path / "task.yaml"
    path.write_text(
        f"project: {{name: scan, workspace: '{tmp_path / 'workspace'}'}}\n"
        "source: {kind: static_html, seeds: [https://example.org/]}\n" + extra,
        encoding="utf-8",
    )
    return path


def test_s222_scan_detects_plaintext_api_key() -> None:
    report = scan_config_text("ai:\n  providers:\n    openai:\n      api_key: sk-plain-abc\n")
    assert report["ok"] is False
    assert report["findings"][0]["line"] == 4


def test_s222_scan_allows_secret_reference_and_env_var() -> None:
    clean = "api_key: secret://ai.openai.api_key\n"
    assert scan_config_text(clean)["ok"] is True
    assert scan_config_text("password: ${DB_PASSWORD}\n")["ok"] is True
    assert scan_config_text("token: ''\n")["ok"] is True


def test_s222_scan_detects_authorization_header_and_password() -> None:
    report = scan_config_text("http:\n  headers:\n    Authorization: Bearer abc123\n")
    assert report["ok"] is False
    report = scan_config_text("source:\n  login:\n    password: hunter2\n")
    assert report["ok"] is False


def test_s222_package_rejects_plaintext_config(tmp_path: Path) -> None:
    config = load_config(_config(tmp_path, ai_key="sk-plain-value"))
    manager = WorkspaceManager(config)
    with pytest.raises(ValueError, match="明文凭据"):
        manager.package(tmp_path / "out.zip", kind="config")
    with pytest.raises(ValueError, match="明文凭据"):
        manager.package(tmp_path / "out.zip", kind="full")


def test_s222_package_allows_secret_reference(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("OMNICRAWL_SECRET_AI_OPENAI_API_KEY", "dummy-resolved")
    config = load_config(_config(tmp_path, ai_key="secret://ai.openai.api_key"))
    manager = WorkspaceManager(config)
    result = manager.package(tmp_path / "out.zip", kind="config")
    assert result["created"].endswith("out.zip")


def test_s222_scan_reports_all_findings() -> None:
    text = "a:\n  password: x\n  api_key: y\n"
    report = scan_config_text(text)
    assert report["ok"] is False
    assert len(report["findings"]) == 2
