"""network.fetch 的 auth 注入契约测试（§12.2 #8「网络凭据零暴露」的实现侧）。

契约：``network.fetch`` 可用 ``auth: {secret_ref, header}``，**宿主代理侧**
从密钥库解析并注入请求头，插件进程永远看不到明文；注入头值不进审计/日志。

覆盖两个层面：
1. 纯函数 ``resolve_auth_header`` —— 白名单/头名/换行/缺省等分支；
2. 端到端 ``CapabilityBroker.dispatch("network.fetch", ...)`` —— 注入确实发生、
   值确实到达网络客户端、而**审计里只有头名没有头值**。
"""

from __future__ import annotations

import pytest

from omnicrawler.plugins.plugin_broker import CapabilityBroker
from omnicrawler.plugins.plugin_broker_io import resolve_auth_header

pytestmark = pytest.mark.plugin_contract


# ── 纯函数层 ─────────────────────────────────────────────


class TestResolveAuthHeader:
    def test_no_auth_returns_none(self) -> None:
        """无规则不注入（缺省即安全）。"""
        assert resolve_auth_header(None, resolver=lambda _r: "v", allowlist=set()) is None
        assert resolve_auth_header({}, resolver=lambda _r: "v", allowlist=set()) is None
        assert resolve_auth_header("nonsense", resolver=lambda _r: "v", allowlist=set()) is None

    def test_default_header_is_authorization(self) -> None:
        header, value = resolve_auth_header(
            {"secret_ref": "api"}, resolver=lambda _r: "s3cr3t", allowlist={"api"}
        )
        assert header == "Authorization"
        assert value == "s3cr3t"

    def test_custom_allowed_header(self) -> None:
        header, _value = resolve_auth_header(
            {"secret_ref": "api", "header": "X-Api-Key"},
            resolver=lambda _r: "v",
            allowlist={"api"},
        )
        assert header == "X-Api-Key"

    def test_out_of_allowlist_ref_rejected(self) -> None:
        from omnicrawler.plugins.plugin_broker_contracts import CapabilityError

        with pytest.raises(CapabilityError) as exc:
            resolve_auth_header(
                {"secret_ref": "other"}, resolver=lambda _r: "v", allowlist={"api"}
            )
        assert exc.value.code == "E_PERMISSION"

    def test_arbitrary_header_name_rejected(self) -> None:
        """任意头名 = 密钥可被投递到非标准通道 ⇒ 必须拒（防外泄面扩大）。"""
        from omnicrawler.plugins.plugin_broker_contracts import CapabilityError

        with pytest.raises(CapabilityError) as exc:
            resolve_auth_header(
                {"secret_ref": "api", "header": "X-Custom-Exfil"},
                resolver=lambda _r: "v",
                allowlist={"api"},
            )
        assert exc.value.code == "E_CONTRACT"

    def test_missing_secret_ref_rejected(self) -> None:
        from omnicrawler.plugins.plugin_broker_contracts import CapabilityError

        with pytest.raises(CapabilityError) as exc:
            resolve_auth_header({"header": "Authorization"}, resolver=lambda _r: "v", allowlist=set())
        assert exc.value.code == "E_CONTRACT"

    def test_newline_in_secret_rejected(self) -> None:
        """含换行的值会被 HTTP 库拆成多个头（头注入）⇒ 直接拒绝而非静默清洗。"""
        from omnicrawler.plugins.plugin_broker_contracts import CapabilityError

        with pytest.raises(CapabilityError) as exc:
            resolve_auth_header(
                {"secret_ref": "api"},
                resolver=lambda _r: "line1\r\nX-Evil: injected",
                allowlist={"api"},
            )
        assert exc.value.code == "E_CONTRACT"

    def test_unknown_secret_rejected(self) -> None:
        from omnicrawler.plugins.plugin_broker_contracts import CapabilityError

        with pytest.raises(CapabilityError) as exc:
            resolve_auth_header(
                {"secret_ref": "api"}, resolver=lambda _r: None, allowlist={"api"}
            )
        assert exc.value.code == "E_RESOURCE"

    def test_missing_resolver_rejected(self) -> None:
        from omnicrawler.plugins.plugin_broker_contracts import CapabilityError

        with pytest.raises(CapabilityError) as exc:
            resolve_auth_header({"secret_ref": "api"}, resolver=None, allowlist={"api"})
        assert exc.value.code == "E_INTERNAL"

    def test_header_name_case_insensitive(self) -> None:
        header, _value = resolve_auth_header(
            {"secret_ref": "api", "header": "authorization"},
            resolver=lambda _r: "v",
            allowlist={"api"},
        )
        assert header == "authorization"


