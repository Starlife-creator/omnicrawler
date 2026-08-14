from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from omnicrawl.core.config import DEFAULTS, AppConfig, validate_config
from omnicrawl.core.errors import CredentialScopeError, EgressBudgetExceededError, EgressDisabledError
from omnicrawl.fetching.browser_fetcher import BrowserFetcher
from omnicrawl.pipeline import build_registry
from omnicrawl.security.egress import EgressBroker, redact_url
from omnicrawl.security.security_audit import egress_audit_report
from omnicrawl.services.ai_providers import build_provider


class _Policy:
    def __init__(self) -> None:
        self.urls: list[str] = []

    def require(self, url: str) -> None:
        self.urls.append(url)

    def approved_addresses(self, host: str, port: int) -> tuple[str, ...]:
        return (f"approved:{host}:{port}",)


def _config(tmp_path: Path, **egress) -> AppConfig:
    raw = {
        **DEFAULTS,
        "project": {"name": "egress", "workspace": str(tmp_path / "work")},
        "source": {"kind": "static_html", "seeds": ["https://api.example.com/start"]},
        "http": {**DEFAULTS["http"], "user_agent": "test@example.com"},
        "egress": {**DEFAULTS["egress"], **egress},
    }
    return AppConfig(tmp_path / "task.yaml", tmp_path, raw, tmp_path / "work")


@pytest.fixture(autouse=True)
def _restore_global_egress_kill_switch():
    """每个测试后恢复全局出网 kill-switch（_global_disabled），防跨测试泄漏。

    emergency_disconnect_all 会禁用进程内所有 broker 的出网；若测试中途
    异常且未走到 finally，全局状态会污染后续测试（P1-5）。
    """
    was_set = EgressBroker._global_disabled.is_set()
    yield
    if was_set:
        EgressBroker._global_disabled.set()
    else:
        EgressBroker._global_disabled.clear()


def test_redaction_domain_port_scheme_and_audit(tmp_path: Path) -> None:
    policy = _Policy()
    broker = EgressBroker(
        _config(
            tmp_path,
            allowed_domains=["example.com"],
            allowed_ports=[443],
            credential_domains=["api.example.com"],
        ),
        policy=policy,
    )
    addresses = broker.authorize(
        "https://api.example.com/items?token=secret&page=2#fragment",
        headers={"Authorization": "Bearer never-log-this"},
    )
    assert addresses == ("approved:api.example.com:443",)
    assert policy.urls == ["https://api.example.com/items?token=secret&page=2"]
    audit = broker.audit_path.read_text(encoding="utf-8")
    assert "secret" not in audit and "never-log-this" not in audit and "token=%2A%2A%2A" in audit
    assert "#fragment" not in redact_url("https://example.com/?key=x#fragment")
    assert "secret" not in redact_url("https://user:secret@example.com/private?token=value")
    assert redact_url("https://user:secret@example.com:8443/private") == (
        "https://example.com:8443/private"
    )

    with pytest.raises(EgressDisabledError):
        broker.authorize("http://api.example.com:80/")
    with pytest.raises(EgressDisabledError):
        broker.authorize("https://example.net/")
    with pytest.raises(EgressDisabledError):
        broker.authorize("ftp://api.example.com/file")
    report = egress_audit_report(broker.audit_path)
    assert report["blocked_attempts"] == 3
    assert report["accessed_boundaries"] == [
        {"scheme": "ftp", "host": "api.example.com", "port": 80, "events": 1},
        {"scheme": "http", "host": "api.example.com", "port": 80, "events": 1},
        {"scheme": "https", "host": "api.example.com", "port": 443, "events": 1},
        {"scheme": "https", "host": "example.net", "port": 443, "events": 1},
    ]


