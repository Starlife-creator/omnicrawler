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

import hashlib
import json
import logging
import re
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..core.errors import PolicyBlockedError
from ..security.egress import EgressBroker
from .signing import verify_bytes

LOGGER = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 15.0
_ID_RE = re.compile(r"^[a-z][a-z0-9_-]{1,63}$")
# 模板 ID 允许层级命名（如 generic/single-page）；禁止 .. / 空段 / 首尾斜杠
_TEMPLATE_ID_RE = re.compile(r"^[a-z][a-z0-9_-]*(?:/[a-z0-9_-]+)*$")
_USER_AGENT = "OmniCrawler-Market/1.0"
# 安装元数据锁文件：记录安装时的文件哈希，防市场仓库被篡改后已装文件被静默替换
_INSTALL_META = ".omnicrawl-install.json"


def _is_remote(url: str) -> bool:
    return url.startswith(("http://", "https://"))


def _read(
    url_or_path: str,
    *,
    timeout: float = DEFAULT_TIMEOUT,
    egress: EgressBroker | None = None,
) -> bytes:
    if _is_remote(url_or_path):
        request = urllib.request.Request(url_or_path, headers={"User-Agent": _USER_AGENT})
        if egress is None:
            # B01-011：egress=None 时拒绝出网（fail-closed）——市场下载必须显式
            # 提供出口策略，杜绝裸 urlopen 绕过策略/预算/审计边界。
            err = PolicyBlockedError(
                f"市场下载缺少出口策略，已阻止访问: {url_or_path}"
            )
            err.suggestion = "请通过 GUI 市场面板发起下载（内部会构建 EgressBroker）"
            raise err
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


