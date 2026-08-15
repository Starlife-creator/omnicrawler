from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Callable
from typing import Any

from ..core.config import AppConfig
from ..core.errors import ResponseTooLargeError
from ..core.models import CrawlRequest, ExtractedRecord
from ..core.safe_data import safe_float, safe_int, safe_json_loads
from ..security.egress import EgressBroker
from .http_client import build_safe_opener

LOGGER = logging.getLogger(__name__)


def collect_sse(
    config: AppConfig,
    request: CrawlRequest,
    should_continue: Callable[[], bool] | None = None,
    egress: EgressBroker | None = None,
    max_messages: int | None = None,
) -> list[ExtractedRecord]:
    source = config.section("source")
    maximum = max_messages if max_messages is not None else (safe_int(source.get("max_messages"), default=100) or 100)
    timeout = safe_float(source.get("duration_seconds"), default=60.0) or 60.0
    maximum_bytes = safe_int(config.section("http").get("max_response_bytes"), default=50_000_000) or 50_000_000
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
            eof_streak = 0
            while len(records) < maximum and time.monotonic() - started < timeout:
                if should_continue is not None and not should_continue():
                    break
                line_bytes = response.readline()
                if not line_bytes:
                    # EOF：服务端断开连接。连续多次空读判定为断开，避免忙循环占 CPU；
                    # 正常 SSE 的事件分隔空行会返回 b"\n"，不会被误判。
                    eof_streak += 1
                    if eof_streak >= 3:
                        if event:
                            data = {key: "\n".join(values) for key, values in event.items()}
                            records.append(ExtractedRecord(request.url, "sse_event", data))
                            event = {}
                        break
                    continue
                eof_streak = 0
                line = line_bytes.decode("utf-8", errors="replace").rstrip("\r\n")
                consumed += len(line_bytes)
                if consumed > maximum_bytes:
                    raise ResponseTooLargeError(f"SSE数据超过大小限制: > {maximum_bytes}")
                if not line:
                    if event:
                        data = {key: "\n".join(values) for key, values in event.items()}
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
    max_messages: int | None = None,
) -> list[ExtractedRecord]:
    try:
        import websockets
    except ImportError as exc:
        raise RuntimeError("缺少websockets，请安装 omnicrawl[streams]") from exc
    source = config.section("source")
    maximum = max_messages if max_messages is not None else (safe_int(source.get("max_messages"), default=100) or 100)
    duration = safe_float(source.get("duration_seconds"), default=60.0) or 60.0
    records: list[ExtractedRecord] = []
    started = time.monotonic()
    consumed = 0
    maximum_bytes = safe_int(config.section("http").get("max_response_bytes"), default=50_000_000) or 50_000_000
    headers = {**config.section("http").get("headers", {}), **source.get("headers", {}), **request.headers}
    broker = egress or EgressBroker(config)
    # B03-004：websockets 尊重 verify_tls（默认开启校验），与 HTTP 路径行为一致。
    verify_tls = bool(config.section("http").get("verify_tls", True))
    ssl_context = None
    if request.url.startswith(("wss://", "wss:")):
        import ssl

        ssl_context = ssl.create_default_context()
        if not verify_tls:
            ssl_context.check_hostname = False
            ssl_context.verify_mode = ssl.CERT_NONE
            LOGGER.warning("WebSocket 路径 verify_tls=false：TLS 校验已关闭（仅限受控内网站点）")
    with broker.request(request.url, purpose="stream", headers=headers):
        # B03-003：补 open_timeout，避免握手阶段无限挂起（recv 已有 wait_for 兜底）。
        async with websockets.connect(
            request.url,
            additional_headers=headers or None,
            ssl=ssl_context,
            open_timeout=max(0.1, min(30.0, safe_float(source.get("open_timeout_seconds"), default=30.0) or 30.0)),
        ) as socket:
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
                    parsed = safe_json_loads(message)
                    data = parsed if parsed is not None else {"text": message}
                records.append(ExtractedRecord(request.url, "websocket_message", data if isinstance(data, dict) else {"value": data}))
        broker.record_response(consumed, url=request.url)
    return records


def collect_websocket(
    config: AppConfig,
    request: CrawlRequest,
    should_continue: Callable[[], bool] | None = None,
    egress: EgressBroker | None = None,
    max_messages: int | None = None,
) -> list[ExtractedRecord]:
    return asyncio.run(_websocket_collect(config, request, should_continue, egress, max_messages))
