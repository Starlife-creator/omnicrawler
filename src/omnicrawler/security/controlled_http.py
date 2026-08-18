"""Scoped, audited HTTP requests for small interactive operations.

Connection probes, selector previews and the standalone PDF LLM client used to
make their own transport calls. They now receive an ephemeral ``AppConfig``
whose only approved target is the endpoint supplied for that operation, then
use the same ``HTTPFetcher`` and ``EgressBroker`` path as a crawl task.

The configuration is in-memory only. Its workspace is supplied by the caller
so audit records remain with the user's task rather than being silently placed
in a process-global location.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from ..core.config import DEFAULTS, AppConfig
from ..core.models import CrawlRequest, FetchResult
from ..core.utils import deep_merge, user_agent
from ..fetching.http_client import HTTPFetcher


def scoped_network_config(
    endpoint: str,
    *,
    workspace: str | Path,
    purpose: str,
    timeout_seconds: float = 30,
    max_response_bytes: int = 1_048_576,
    user_agent: str = user_agent("scoped operation"),
) -> AppConfig:
    """Create a strict in-memory config for one user-initiated endpoint.

    The endpoint's exact scheme, host and effective port are allow-listed. A
    redirect therefore cannot switch to a different service or a private
    address, and sensitive headers are accepted only for the ``ai`` purpose.
    """

    parts = urlsplit(endpoint)
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        raise ValueError("受控网络请求必须使用有效的 HTTP(S) 地址")
    if parts.username is not None or parts.password is not None:
        raise ValueError("受控网络请求地址不能包含用户名或密码")
    if not 1 <= float(timeout_seconds) <= 3_600:
        raise ValueError("请求超时必须在 1 到 3600 秒之间")
    if not 1_024 <= int(max_response_bytes) <= 50 * 1024 * 1024:
        raise ValueError("响应大小上限必须在 1KB 到 50MB 之间")

    scoped_workspace = Path(workspace).expanduser().resolve()
    scheme = parts.scheme.casefold()
    port = parts.port or (443 if scheme == "https" else 80)
    canonical_endpoint = urlunsplit((scheme, parts.netloc, parts.path or "/", parts.query, ""))
    raw = deep_merge(
        DEFAULTS,
        {
            "project": {"name": "scoped_network_operation", "workspace": str(scoped_workspace)},
            "source": {"kind": "static_html", "seeds": [canonical_endpoint], "headers": {}},
            "crawl": {"max_pages": 1, "max_depth": 0, "concurrency": 1},
            "http": {
                "user_agent": user_agent,
                "timeout_seconds": float(timeout_seconds),
                "retries": 1,
                "delay_seconds": 0,
                "max_redirects": 3,
                "max_response_bytes": int(max_response_bytes),
                "allow_private_network": False,
                "resolve_dns": True,
                "dns_fail_closed": True,
                "proxy": "",
            },
            "egress": {
                "enabled": True,
                "allowed_schemes": [scheme],
                "allowed_ports": [port],
                "allowed_domains": [parts.hostname],
                "credential_domains": [parts.hostname],
                "credential_purposes": ["ai"],
                "maximum_requests": 4,
                "maximum_bytes": int(max_response_bytes) * 4,
                "maximum_concurrency": 1,
                "audit": True,
            },
        },
    )
    return AppConfig(
        path=scoped_workspace / ".omnicrawler-scoped-network.yaml",
        root=scoped_workspace,
        raw=raw,
        workspace=scoped_workspace,
    )


def scoped_fetch(
    endpoint: str,
    *,
    workspace: str | Path,
    purpose: str,
    method: str = "GET",
    headers: Mapping[str, str] | None = None,
    body: bytes | None = None,
    timeout_seconds: float = 30,
    max_response_bytes: int = 1_048_576,
    user_agent: str = user_agent("scoped operation"),
) -> FetchResult:
    """Fetch one endpoint through the shared policy, budget and audit path."""

    config = scoped_network_config(
        endpoint,
        workspace=workspace,
        purpose=purpose,
        timeout_seconds=timeout_seconds,
        max_response_bytes=max_response_bytes,
        user_agent=user_agent,
    )
    request = CrawlRequest(
        url=endpoint,
        method=method,
        headers={str(key): str(value) for key, value in (headers or {}).items()},
        body=body,
        kind="scoped_operation",
    )
    return HTTPFetcher(config, purpose=purpose).fetch(request)


def scoped_json_request(endpoint: str, **kwargs: Any) -> dict[str, Any]:
    """Make a scoped request and require a JSON-object response."""

    result = scoped_fetch(endpoint, **kwargs)
    try:
        payload = json.loads(result.body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("服务返回的不是有效 JSON") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("服务返回的 JSON 顶层必须是对象")
    return payload
