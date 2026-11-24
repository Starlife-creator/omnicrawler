"""S2.2.2：导出前明文凭据扫描（scan_config_text + 工作区包拒绝导出）。

验收：快照/导出无明文凭据落盘；secret:// 引用与 ${VAR} 放行；命中拒绝并提示行号。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from omnicrawler.core.config import load_config
from omnicrawler.security.security_audit import scan_config_text
from omnicrawler.services.workspace import WorkspaceManager


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


# ── 导出前扫描的两处真实缺陷（2026-11-24）──────────────────────────────


@pytest.mark.parametrize(
    "text",
    [
        # 键名可疑（api_key/password/token）但值已密封 ⇒ 不得判红
        "ai: {providers: {openai: {api_key: 'secret://ai.openai.api_key'}}}",
        "proxy: {list: 'secret://settings.proxy_list'}",
        "password: secret://p.q",
        "webhook: {token: 'secret://x.y'}",
    ],
)
def test_sealed_references_are_never_reported_as_plaintext(text: str) -> None:
    """已密封的 `secret://` 引用必须放行。

    实测缺陷：遮蔽前，`secret://settings.proxy_list` 里的 `secret` 被**当成键名**
    （键名正则没加词边界），于是正确密封的配置被判成「明文凭据」、拒绝导出 ——
    安全措施反而挡住了正确做法。
    """
    report = scan_config_text(text)
    assert report["ok"] is True, f"{text!r} 被误判：{report['findings']}"


@pytest.mark.parametrize(
    "text",
    [
        # 代理把凭据放在**值**里，键名（list/enabled）毫无可疑之处
        "proxy: {list: 'http://user:pass@proxy:8080'}",
        "proxy: {list: 'user:pass@proxy:8080'}",
        "proxy: {list: 'socks5://u:p@h:1080'}",
    ],
)
def test_proxy_credentials_inside_a_value_are_caught(text: str) -> None:
    """代理地址里内嵌的 `user:pass@host` 必须被判出来。

    实测缺陷：键名规则只看「字段名像不像凭据」，而 `list` / `enabled` 都不可疑
    ⇒ 整条被放行，导出包会带着代理密码出门。含/不含 scheme 两种形态都漏。
    """
    report = scan_config_text(text)
    assert report["ok"] is False, f"{text!r} 没被抓到"
    assert any(item["key"] == "proxy_credentials" for item in report["findings"]), report["findings"]


@pytest.mark.parametrize(
    "text",
    [
        "proxy: {list: 'http://proxy1:8080'}",
        "note: 'see http://host/path?a=b@c'",
        "name: demo",
    ],
)
def test_plain_proxies_and_at_signs_in_paths_are_not_flagged(text: str) -> None:
    """无凭据的代理、以及出现在路径里的 `@` 都不该判红。"""
    assert scan_config_text(text)["ok"] is True, text