def test_credentials_are_bound_to_domain_and_purpose(tmp_path: Path) -> None:
    broker = EgressBroker(
        _config(
            tmp_path,
            credential_domains=["login.example.com"],
            credential_purposes=["login"],
        ),
        policy=_Policy(),
    )
    broker.authorize(
        "https://login.example.com/session",
        purpose="login",
        headers={"Cookie": "private"},
    )
    with pytest.raises(CredentialScopeError):
        broker.authorize(
            "https://api.example.com/data",
            purpose="login",
            headers={"X-Api-Key": "private"},
        )
    with pytest.raises(CredentialScopeError):
        broker.authorize(
            "https://login.example.com/data",
            purpose="plugin",
            headers={"Authorization": "private"},
        )


def test_request_byte_cost_concurrency_and_runtime_budgets(tmp_path: Path) -> None:
    broker = EgressBroker(
        _config(
            tmp_path,
            maximum_requests=2,
            maximum_bytes=5,
            maximum_cost=2,
            maximum_concurrency=1,
        ),
        policy=_Policy(),
    )
    with broker.request("https://api.example.com/one"):
        with pytest.raises(EgressBudgetExceededError):
            with broker.request("https://api.example.com/concurrent"):
                pass
    broker.authorize("https://api.example.com/two")
    with pytest.raises(EgressBudgetExceededError):
        broker.authorize("https://api.example.com/three")
    broker.record_response(5, cost=1.5)
    with pytest.raises(EgressBudgetExceededError):
        broker.record_response(1)
    with pytest.raises(EgressBudgetExceededError):
        broker.record_response(0, cost=0.6)
    assert broker.snapshot().active == 0

    timed = EgressBroker(_config(tmp_path / "timed", maximum_runtime_seconds=0.01), policy=_Policy())
    timed._started -= 1
    with pytest.raises(EgressBudgetExceededError):
        timed.authorize("https://api.example.com/")


def test_per_host_circuit_breaker_opens_and_recovers(tmp_path: Path) -> None:
    broker = EgressBroker(
        _config(
            tmp_path,
            circuit_failure_threshold=2,
            circuit_recovery_seconds=10,
        ),
        policy=_Policy(),
    )
    url = "https://api.example.com/data"
    broker.record_failure(url, error="timeout one")
    broker.authorize(url)
    broker.record_failure(url, error="timeout two")
    with pytest.raises(EgressBudgetExceededError, match="熔断器"):
        broker.authorize(url)
    failures, _open_until = broker._circuits["api.example.com"]
    broker._circuits["api.example.com"] = (failures, 0.0)
    broker.authorize(url)
    broker.record_success(url)
    assert "api.example.com" not in broker._circuits


def test_task_global_switches_and_stopped_run_are_fail_closed(tmp_path: Path) -> None:
    EgressBroker.restore_global_network()
    broker = EgressBroker(_config(tmp_path), policy=_Policy())
    broker.disconnect_task()
    with pytest.raises(EgressDisabledError):
        broker.authorize("https://api.example.com/")
    broker.reconnect_task()
    broker.authorize("https://api.example.com/")

    EgressBroker.emergency_disconnect_all()
    try:
        with pytest.raises(EgressDisabledError):
            broker.authorize("https://api.example.com/")
    finally:
        EgressBroker.restore_global_network()

    broker.config.workspace.mkdir(parents=True, exist_ok=True)
    (broker.config.workspace / "run_control.json").write_text(
        json.dumps({"stop_requested": True}), encoding="utf-8"
    )
    with pytest.raises(EgressDisabledError):
        broker.authorize("https://api.example.com/")