def resolve_template_entry(catalog: dict[str, Any], template_id: str) -> dict[str, Any]:
    for entry in catalog.get("templates", []):
        if entry.get("id") == template_id:
            return entry
    raise KeyError(f"catalog 中无此模板: {template_id}")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _write_install_meta(
    dest_dir: Path,
    *,
    plugin_id: str,
    main_name: str,
    sig_name: str,
    main_bytes: bytes,
    sig_bytes: bytes,
    extra_files: dict[str, bytes] | None = None,
) -> None:
    """写安装元数据锁文件（插件/模板通用）。

    ``extra_files``：额外落盘文件（如创作者轨 creator.sig/creator.identity）的
    {文件名: 字节} 映射，其 sha256 一并记录进锁文件，防被单独替换（B01-003）。
    """
    meta: dict[str, Any] = {
        "schema_version": 1,
        "plugin_id": plugin_id,
        "plugin_file": main_name,
        "plugin_sha256": _sha256(main_bytes),
        "signature_file": sig_name,
        "signature_sha256": _sha256(sig_bytes),
        "installed_at": datetime.now(timezone.utc).isoformat(),
    }
    if extra_files:
        meta["extra_files"] = {name: _sha256(data) for name, data in extra_files.items()}
    (dest_dir / _INSTALL_META).write_text(
        json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def _verify_install_meta(dest_dir: Path, *, main_name: str) -> tuple[bool, str]:
    """校验安装元数据；无元数据（旧版安装）退化为纯签名校验。"""
    meta_path = dest_dir / _INSTALL_META
    if not meta_path.is_file():
        return True, ""
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        return False, f"安装元数据损坏: {exc}"
    if meta.get("plugin_file") != main_name:
        return False, "安装元数据与插件文件不匹配"
    current = _sha256((dest_dir / main_name).read_bytes())
    if current != meta.get("plugin_sha256"):
        return False, "安装哈希校验失败（插件文件与安装时记录不一致）"
    return True, ""


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
    # B01-003：可选下载创作者轨（creator.sig + creator.identity），保留作者归属信息。
    # 字段缺失时不影响安装（向后兼容）；下载失败仅告警，作者展示降级为不可用。
    extra_files: dict[str, bytes] = {}
    for field, filename in (("creator_signature_file", "creator.sig"),
                            ("creator_identity_file", "creator.identity")):
        rel = entry.get(field)
        if not rel:
            continue
        try:
            extra_files[filename] = fetch_resource(catalog_url, rel, timeout=timeout, egress=egress)
            (dest_dir / filename).write_bytes(extra_files[filename])
        except (FileNotFoundError, OSError) as exc:
            LOGGER.warning("创作者轨资源缺失，作者归属将不可用: %s（%s）", rel, exc)
    _write_install_meta(
        dest_dir,
        plugin_id=plugin_id,
        main_name="plugin.py",
        sig_name="plugin.py.sig",
        main_bytes=plugin_bytes,
        sig_bytes=sig_bytes,
        extra_files=extra_files or None,
    )
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
    """Re-verify an already-installed plugin against the trust root.

    校验顺序：安装哈希（存在锁文件时）→ 签名。任一失败即 fail-closed。
    """
    dest_dir = Path(dest_root) / plugin_id
    plugin_path = dest_dir / "plugin.py"
    sig_path = dest_dir / "plugin.py.sig"
    if not plugin_path.is_file() or not sig_path.is_file():
        return False, "插件或签名文件缺失"
    ok, meta_reason = _verify_install_meta(dest_dir, main_name="plugin.py")
    if not ok:
        return False, meta_reason
    ok = verify_bytes(plugin_path.read_bytes(), sig_path.read_bytes(), trust_source)
    return ok, "verified" if ok else "签名校验失败"


def download_template_and_verify(
    template_id: str,
    catalog_url: str,
    dest_root: str | Path,
    trust_source: str,
    *,
    timeout: float = DEFAULT_TIMEOUT,
    egress: EgressBroker | None = None,
) -> Path:
    """Download a market template, verify its detached signature, and install it.

    Installs into ``dest_root/<template_id>/`` as ``template.yaml`` +
    ``template.yaml.sig`` (+ ``listing.md`` when present). Raises on a bad id,
    download failure, or a signature mismatch (fail-closed). Installed templates
    are discovered by ``TemplateCatalog`` via ``user_dirs``.
    """
    if not _TEMPLATE_ID_RE.match(template_id) or ".." in template_id:
        raise ValueError(f"非法模板 ID: {template_id}")
    catalog = fetch_catalog(catalog_url, timeout=timeout, egress=egress)
    entry = resolve_template_entry(catalog, template_id)
    template_bytes = fetch_resource(catalog_url, entry["template_file"], timeout=timeout, egress=egress)
    sig_bytes = fetch_resource(catalog_url, entry["signature_file"], timeout=timeout, egress=egress)
    if not verify_bytes(template_bytes, sig_bytes, trust_source):
        raise PermissionError(f"模板 {template_id} 签名校验失败（fail-closed 拒载）")

    dest_dir = Path(dest_root) / template_id
    dest_dir.mkdir(parents=True, exist_ok=True)
    template_path = dest_dir / "template.yaml"
    template_path.write_bytes(template_bytes)
    (dest_dir / "template.yaml.sig").write_bytes(sig_bytes)
    _write_install_meta(
        dest_dir,
        plugin_id=template_id,
        main_name="template.yaml",
        sig_name="template.yaml.sig",
        main_bytes=template_bytes,
        sig_bytes=sig_bytes,
    )
    listing_rel = entry.get("description_file")
    if listing_rel:
        try:
            (dest_dir / "listing.md").write_bytes(
                fetch_resource(catalog_url, listing_rel, timeout=timeout, egress=egress)
            )
        except (FileNotFoundError, OSError):
            pass
    return template_path


def verify_installed_template(dest_root: str | Path, template_id: str, trust_source: str) -> tuple[bool, str]:
    """Re-verify an already-installed market template against the trust root."""
    dest_dir = Path(dest_root) / template_id
    template_path = dest_dir / "template.yaml"
    sig_path = dest_dir / "template.yaml.sig"
    if not template_path.is_file() or not sig_path.is_file():
        return False, "模板或签名文件缺失"
    ok, meta_reason = _verify_install_meta(dest_dir, main_name="template.yaml")
    if not ok:
        return False, meta_reason
    ok = verify_bytes(template_path.read_bytes(), sig_path.read_bytes(), trust_source)
    return ok, "verified" if ok else "签名校验失败"