# ── 端到端层：注入确实发生，且不进审计 ─────────────────────


class _RecordingNetwork:
    def __init__(self) -> None:
        self.last_headers: dict | None = None

    def fetch(self, url: str, method: str = "GET", headers: dict | None = None):
        self.last_headers = dict(headers or {})
        from omnicrawler.core.models import CrawlRequest, FetchResult

        return FetchResult(
            request=CrawlRequest(url=url, method=method, headers=headers or {}),
            final_url=url,
            status=200,
            headers={"content-type": "application/json"},
            body=b"{}",
            elapsed_seconds=0.01,
        )


def _broker(**overrides) -> CapabilityBroker:
    kwargs = {"permissions": set(), "system_info": {"version": "t"}}
    kwargs.update(overrides)
    return CapabilityBroker(**kwargs)


def test_auth_injection_reaches_network_client() -> None:
    """端到端：auth 声明的头确实被注入到出站请求（值来自密钥库解析）。"""
    network = _RecordingNetwork()
    broker = _broker(
        permissions={"network:scoped"},
        network_client=network,
        secrets_allowlist=("api_token",),
        secret_resolver=lambda ref: "resolved-secret" if ref == "api_token" else None,
    )
    broker.dispatch(
        "network.fetch",
        {"url": "https://example.com/api", "auth": {"secret_ref": "api_token"}},
    )
    assert network.last_headers is not None
    assert network.last_headers.get("Authorization") == "resolved-secret"


def test_auth_injection_audits_header_name_not_value() -> None:
    """审计只留头名与 decision —— **绝不出现头值**（反向断言：值不得进审计）。"""
    events: list[tuple[str, dict]] = []
    network = _RecordingNetwork()
    broker = _broker(
        permissions={"network:scoped"},
        network_client=network,
        secrets_allowlist=("api_token",),
        secret_resolver=lambda _r: "TOP-SECRET-VALUE",
        audit_hook=lambda action, details: events.append((action, details)),
    )
    broker.dispatch(
        "network.fetch",
        {"url": "https://example.com/api", "auth": {"secret_ref": "api_token"}},
    )
    injected = [d for a, d in events if a == "plugin.auth_injected"]
    assert injected, "未记录 auth 注入审计"
    assert injected[0]["header"] == "Authorization"
    # ★ 反向断言：密钥明文不得出现在任何审计事件里
    blob = repr(events)
    assert "TOP-SECRET-VALUE" not in blob


def test_no_auth_means_no_injection() -> None:
    """未声明 auth ⇒ 不注入、不审计（缺省即安全）。"""
    events: list[str] = []
    network = _RecordingNetwork()
    broker = _broker(
        permissions={"network:scoped"},
        network_client=network,
        secrets_allowlist=("api_token",),
        secret_resolver=lambda _r: "SHOULD-NOT-APPEAR",
        audit_hook=lambda action, _details: events.append(action),
    )
    broker.dispatch("network.fetch", {"url": "https://example.com/api"})
    assert "Authorization" not in (network.last_headers or {})
    assert "plugin.auth_injected" not in events


def test_auth_ref_outside_allowlist_does_not_leak() -> None:
    """白名单外的 ref ⇒ E_PERMISSION，且**请求根本没发出去**（不静默降级成匿名请求）。"""
    network = _RecordingNetwork()
    broker = _broker(
        permissions={"network:scoped"},
        network_client=network,
        secrets_allowlist=("api_token",),
        secret_resolver=lambda _r: "leak",
    )
    from omnicrawler.plugins.plugin_broker_contracts import CapabilityError

    with pytest.raises(CapabilityError) as exc:
        broker.dispatch(
            "network.fetch",
            {"url": "https://example.com/api", "auth": {"secret_ref": "other_token"}},
        )
    assert exc.value.code == "E_PERMISSION"
    # 越权尝试不得退回"不带凭据的匿名请求"——那会让插件以为调用成功了
    assert network.last_headers is None


def test_auth_with_headers_merge_preserves_both() -> None:
    """注入头与插件自带头共存（注入是叠加，不是覆盖整个 headers）。"""
    network = _RecordingNetwork()
    broker = _broker(
        permissions={"network:scoped"},
        network_client=network,
        secrets_allowlist=("api_token",),
        secret_resolver=lambda _r: "tok",
    )
    broker.dispatch(
        "network.fetch",
        {
            "url": "https://example.com/api",
            "headers": {"Accept": "application/json"},
            "auth": {"secret_ref": "api_token", "header": "X-Api-Key"},
        },
    )
    assert network.last_headers is not None
    assert network.last_headers.get("Accept") == "application/json"
    assert network.last_headers.get("X-Api-Key") == "tok"
