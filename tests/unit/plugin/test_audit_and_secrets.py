"""Phase 2a C6 审计留痕 + O 密钥零暴露（secrets.get 例外路径）。"""

from __future__ import annotations

import pytest

from omnicrawler.plugins.plugin_broker import CapabilityBroker, CapabilityError

pytestmark = pytest.mark.plugin_contract


def _make_broker(**overrides) -> CapabilityBroker:
    kwargs = {"permissions": set(), "system_info": {"version": "t"}}
    kwargs.update(overrides)
    return CapabilityBroker(**kwargs)


def test_audit_hook_records_capability_calls() -> None:
    """C6：每次能力调用经 audit_hook 留痕（decision=executed）。"""
    events: list[tuple[str, dict]] = []
    broker = _make_broker(
        audit_hook=lambda action, details: events.append((action, details)),
        plugin_id="audit-demo",
    )
    broker.dispatch("system.info", {})
    broker.dispatch("system.info", {})

    calls = [e for e in events if e[0] == "plugin.subprocess.call"]
    assert len(calls) == 2
    assert calls[0][1]["plugin_id"] == "audit-demo"
    assert calls[0][1]["operation"] == "system.info"
    assert calls[0][1]["decision"] == "executed"
    assert calls[0][1]["duration_ms"] >= 0


def test_audit_hook_failure_does_not_block() -> None:
    """C6：审计写入失败不阻断插件运行（第 35 轮）。"""
    def bad_hook(action, details):
        raise RuntimeError("audit store down")

    broker = _make_broker(audit_hook=bad_hook, plugin_id="x")
    # 不抛异常，正常返回
    assert broker.dispatch("system.info", {})["version"] == "t"


def test_trace_full_records_sequence() -> None:
    """C6：trace_full=True 记全序列；False 时仅降采样计数。"""
    full = _make_broker(trace_full=True)
    full.dispatch("system.info", {})
    full.dispatch("system.info", {})
    assert len(full.trace_log) == 2
    assert full.trace_log[0]["operation"] == "system.info"

    sampled = _make_broker(trace_full=False)
    sampled.dispatch("system.info", {})
    assert sampled.trace_log == []
    assert sampled.op_counts["system.info"] == 1


def test_secrets_get_requires_permission() -> None:
    """O：未声明 secrets:read → E_PERMISSION。"""
    broker = _make_broker(permissions=set())
    with pytest.raises(CapabilityError) as exc_info:
        broker.dispatch("secrets.get", {"ref": "api_key"})
    assert exc_info.value.code == "E_PERMISSION"


def test_secrets_get_respects_allowlist() -> None:
    """O：ref 不在 manifest secrets 白名单 → E_PERMISSION。"""
    broker = _make_broker(
        permissions={"secrets:read"},
        secrets_allowlist=("allowed_key",),
        secret_resolver=lambda ref: "secret-value",
    )
    with pytest.raises(CapabilityError) as exc_info:
        broker.dispatch("secrets.get", {"ref": "not_allowed"})
    assert exc_info.value.code == "E_PERMISSION"


def test_secrets_get_returns_value_and_audits() -> None:
    """O：白名单内 ref 返回明文 + 审计留痕（decision=secret_accessed）。"""
    events: list[tuple[str, dict]] = []
    store = {"api_key": "s3cr3t"}
    broker = _make_broker(
        permissions={"secrets:read"},
        secrets_allowlist=("api_key",),
        secret_resolver=lambda ref: store.get(ref),
        audit_hook=lambda action, details: events.append((action, details)),
        plugin_id="sec-demo",
    )
    result = broker.dispatch("secrets.get", {"ref": "api_key"})
    assert result["value"] == "s3cr3t"

    secret_events = [e for e in events if e[0] == "plugin.secret_accessed"]
    assert len(secret_events) == 1
    assert secret_events[0][1]["decision"] == "secret_accessed"
    assert secret_events[0][1]["reason"] == "api_key"
    # 审计 details 不含明文（零暴露留痕原则）
    assert "s3cr3t" not in str(secret_events[0][1])


def test_secrets_get_missing_value_resource_error() -> None:
    """O：ref 在白名单但密钥库无值 → E_RESOURCE。"""
    broker = _make_broker(
        permissions={"secrets:read"},
        secrets_allowlist=("api_key",),
        secret_resolver=lambda ref: None,
    )
    with pytest.raises(CapabilityError) as exc_info:
        broker.dispatch("secrets.get", {"ref": "api_key"})
    assert exc_info.value.code == "E_RESOURCE"


def test_secrets_get_without_resolver_internal_error() -> None:
    """O：宿主未提供解析器 → E_INTERNAL。"""
    broker = _make_broker(permissions={"secrets:read"}, secrets_allowlist=("api_key",))
    with pytest.raises(CapabilityError) as exc_info:
        broker.dispatch("secrets.get", {"ref": "api_key"})
    assert exc_info.value.code == "E_INTERNAL"
