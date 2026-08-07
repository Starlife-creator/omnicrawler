"""Curated plugin-market client.

Fetches the catalog from a configurable base URL (GitHub raw by default,
mirror/self-hosted for migration) and installs signed plugins after an
offline ed25519 verification. No network trust is used — only the bundled
trust root validates downloads (fail-closed).

Migration note: the catalog base is fully determined by ``catalog_url``.
Moving the ``registry/`` subtree to a new repo/service only requires changing
that one value; every path inside ``catalog.json`` is relative to it.
"""

from __future__ import annotations

import json
import re
import urllib.request
from pathlib import Path
from typing import Any

from ..security.egress import EgressBroker
from .signing import verify_bytes

DEFAULT_TIMEOUT = 15.0
_ID_RE = re.compile(r"^[a-z][a-z0-9_-]{1,63}$")
_USER_AGENT = "OmniCrawler-Market/1.0"


def _is_remote(url: str) -> bool:
    return url.startswith(("http://", "https://"))


def _read(
    url_or_path: str,
    *,
    timeout: float = DEFAULT_TIMEOUT,
    egress: EgressBroker | None = None,
) -> bytes:
    if _is_remote(url_or_path):
        request = urllib.request.Request(
            url_or_path, headers={"User-Agent": _USER_AGENT}
        )
        if egress is None:
            with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
                return response.read()
        with egress.request(url_or_path, purpose="plugin", headers=request.headers):
            with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
                return response.read()
    candidate = Path(url_or_path)
    if candidate.is_file():
        return candidate.read_bytes()
    raise FileNotFoundError(f"无法读取资源: {url_or_path}")


def _join(base: str, rel: str) -> str:
    """Join a catalog-relative path to the catalog base URL/path."""
    if _is_remote(base):
        return base.rstrip("/") + "/" + rel.lstrip("/")
    return str(Path(base) / rel)


def fetch_resource(
    catalog_url: str,
    rel: str,
    *,
    timeout: float = DEFAULT_TIMEOUT,
    egress: EgressBroker | None = None,
) -> bytes:
    """Fetch an arbitrary catalog-relative resource (e.g. a listing file)."""
    return _read(_join(catalog_url, rel), timeout=timeout, egress=egress)


def fetch_catalog(
    catalog_url: str,
    *,
    timeout: float = DEFAULT_TIMEOUT,
    egress: EgressBroker | None = None,
) -> dict[str, Any]:
    """Fetch and parse ``catalog.json`` from ``catalog_url`` (remote or local)."""
    raw = fetch_resource(catalog_url, "catalog.json", timeout=timeout, egress=egress)
    try:
        data = json.loads(raw.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValueError(f"catalog.json 解析失败: {exc}") from exc
    if not isinstance(data.get("plugins"), list):
        raise ValueError("catalog.json 缺少 plugins 数组")
    return data


def resolve_entry(catalog: dict[str, Any], plugin_id: str) -> dict[str, Any]:
    for entry in catalog.get("plugins", []):
        if entry.get("id") == plugin_id:
            return entry
    raise KeyError(f"catalog 中无此插件: {plugin_id}")


def download_and_verify(
    plugin_id: str,
    catalog_url: str,
    dest_root: str | Path,
    trust_source: str,
    *,
    timeout: float = DEFAULT_TIMEOUT,
    egress: EgressBroker | None = None,
) -> Path:
    """Download a plugin, verify its detached signature, and install it.

    Installs into ``dest_root/<plugin_id>/`` as ``plugin.py`` + ``plugin.py.sig``
    (+ ``listing.md`` when the catalog entry provides one). Raises on a bad id,
    download failure, or a signature mismatch (fail-closed).
    """
    if not _ID_RE.match(plugin_id):
        raise ValueError(f"非法插件 ID: {plugin_id}")
    catalog = fetch_catalog(catalog_url, timeout=timeout, egress=egress)
    entry = resolve_entry(catalog, plugin_id)
    plugin_bytes = fetch_resource(catalog_url, entry["plugin_file"], timeout=timeout, egress=egress)
    sig_bytes = fetch_resource(catalog_url, entry["signature_file"], timeout=timeout, egress=egress)
    if not verify_bytes(plugin_bytes, sig_bytes, trust_source):
        raise PermissionError(f"插件 {plugin_id} 签名校验失败（fail-closed 拒载）")

    dest_dir = Path(dest_root) / plugin_id
    dest_dir.mkdir(parents=True, exist_ok=True)
    plugin_path = dest_dir / "plugin.py"
    plugin_path.write_bytes(plugin_bytes)
    (dest_dir / "plugin.py.sig").write_bytes(sig_bytes)
    listing_rel = entry.get("description_file")
    if listing_rel:
        try:
            (dest_dir / "listing.md").write_bytes(
                fetch_resource(catalog_url, listing_rel, timeout=timeout, egress=egress)
            )
        except (FileNotFoundError, OSError):
            pass  # listing 是可选增强，不影响安装
    return plugin_path


def verify_installed(dest_root: str | Path, plugin_id: str, trust_source: str) -> tuple[bool, str]:
    """Re-verify an already-installed plugin against the trust root."""
    dest_dir = Path(dest_root) / plugin_id
    plugin_path = dest_dir / "plugin.py"
    sig_path = dest_dir / "plugin.py.sig"
    if not plugin_path.is_file() or not sig_path.is_file():
        return False, "插件或签名文件缺失"
    ok = verify_bytes(plugin_path.read_bytes(), sig_path.read_bytes(), trust_source)
    return ok, "verified" if ok else "签名校验失败"
