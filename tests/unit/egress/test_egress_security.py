from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from omnicrawler.core.config import DEFAULTS, AppConfig
from omnicrawler.core.errors import (
    CredentialScopeError,
    EgressBudgetExceededError,
    EgressDisabledError,
)
from omnicrawler.security.egress import EgressBroker
from omnicrawler.security.security_audit import egress_audit_report


class _Policy:
    """Stub network policy that skips real DNS resolution."""

    def __init__(self) -> None:
        self.urls: list[str] = []

    def require(self, url: str) -> None:
        self.urls.append(url)

    def approved_addresses(self, host: str, port: int) -> tuple[str, ...]:
        return (f"approved:{host}:{port}",)


def _config(tmp_path: Path, **egress: Any) -> AppConfig:
    raw = {
        **DEFAULTS,
        "project": {"name": "egress-security", "workspace": str(tmp_path / "work")},
        "source": {"kind": "static_html", "seeds": ["https://api.example.com/start"]},
        "http": {**DEFAULTS["http"], "user_agent": "test@example.com"},
        "egress": {**DEFAULTS["egress"], **egress},
    }
    return AppConfig(tmp_path / "task.yaml", tmp_path, raw, tmp_path / "work")


@pytest.fixture(autouse=True)
def _restore_global_network():
    """Ensure the global kill-switch is cleared before and after each test."""
    EgressBroker.restore_global_network()
    yield
    EgressBroker.restore_global_network()


def test_domain_whitelist_rejects_unapproved_domain(tmp_path: Path) -> None:
    """域名白名单拒绝测试: 验证不在白名单中的域名被拒绝。"""
    broker = EgressBroker(
        _config(tmp_path, allowed_domains=["trusted.example.com"]),
        policy=_Policy(),
    )
    # Trusted domain is allowed
    broker.authorize("https://trusted.example.com/data")
    # Subdomain of a trusted domain is allowed
    broker.authorize("https://sub.trusted.example.com/data")
    # Untrusted domain is rejected
    with pytest.raises(EgressDisabledError, match="域名未获批准"):
        broker.authorize("https://evil.example.com/data")
    # Completely different domain is rejected
    with pytest.raises(EgressDisabledError, match="域名未获批准"):
        broker.authorize("https://attacker.net/exfil")


def test_port_whitelist_rejects_non_standard_port(tmp_path: Path) -> None:
    """端口白名单拒绝测试: 验证非标准端口被拒绝。"""
    broker = EgressBroker(
        _config(tmp_path, allowed_ports=[443]),
        policy=_Policy(),
    )
    # Standard HTTPS port 443 is allowed
    broker.authorize("https://api.example.com/data")
    # Non-standard port 8443 is rejected
    with pytest.raises(EgressDisabledError, match="端口未获批准"):
        broker.authorize("https://api.example.com:8443/data")
    # HTTP default port 80 is rejected (only 443 is approved)
    with pytest.raises(EgressDisabledError, match="端口未获批准"):
        broker.authorize("http://api.example.com/data")
    # Port 22 (SSH) is rejected
    with pytest.raises(EgressDisabledError, match="端口未获批准"):
        broker.authorize("https://api.example.com:22/data")


def test_protocol_whitelist_rejects_non_http(tmp_path: Path) -> None:
    """协议白名单拒绝测试: 验证非 http/https 协议被拒绝。"""
    broker = EgressBroker(
        _config(tmp_path, allowed_schemes=["http", "https"]),
        policy=_Policy(),
    )
    # HTTPS is allowed
    broker.authorize("https://api.example.com/data")
    # HTTP is allowed
    broker.authorize("http://api.example.com/data")
    # FTP is rejected
    with pytest.raises(EgressDisabledError, match="协议或目标无效"):
        broker.authorize("ftp://api.example.com/file")
    # File scheme is rejected
    with pytest.raises(EgressDisabledError, match="协议或目标无效"):
        broker.authorize("file:///etc/passwd")
    # Gopher is rejected
    with pytest.raises(EgressDisabledError, match="协议或目标无效"):
        broker.authorize("gopher://api.example.com/")


def test_four_dimensional_budget_circuit_breaker(tmp_path: Path) -> None:
    """四维预算超限熔断测试: 验证请求数/字节数/时长/成本超限时触发熔断。"""
    # 1. Request count budget
    broker_req = EgressBroker(
        _config(tmp_path / "req", maximum_requests=2),
        policy=_Policy(),
    )
    broker_req.authorize("https://api.example.com/one")
    broker_req.authorize("https://api.example.com/two")
    with pytest.raises(EgressBudgetExceededError, match="请求预算"):
        broker_req.authorize("https://api.example.com/three")

    # 2. Byte budget
    broker_bytes = EgressBroker(
        _config(tmp_path / "bytes", maximum_bytes=10),
        policy=_Policy(),
    )
    broker_bytes.record_response(5)
    broker_bytes.record_response(3)
    with pytest.raises(EgressBudgetExceededError, match="流量预算"):
        broker_bytes.record_response(5)

    # 3. Runtime budget
    broker_time = EgressBroker(
        _config(tmp_path / "time", maximum_runtime_seconds=0.01),
        policy=_Policy(),
    )
    broker_time._started -= 1  # Simulate elapsed time
    with pytest.raises(EgressBudgetExceededError, match="运行时间"):
        broker_time.authorize("https://api.example.com/")

    # 4. Cost budget
    broker_cost = EgressBroker(
        _config(tmp_path / "cost", maximum_cost=1.0),
        policy=_Policy(),
    )
    broker_cost.record_response(0, cost=0.5)
    broker_cost.record_response(0, cost=0.5)
    with pytest.raises(EgressBudgetExceededError, match="费用预算"):
        broker_cost.record_response(0, cost=0.1)


