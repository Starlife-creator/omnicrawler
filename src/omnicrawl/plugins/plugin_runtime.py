from __future__ import annotations

import inspect
import time
import urllib.request
from collections.abc import Callable
from typing import Any

from ..core.config import AppConfig
from ..core.errors import ResponseTooLargeError
from ..core.models import CrawlRequest, ExtractedRecord, FetchResult
from ..fetching.http_client import build_safe_opener
from ..security.egress import EgressBroker, NetworkCapability
from ..state import StateStore


class PluginNetworkClient:
    """Credential-isolated HTTP client bound to a broker-issued plugin capability."""

    def __init__(
        self,
        config: AppConfig,
        egress: EgressBroker,
        capability: NetworkCapability,
    ) -> None:
        self.config = config
        self.egress = egress
        self.capability = capability
        self.opener = build_safe_opener(
            config,
            target_policy=egress.policy,
            include_cookies=False,
            egress=egress,
            capability=capability,
            purpose="plugin",
        )

    def fetch(
        self,
        url: str,
        *,
        method: str = "GET",
        headers: dict[str, str] | None = None,
        body: bytes | None = None,
    ) -> FetchResult:
        safe_headers = {
            "User-Agent": str(self.config.section("http").get("user_agent", "OmniCrawler")),
            **{str(key): str(value) for key, value in (headers or {}).items()},
        }
        maximum = int(self.config.section("http").get("max_response_bytes", 50_000_000))
        request = CrawlRequest(url, method=method, headers=safe_headers, body=body, kind="plugin")
        raw = urllib.request.Request(url, data=body, headers=safe_headers, method=method.upper())
        started = time.monotonic()
        with self.egress.request(
            url,
            purpose="plugin",
            headers=safe_headers,
            capability=self.capability,
        ):
            with self.opener.open(
                raw,
                timeout=float(self.config.section("http").get("timeout_seconds", 25)),
            ) as response:
                payload = response.read(maximum + 1)
                if len(payload) > maximum:
                    raise ResponseTooLargeError(f"插件响应超过大小限制: > {maximum}")
                self.egress.record_response(len(payload), url=response.geturl())
                return FetchResult(
                    request,
                    response.geturl(),
                    int(getattr(response, "status", 200)),
                    {str(key).casefold(): str(value) for key, value in response.headers.items()},
                    payload,
                    time.monotonic() - started,
                )


def build_extension(factory: Callable[..., Any], config: AppConfig, options: dict[str, Any]) -> Any:
    """Instantiate a plugin factory without hiding TypeError from plugin code."""

    for arguments in ((config, options), (config,), ()):
        if _can_bind(factory, *arguments):
            return factory(*arguments)
    raise TypeError("Plugin factory must accept (config, options), (config), or no arguments")


def prepare_request(provider: Any, request: CrawlRequest) -> CrawlRequest:
    callback = getattr(provider, "prepare", provider)
    if not callable(callback):
        raise TypeError("Auth provider must be callable or implement prepare(request)")
    prepared = callback(request)
    if prepared is None:
        return request
    if not isinstance(prepared, CrawlRequest):
        raise TypeError("Auth provider must return CrawlRequest or None")
    return prepared


def transform_record(transformer: Any, record: ExtractedRecord) -> ExtractedRecord:
    callback = getattr(transformer, "transform", transformer)
    if not callable(callback):
        raise TypeError("Transformer must be callable or implement transform(record)")
    transformed = callback(record)
    if transformed is None:
        return record
    if isinstance(transformed, ExtractedRecord):
        return transformed
    if isinstance(transformed, dict):
        record.data = transformed
        return record
    raise TypeError("Transformer must return ExtractedRecord, dict, or None")


def run_exporter(
    exporter: Callable[..., Any],
    config: AppConfig,
    state: StateStore,
    run_id: str,
    options: dict[str, Any],
) -> Any:
    """Run a function exporter or an exporter object created by a class."""

    if inspect.isclass(exporter):
        instance = build_extension(exporter, config, options)
        callback = getattr(instance, "export", None)
        if not callable(callback):
            raise TypeError("Exporter class must implement export(state, run_id, options)")
        method_arguments: tuple[tuple[Any, ...], ...] = (
            (state, run_id, options), (state, run_id), (state,)
        )
        for arguments in method_arguments:
            if _can_bind(callback, *arguments):
                return callback(*arguments)
        raise TypeError("Exporter method has an unsupported signature")

    function_arguments: tuple[tuple[Any, ...], ...] = (
        (config, state, run_id, options), (config, state, run_id)
    )
    for arguments in function_arguments:
        if _can_bind(exporter, *arguments):
            return exporter(*arguments)
    raise TypeError("Exporter must accept (config, state, run_id[, options])")


def _can_bind(callback: Callable[..., Any], *arguments: Any) -> bool:
    try:
        inspect.signature(callback).bind(*arguments)
    except (TypeError, ValueError):
        return False
    return True