def test_capabilities_are_scoped_budgeted_and_revocable(tmp_path: Path) -> None:
    broker = EgressBroker(_config(tmp_path), policy=_Policy())
    capability = broker.issue_capability(
        "example-plugin",
        domains=["plugin.example.com"],
        purposes=["plugin"],
        maximum_requests=1,
    )
    broker.authorize(
        "https://plugin.example.com/data", purpose="plugin", capability=capability
    )
    with pytest.raises(EgressBudgetExceededError):
        broker.authorize(
            "https://plugin.example.com/more", purpose="plugin", capability=capability
        )
    with pytest.raises(EgressDisabledError):
        broker.authorize("https://api.example.com/", purpose="plugin", capability=capability)
    broker.revoke_capability(capability)
    with pytest.raises(EgressDisabledError):
        broker.authorize(
            "https://plugin.example.com/data", purpose="plugin", capability=capability
        )
    assert capability.token not in repr(capability)


def test_audit_failure_is_observable_without_disabling_policy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    broker = EgressBroker(_config(tmp_path), policy=_Policy())
    monkeypatch.setattr(Path, "open", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("disk full")))
    broker.authorize("https://api.example.com/data")
    status = broker.audit_status()
    assert status["enabled"] is True
    assert status["write_failures"] == 1


def test_egress_configuration_validation(tmp_path: Path) -> None:
    config = _config(tmp_path, allowed_schemes=["file"], maximum_requests=-1)
    errors, _warnings = validate_config(config)
    assert "egress.allowed_schemes包含不支持的协议" in errors
    assert "egress.maximum_requests不能为负数" in errors


def test_selenium_bidi_guard_allows_or_fails_each_subrequest(tmp_path: Path) -> None:
    fetcher = BrowserFetcher.__new__(BrowserFetcher)
    fetcher.config = _config(tmp_path, experimental_selenium_bidi_guard=True)
    fetcher.egress = MagicMock()
    handlers = []
    driver = SimpleNamespace(
        network=SimpleNamespace(
            add_request_handler=lambda event, handler: handlers.append((event, handler))
        )
    )
    fetcher._install_selenium_guard(driver)
    request = SimpleNamespace(
        url="https://api.example.com/data",
        headers={"Accept": "application/json"},
        fail=MagicMock(),
        continue_request=MagicMock(),
    )
    assert handlers[0][0] == "before_request"
    handlers[0][1](request)
    fetcher.egress.authorize.assert_called_once_with(
        request.url, purpose="browser", headers=request.headers
    )
    request.fail.assert_not_called()
    request.continue_request.assert_called_once_with()

    fetcher.egress.authorize.side_effect = EgressDisabledError("blocked")
    handlers[0][1](request)
    request.fail.assert_called_once_with()

    with pytest.raises(RuntimeError, match="逐请求拦截不可用"):
        fetcher._install_selenium_guard(SimpleNamespace())

    fetcher.config = _config(tmp_path / "closed", experimental_selenium_bidi_guard=False)
    with pytest.raises(RuntimeError, match="已显式关闭"):
        fetcher._install_selenium_guard(driver)

    fetcher.config = _config(tmp_path / "legacy", allow_unintercepted_selenium=True)
    fetcher._install_selenium_guard(SimpleNamespace())


class _PluginHandler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        body = b"controlled-plugin-response"
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format, *_args):
        return


def test_network_plugin_receives_only_scoped_client(tmp_path: Path) -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _PluginHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        plugin = tmp_path / "network_plugin.py"
        plugin.write_text(
            "PLUGIN_METADATA = {"
            "'name': 'network-test', 'permissions': ['network'], "
            "'domains': ['127.0.0.1'], 'resource_limits': {'maximum_requests': 1}}\n"
            "def register(registry, context):\n"
            "    registry.register_fetcher('controlled_plugin', lambda *args: context.network)\n",
            encoding="utf-8",
        )
        config_path = tmp_path / "plugin-task.yaml"
        config_path.write_text(
            f"project: {{name: plugin, workspace: '{tmp_path / 'work-plugin'}'}}\n"
            f"source: {{kind: static_html, seeds: ['http://127.0.0.1:{server.server_port}/']}}\n"
            "http: {allow_private_network: true, respect_robots: false, delay_seconds: 0}\n"
            f"plugins: {{paths: ['{plugin}'], approved_permissions: [network], signature_policy: developer}}\n",
            encoding="utf-8",
        )
        from omnicrawl.core.config import load_config

        config = load_config(config_path)
        broker = EgressBroker(config)
        registry = build_registry(config, broker)
        client = registry.fetchers["controlled_plugin"]()
        result = client.fetch(f"http://127.0.0.1:{server.server_port}/data")
        assert result.body == b"controlled-plugin-response"
        with pytest.raises(EgressBudgetExceededError):
            client.fetch(f"http://127.0.0.1:{server.server_port}/again")
    finally:
        server.shutdown()
        server.server_close()


