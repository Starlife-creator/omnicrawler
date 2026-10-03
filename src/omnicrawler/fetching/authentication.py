"""Explicit authentication diagnostics; ordinary 401/403 responses remain unchanged."""

from __future__ import annotations

import hashlib
from urllib.parse import urlsplit

from ..core.config import AppConfig
from ..core.errors import SessionExpiredError
from ..core.models import CrawlRequest, FetchResult
from ..extraction.extractors import decode_body
from ..extraction.html_tools import parse_html, select_nodes
from .session_state import context_key_for_request


def session_scope(config: AppConfig, request: CrawlRequest) -> str:
    host = (urlsplit(request.url).hostname or "").casefold().rstrip(".")
    identity = context_key_for_request(config, request)
    return hashlib.sha256(f"{identity}|{host}".encode()).hexdigest()


def check_authentication(config: AppConfig, result: FetchResult) -> None:
    check = config.section("source").get("auth_check")
    if not check or result.request.kind == "asset":
        return
    if "status_codes" in check and result.status not in check["status_codes"]:
        return
    if "redirect_paths" in check:
        if result.final_url == result.request.url or urlsplit(result.final_url).path not in check["redirect_paths"]:
            return
    if "selector" in check:
        if result.content_type not in {"", "text/html", "application/xhtml+xml"}:
            return
        if not select_nodes(parse_html(decode_body(result)), check["selector"]):
            return
    raise SessionExpiredError(session_scope(config, result.request))
