from __future__ import annotations

import copy
import hashlib
import json
import mimetypes
import os
import re
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, quote, urlencode, urljoin, urlsplit, urlunsplit

_ENV_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")


def utcnow() -> str:
    # S4.5 P3#128：微秒级精度——同秒多条记录不再无法区分
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def expand_env(value: Any) -> Any:
    """递归展开配置中的 ${VAR} / ${VAR:-default} 占位符（缺失变量替换为空串）。

    需要汇总缺失变量时请使用 :func:`expand_env_checked`。
    """
    expanded, _missing = expand_env_checked(value)
    return expanded


def expand_env_checked(value: Any) -> tuple[Any, list[str]]:
    """与 expand_env 相同，但额外返回缺失（无环境值且无默认）的变量名列表。

    S2.1.2 ③：${VAR} 缺失不再静默——调用方可据此给出 warning 汇总。
    """
    missing: list[str] = []

    def walk(item: Any) -> Any:
        if isinstance(item, str):
            def replace(match: re.Match[str]) -> str:
                name, default = match.group(1), match.group(2)
                if name not in os.environ and default is None and name not in missing:
                    missing.append(name)
                return os.environ.get(name, default or "")
            return _ENV_RE.sub(replace, item)
        if isinstance(item, list):
            return [walk(child) for child in item]
        if isinstance(item, dict):
            return {key: walk(child) for key, child in item.items()}
        return item

    return walk(value), missing


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(base)
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
    # S4.5 P3#126：保留 userinfo（urlsplit 的 username/password 不再被丢弃）
    userinfo = ""
    if parts.username:
        userinfo = quote(parts.username, safe="")
        if parts.password:
            userinfo += ":" + quote(parts.password, safe="")
        userinfo += "@"
    host = userinfo + display_host if not port or default_port else userinfo + f"{display_host}:{port}"
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


def excel_safe(value: Any, max_length: int = 32700) -> Any:
    """Excel/CSV 单元格安全化（S4.4 ③：pdfx.safe_cell 统一委托此处）。

    - 截断超长字符串（Excel 单格上限 32767）
    - 公式注入防护：以 = + - @ 开头且非纯数字的字符串前加单引号
    """
    if value is None:
        return ""
    if not isinstance(value, str):
        return value
    value = value[:max_length]
    # S4.5 P3#127：数字含科学计数法（1e10）不被误伤
    if value.lstrip("\t\r\n ").startswith(("=", "+", "-", "@")):
        if not re.fullmatch(r"[-+]?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?", value):
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
