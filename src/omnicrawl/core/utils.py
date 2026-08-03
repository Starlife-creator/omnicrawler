from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import re
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

_ENV_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")


def utcnow() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def expand_env(value: Any) -> Any:
    if isinstance(value, str):
        def replace(match: re.Match[str]) -> str:
            name, default = match.group(1), match.group(2)
            return os.environ.get(name, default or "")
        return _ENV_RE.sub(replace, value)
    if isinstance(value, list):
        return [expand_env(item) for item in value]
    if isinstance(value, dict):
        return {key: expand_env(item) for key, item in value.items()}
    return value


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def canonicalize_url(base_url: str, href: str, *, sort_query: bool = False) -> str | None:
    try:
        joined = urljoin(base_url, href.strip())
        parts = urlsplit(joined)
    except ValueError:
        return None
    if parts.scheme.lower() not in {"http", "https"} or not parts.netloc:
        return None
    hostname = (parts.hostname or "").lower()
    if not hostname:
        return None
    port = parts.port
    default_port = (parts.scheme.lower() == "http" and port == 80) or (
        parts.scheme.lower() == "https" and port == 443
    )
    display_host = f"[{hostname}]" if ":" in hostname else hostname
    host = display_host if not port or default_port else f"{display_host}:{port}"
    query = urlencode(sorted(parse_qsl(parts.query, keep_blank_values=True))) if sort_query else parts.query
    return urlunsplit((parts.scheme.lower(), host, parts.path or "/", query, ""))


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def safe_filename(url: str, content_type: str = "", disposition: str = "") -> str:
    name = ""
    encoded = re.search(r"filename\*\s*=\s*UTF-8''([^;]+)", disposition, flags=re.I)
    plain = re.search(r'filename\s*=\s*"?([^";]+)', disposition, flags=re.I)
    if encoded:
        from urllib.parse import unquote
        name = unquote(encoded.group(1))
    elif plain:
        name = plain.group(1).strip()
    if not name:
        name = Path(urlsplit(url).path).name or "index"
    name = re.sub(r"[\\/:*?\"<>|\x00-\x1f]+", "_", name).strip(" ._") or "file"
    if "." not in name and content_type:
        name += mimetypes.guess_extension(content_type.split(";", 1)[0]) or ""
    digest = hashlib.sha1(url.encode("utf-8"), usedforsecurity=False).hexdigest()[:10]
    path = Path(name)
    return f"{path.stem[:100]}_{digest}{path.suffix[:16].lower()}"


def atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        # On Windows another process can hold the destination briefly while it
        # reads the previous control/config file.  Retrying the atomic rename
        # preserves all-or-nothing semantics without falling back to an unsafe
        # in-place write.
        for attempt in range(6):
            try:
                os.replace(temp_name, path)
                break
            except PermissionError:
                if attempt == 5:
                    raise
                time.sleep(0.01 * (2**attempt))
    except Exception:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise


def json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def excel_safe(value: Any) -> Any:
    if isinstance(value, str) and value.startswith(("=", "+", "-", "@")):
        if not re.fullmatch(r"[-+]?\d+(?:\.\d+)?", value):
            return "'" + value
    return value


def user_agent(suffix: str = "") -> str:
    """Generate a version-tracking User-Agent string automatically.

    The returned string always mirrors the current :data:`omnicrawl.__version__`,
    so a single ``python tools/bump_version.py X.Y.Z`` updates every User-Agent in
    the codebase.

    *suffix* is appended after a space (no parentheses are added automatically).
    Typical values: ``"+contact: change-me@example.com"``, ``"+bot"``,
    ``"scoped operation"``, ``"Probe"``.
    """
    from .. import __version__
    base = f"OmniCrawler/{__version__}"
    return f"{base} {suffix}" if suffix else base


def redact_headers(headers: dict[str, str]) -> dict[str, str]:
    sensitive = {"authorization", "proxy-authorization", "cookie", "set-cookie", "x-api-key", "api-key"}
    return {
        key: value
        for key, value in headers.items()
        if key.casefold() not in sensitive and not key.casefold().endswith("-api-key")
    }