def test_credential_scope_isolation(tmp_path: Path) -> None:
    """凭据作用域隔离测试: 验证凭据只在授权作用域内可用。"""
    broker = EgressBroker(
        _config(
            tmp_path,
            credential_domains=["login.example.com"],
            credential_purposes=["login"],
        ),
        policy=_Policy(),
    )
    # Credential to authorized domain + purpose is allowed
    broker.authorize(
        "https://login.example.com/session",
        purpose="login",
        headers={"Authorization": "Bearer token"},
    )
    # Credential to unauthorized domain is rejected
    with pytest.raises(CredentialScopeError, match="凭据不能发送到"):
        broker.authorize(
            "https://api.example.com/data",
            purpose="login",
            headers={"Authorization": "Bearer token"},
        )
    # Credential with unauthorized purpose is rejected
    with pytest.raises(CredentialScopeError, match="凭据不能发送到"):
        broker.authorize(
            "https://login.example.com/data",
            purpose="fetch",
            headers={"Cookie": "session=abc"},
        )
    # Different sensitive header type is also rejected
    with pytest.raises(CredentialScopeError):
        broker.authorize(
            "https://api.example.com/data",
            purpose="fetch",
            headers={"X-Api-Key": "secret-key"},
        )
    # No credential headers: any domain/purpose is fine
    broker.authorize("https://api.example.com/data", purpose="fetch")


def test_kill_switch_triggers_emergency_stop(tmp_path: Path) -> None:
    """kill-switch 触发测试: 验证紧急停止开关生效。"""
    broker = EgressBroker(_config(tmp_path), policy=_Policy())

    # Normal operation before kill-switch
    broker.authorize("https://api.example.com/normal")

    # Task-level kill-switch (disconnect_task)
    broker.disconnect_task()
    with pytest.raises(EgressDisabledError, match="网络出口已关闭"):
        broker.authorize("https://api.example.com/blocked")

    # Task-level recovery (reconnect_task)
    broker.reconnect_task()
    broker.authorize("https://api.example.com/restored")

    # Global kill-switch (emergency_disconnect_all)
    EgressBroker.emergency_disconnect_all()
    try:
        with pytest.raises(EgressDisabledError, match="网络出口已关闭"):
            broker.authorize("https://api.example.com/global")
    finally:
        EgressBroker.restore_global_network()

    # After global restore, a new broker on the same class works
    broker.authorize("https://api.example.com/after-restore")


def test_audit_log_integrity(tmp_path: Path) -> None:
    """审计日志完整性测试: 验证每次网络访问都被记录。"""
    broker = EgressBroker(
        _config(
            tmp_path,
            allowed_domains=["api.example.com"],
            audit=True,
        ),
        policy=_Policy(),
    )
    # Authorized request (with sensitive query parameter for redaction check)
    broker.authorize(
        "https://api.example.com/data?token=secret&page=2",
        purpose="fetch",
    )
    # Blocked request (domain not in whitelist)
    with pytest.raises(EgressDisabledError):
        broker.authorize("https://evil.com/data")
    # Response recording
    broker.record_response(1024, cost=0.01, url="https://api.example.com/data")
    # Circuit breaker success
    broker.record_success("https://api.example.com/data")
    # Circuit breaker failure
    broker.record_failure("https://api.example.com/data", error="timeout")

    # Every event is recorded in the audit log
    audit_path = broker.audit_path
    assert audit_path.exists()
    lines = audit_path.read_text(encoding="utf-8").strip().split("\n")
    events = [json.loads(line) for line in lines]
    event_types = [e["event"] for e in events]

    assert "authorized" in event_types
    assert "blocked" in event_types
    assert "response" in event_types
    assert "circuit_success" in event_types
    assert "circuit_failure" in event_types

    # Sensitive data is redacted in URLs
    full_audit = audit_path.read_text(encoding="utf-8")
    assert "secret" not in full_audit
    assert "token=***" in full_audit or "token=%2A%2A%2A" in full_audit

    # The egress_audit_report summary is consistent
    report = egress_audit_report(audit_path)
    assert report["blocked_attempts"] >= 1
    boundaries = report.get("accessed_boundaries", [])
    assert any(
        b["host"] == "api.example.com" and b["scheme"] == "https"
        for b in boundaries
    )
