from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Callable
from typing import Any

from ..core.config import AppConfig
from ..core.errors import ResponseTooLargeError
from ..core.models import CrawlRequest, ExtractedRecord
from ..security.egress import EgressBroker
from .http_client import build_safe_opener


def collect_sse(
    config: AppConfig,
    request: CrawlRequest,
    should_continue: Callable[[], bool] | None = None,
    egress: EgressBroker | None = None,
) -> list[ExtractedRecord]:
    source = config.section("source")
    maximum = int(source.get("max_messages", 100))
    timeout = float(source.get("duration_seconds", 60))
    maximum_bytes = int(config.section("http").get("max_response_bytes", 50_000_000))
    headers = {
        "Accept": "text/event-stream", "User-Agent": config.section("http").get("user_agent", "OmniCrawler"),
        **config.section("http").get("headers", {}), **source.get("headers", {}), **request.headers,
    }
    import urllib.request

    broker = egress or EgressBroker(config)
    raw = urllib.request.Request(request.url, headers=headers)
    records: list[ExtractedRecord] = []
    started = time.monotonic()
    consumed = 0
    opener = build_safe_opener(config, target_policy=broker.policy, include_cookies=False, egress=broker)
    with broker.request(request.url, purpose="stream", headers=headers):
        with opener.open(raw, timeout=float(config.section("http").get("timeout_seconds", 25))) as response:
            event: dict[str, list[str]] = {}
            while len(records) < maximum and time.monotonic() - started < timeout:
                if should_continue is not None and not should_continue():
                    break
                line = response.readline().decode("utf-8", errors="replace").rstrip("\r\n")
                consumed += len(line.encode("utf-8", errors="replace"))
                if consumed > maximum_bytes:
                    raise ResponseTooLargeError(f"SSE数据超过大小限制: > {maximum_bytes}")
                if not line:
                    if event:
                        data: dict[str, Any] = {key: "\n".join(values) for key, values in event.items()}
                        records.append(ExtractedRecord(request.url, "sse_event", data))
                        event = {}
                    continue
                if line.startswith(":") or ":" not in line:
                    continue
                key, value = line.split(":", 1)
                event.setdefault(key, []).append(value.lstrip())
        broker.record_response(consumed, url=request.url)
    return records


async def _websocket_collect(
    config: AppConfig,
    request: CrawlRequest,
    should_continue: Callable[[], bool] | None = None,
    egress: EgressBroker | None = None,
) -> list[ExtractedRecord]:
    try:
        import websockets
    except ImportError as exc:
        raise RuntimeError("缺少websockets，请安装 omnicrawl[streams]") from exc
    source = config.section("source")
    maximum = int(source.get("max_messages", 100))
    duration = float(source.get("duration_seconds", 60))
    records: list[ExtractedRecord] = []
    started = time.monotonic()
    consumed = 0
    maximum_bytes = int(config.section("http").get("max_response_bytes", 50_000_000))
    headers = {**config.section("http").get("headers", {}), **source.get("headers", {}), **request.headers}
    broker = egress or EgressBroker(config)
    with broker.request(request.url, purpose="stream", headers=headers):
        async with websockets.connect(request.url, additional_headers=headers or None) as socket:
            subscribe = source.get("subscribe")
            if subscribe is not None:
                await socket.send(json.dumps(subscribe, ensure_ascii=False) if isinstance(subscribe, (dict, list)) else str(subscribe))
            while len(records) < maximum and time.monotonic() - started < duration:
                if should_continue is not None and not should_continue():
                    break
                remaining = max(0.1, duration - (time.monotonic() - started))
                try:
                    message = await asyncio.wait_for(socket.recv(), timeout=remaining)
                except TimeoutError:
                    break
                consumed += len(message) if isinstance(message, bytes) else len(message.encode("utf-8"))
                if consumed > maximum_bytes:
                    raise ResponseTooLargeError(f"WebSocket数据超过大小限制: > {maximum_bytes}")
                if isinstance(message, bytes):
                    data: Any = {"binary_hex": message.hex(), "size": len(message)}
                else:
                    try:
                        data = json.loads(message)
                    except json.JSONDecodeError:
                        data = {"text": message}
                records.append(ExtractedRecord(request.url, "websocket_message", data if isinstance(data, dict) else {"value": data}))
        broker.record_response(consumed, url=request.url)
    return records


def collect_websocket(
    config: AppConfig,
    request: CrawlRequest,
    should_continue: Callable[[], bool] | None = None,
    egress: EgressBroker | None = None,
) -> list[ExtractedRecord]:
    return asyncio.run(_websocket_collect(config, request, should_continue, egress))