def test_direct_plugin_transport_import_is_rejected_before_execution(tmp_path: Path) -> None:
    marker = tmp_path / "executed.txt"
    plugin = tmp_path / "unsafe_network.py"
    plugin.write_text(
        "PLUGIN_METADATA = {'name': 'unsafe', 'permissions': ['network'], "
        "'domains': ['example.com']}\n"
        "import requests\n"
        f"open({str(marker)!r}, 'w').write('executed')\n"
        "def register(registry, context): pass\n",
        encoding="utf-8",
    )
    config = _config(tmp_path)
    config.raw["plugins"] = {
        "paths": [str(plugin)],
        "approved_permissions": ["network"],
        "signature_policy": "developer",
    }
    with pytest.raises(PermissionError, match="不得直接导入网络客户端"):
        build_registry(config, EgressBroker(config, policy=_Policy()))
    assert not marker.exists()


def test_failed_network_plugin_registration_revokes_issued_capability(tmp_path: Path) -> None:
    plugin = tmp_path / "broken_network_plugin.py"
    plugin.write_text(
        "PLUGIN_METADATA = {'name': 'broken-network', 'permissions': ['network'], "
        "'domains': ['example.com']}\n"
        "def register(registry, context):\n"
        "    registry.register_source('partial_source', object)\n"
        "    raise RuntimeError('registration failed')\n",
        encoding="utf-8",
    )
    config = _config(tmp_path)
    config.raw["plugins"] = {
        "paths": [str(plugin)],
        "approved_permissions": ["network"],
        "signature_policy": "developer",
    }
    broker = EgressBroker(config, policy=_Policy())
    with pytest.raises(RuntimeError, match="registration failed"):
        build_registry(config, broker)
    assert broker._capability_counts == {}


def test_ai_provider_is_fail_closed_without_broker_and_audited_with_it(tmp_path: Path) -> None:
    ai = {
        "mode": "custom",
        "default_provider": "demo",
        "providers": {
            "demo": {
                "type": "openai_compatible",
                "base_url": "https://ai.example.com/v1",
                "api_key": "never-log-ai-key",
                "model": "demo-model",
            }
        },
    }
    unsafe = build_provider(ai)
    with pytest.raises(RuntimeError, match="Egress Broker"):
        unsafe.generate([{"role": "user", "content": "hello"}])

    config = _config(tmp_path)
    config.raw["ai"] = ai
    broker = EgressBroker(config, policy=_Policy())
    provider = build_provider(ai, app_config=config, egress=broker)
    response = MagicMock()
    response.read.return_value = json.dumps(
        {
            "choices": [{"message": {"content": "ok"}}],
            "usage": {"total_tokens": 2},
        }
    ).encode()
    response.geturl.return_value = "https://ai.example.com/v1/chat/completions"
    response.headers = {}
    response.__enter__.return_value = response
    opener = MagicMock()
    opener.open.return_value = response
    with patch("omnicrawl.services.ai_providers.build_safe_opener", return_value=opener):
        result = provider.generate([{"role": "user", "content": "hello"}])
    assert result.text == "ok"
    audit = broker.audit_path.read_text(encoding="utf-8")
    assert "never-log-ai-key" not in audit and '"purpose": "ai"' in audit
