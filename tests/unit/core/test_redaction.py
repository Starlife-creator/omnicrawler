"""P9-A1：URL 凭据脱敏闭环（B05-024 + B08-007 + redact_url 工具）。"""

from __future__ import annotations

import logging

from omnicrawl.core.logging_utils import JsonFormatter
from omnicrawl.security.redaction import redact_url
from omnicrawl.services.research_package import _redact

# ── redact_url 工具 ───────────────────────────────────────────────


def test_redact_url_strips_credentials() -> None:
    assert redact_url("postgres://user:pass@db") == "postgres://user:<redacted>@db"
    assert redact_url("https://u:p@example.org/p") == "https://u:<redacted>@example.org/p"


def test_redact_url_keeps_plain_url() -> None:
    assert redact_url("https://example.org/page") == "https://example.org/page"


def test_redact_url_non_string_passthrough() -> None:
    assert redact_url("") == ""


# ── research_package（B08-007）───────────────────────────────────


def test_redact_nonstandard_connection_key() -> None:
    """db_url/dsn/conn 等非标准键整体隐藏（连接串可能内嵌凭据）。"""
    result = _redact({"db_url": "postgres://u:p@h/db"})
    assert result["db_url"].startswith("<redacted")
    result = _redact({"dsn": "oracle://user:pass@host:1521/sid"})
    assert result["dsn"].startswith("<redacted")


def test_redact_plain_url_key_keeps_info_but_hides_credentials() -> None:
    """普通 url 键保留信息，仅隐藏内嵌凭据。"""
    result = _redact({"url": "https://u:p@example.org/page"})
    assert result["url"] == "https://u:<redacted>@example.org/page"


def test_redact_traditional_sensitive_key() -> None:
    result = _redact({"password": "plain-secret"})
    assert result["password"].startswith("<redacted")


# ── logging_utils（B05-024）──────────────────────────────────────


def _record(message: str, **attrs) -> logging.LogRecord:
    rec = logging.LogRecord("t", logging.INFO, "mod", 1, message, (), None)
    for name, value in attrs.items():
        setattr(rec, name, value)
    return rec


def test_json_formatter_redacts_url_credentials_in_message() -> None:
    rec = _record("connect %s", args=("https://u:p@h/",))
    out = JsonFormatter().format(rec)
    assert "u:p@" not in out
    assert "<redacted>" in out


def test_json_formatter_redacts_url_and_site_attributes() -> None:
    rec = _record("fetched")
    rec.url = "https://u:p@h/x"
    rec.site = "https://s:p@h/"
    out = JsonFormatter().format(rec)
    assert "u:p@" not in out and "s:p@" not in out
    assert out.count("<redacted>") == 2


def test_json_formatter_keeps_plain_message() -> None:
    rec = _record("all good")
    out = JsonFormatter().format(rec)
    assert "all good" in out
    assert "<redacted>" not in out
