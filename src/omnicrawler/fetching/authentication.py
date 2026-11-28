"""Explicit authentication diagnostics; ordinary 401/403 responses remain unchanged."""

from __future__ import annotations

import hashlib
from urllib.parse import urlsplit

from ..core.config import AppConfig
from ..core.errors import SessionExpiredError
from ..core.models import CrawlRequest, FetchResult
from .session_state import context_key_for_request

# ★ 2026-11-28：`extraction.*` 改为**调用点导入**。
#
#   `extraction.extractors` 与 `extraction.html_tools`（连带 **bs4**，约 74 ms）
#   只在 `auth_check.selector` 存在时才用得到 —— 那是**少数**配置。
#
#   而本模块被 `runtime/recovery` 与 `gui/views/login_session` 拉进 GUI 启动路径，
#   于是「没配 auth_check 的用户」也在启动时付 bs4 的钱。
#
#   实测：`gui.views.login_session` 109.2 ms（含本模块 83.7 ms），
#   其中约 77 ms 是 `extraction.html_tools` 连带 bs4。


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
        # 只有真的要看 DOM 时才拉起抽取引擎（连带 bs4）。
        from ..extraction.extractors import decode_body
        from ..extraction.html_tools import parse_html, select_nodes

        if not select_nodes(parse_html(decode_body(result)), check["selector"]):
            return
    raise SessionExpiredError(session_scope(config, result.request))
