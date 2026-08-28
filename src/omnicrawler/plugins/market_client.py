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
import os
import re
import shutil
import tempfile
import urllib.request
from datetime import UTC, datetime
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
_INSTALL_META = ".omnicrawler-install.json"


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


def catalog_cache_path(cache_root: str | Path, catalog_url: str) -> Path:
    """Return a source-specific replay cache path without exposing the URL."""
    source_id = hashlib.sha256(catalog_url.encode("utf-8")).hexdigest()[:24]
    return Path(cache_root) / f"catalog-{source_id}.json"


def fetch_catalog_verified(
    catalog_url: str,
    trust_source: str,
    *,
    cache_path: str | Path | None = None,
    timeout: float = DEFAULT_TIMEOUT,
    egress: EgressBroker | None = None,
) -> dict[str, Any]:
    """Fetch a catalog only after signature and anti-replay verification.

    Parsing happens after signature verification so no attacker-controlled
    catalog field is consumed before the market trust root has authenticated
    the exact bytes.  When ``cache_path`` is supplied, an older signed catalog
    is rejected and the cache advances only after all checks pass.
    """
    from .catalog_trust import (
        check_catalog_stale,
        load_cached_catalog,
        save_cached_catalog,
        verify_catalog_signature,
    )

    raw = fetch_resource(catalog_url, "catalog.json", timeout=timeout, egress=egress)
    signature = fetch_resource(
        catalog_url, "catalog.json.sig", timeout=timeout, egress=egress
    )
    if not verify_catalog_signature(raw, signature, trust_source):
        raise PermissionError("catalog.json 签名校验失败（fail-closed）")
    try:
        catalog = json.loads(raw.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValueError(f"catalog.json 解析失败: {exc}") from exc
    if not isinstance(catalog, dict) or not isinstance(catalog.get("plugins"), list):
        raise ValueError("catalog.json 缺少 plugins 数组")
    if "templates" in catalog and not isinstance(catalog["templates"], list):
        raise ValueError("catalog.json templates 必须是数组")

    resolved_cache = Path(cache_path) if cache_path is not None else None
    cached = load_cached_catalog(resolved_cache) if resolved_cache is not None else None
    accepted, reason = check_catalog_stale(catalog, cached)
    if not accepted:
        raise PermissionError(reason)
    if resolved_cache is not None:
        save_cached_catalog(resolved_cache, catalog)
    return catalog


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
        "installed_at": datetime.now(UTC).isoformat(),
    }
    if extra_files:
        meta["extra_files"] = {name: _sha256(data) for name, data in extra_files.items()}
    (dest_dir / _INSTALL_META).write_text(
        json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def _verify_install_meta(dest_dir: Path, *, main_name: str) -> tuple[bool, str]:
    """校验安装元数据。

    B01-010：元数据缺失时**显式记录并退化为纯签名校验**（旧版安装兼容），
    而非静默通过——签名校验始终执行，锁文件仅提供哈希级防篡改的额外层。
    """
    meta_path = dest_dir / _INSTALL_META
    if not meta_path.is_file():
        LOGGER.info(
            "安装元数据不存在（旧版安装或无锁文件），退化为纯签名校验: %s", dest_dir
        )
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


def _download_manifest_package(
    entry: dict[str, Any],
    catalog_url: str,
    dest_dir: Path,
    trust_source: str,
    *,
    main_name: str,
    timeout: float,
    egress: EgressBroker | None,
) -> Path:
    """Download and verify every creator-signed payload byte before install."""
    from pathlib import PurePosixPath

    from .identity import public_key_bytes_from_pem
    from .package_manifest import (
        CREATOR_SIGNATURE_NAME,
        MAINTAINER_SIGNATURE_NAME,
        MANIFEST_NAME,
        verify_package,
    )

    manifest_rel = str(entry["package_manifest_file"])
    manifest_bytes = fetch_resource(catalog_url, manifest_rel, timeout=timeout, egress=egress)
    manifest = json.loads(manifest_bytes.decode("utf-8"))
    files = manifest.get("files")
    if not isinstance(files, dict) or not files:
        raise PermissionError("市场 package manifest 的 files 非法")
    base = PurePosixPath(manifest_rel).parent
    dest_dir.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="omnicrawl-market-", dir=dest_dir.parent) as temp:
        staging = Path(temp) / "package"
        staging.mkdir()
        (staging / MANIFEST_NAME).write_bytes(manifest_bytes)
        signatures = {
            CREATOR_SIGNATURE_NAME: str(entry["creator_package_signature_file"]),
            MAINTAINER_SIGNATURE_NAME: str(entry["maintainer_package_signature_file"]),
            "creator.sig": str(entry.get("creator_signature_file") or ""),
            f"{main_name}.sig": str(entry["signature_file"]),
        }
        for name, rel in signatures.items():
            if rel:
                (staging / name).write_bytes(
                    fetch_resource(catalog_url, rel, timeout=timeout, egress=egress)
                )
        for rel in sorted(files):
            safe = PurePosixPath(str(rel))
            if safe.is_absolute() or ".." in safe.parts or "\\" in str(rel):
                raise PermissionError(f"市场包包含非法路径: {rel}")
            target = staging / Path(*safe.parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(
                fetch_resource(
                    catalog_url,
                    (base / safe).as_posix(),
                    timeout=timeout,
                    egress=egress,
                )
            )
        verified = verify_package(
            staging,
            maintainer_public_key=public_key_bytes_from_pem(trust_source),
            require_maintainer=True,
        )
        if verified.manifest_sha256 != entry.get("package_manifest_sha256"):
            raise PermissionError("市场包 manifest 哈希与 catalog 不一致")
        if verified.package_id != entry.get("id") or verified.version != entry.get("version"):
            raise PermissionError("市场包 ID/版本与 catalog 不一致")
        if dest_dir.exists():
            backup = dest_dir.with_name(dest_dir.name + ".previous")
            if backup.exists():
                raise PermissionError(f"上次升级备份尚未处理: {backup}")
            dest_dir.replace(backup)
            try:
                os.replace(staging, dest_dir)
            except Exception:
                backup.replace(dest_dir)
                raise
            shutil.rmtree(backup)
        else:
            os.replace(staging, dest_dir)
    return dest_dir / main_name


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
    cache = catalog_cache_path(Path(dest_root) / ".catalog-cache", catalog_url)
    catalog = fetch_catalog_verified(
        catalog_url,
        trust_source,
        cache_path=cache,
        timeout=timeout,
        egress=egress,
    )
    entry = resolve_entry(catalog, plugin_id)
    if entry.get("package_manifest_file"):
        return _download_manifest_package(
            entry,
            catalog_url,
            Path(dest_root) / plugin_id,
            trust_source,
            main_name="plugin.py",
            timeout=timeout,
            egress=egress,
        )
    plugin_bytes = fetch_resource(catalog_url, entry["plugin_file"], timeout=timeout, egress=egress)
    sig_bytes = fetch_resource(catalog_url, entry["signature_file"], timeout=timeout, egress=egress)
    if not verify_bytes(plugin_bytes, sig_bytes, trust_source):
        raise PermissionError(f"插件 {plugin_id} 签名校验失败（fail-closed 拒载）")

    # G1（time-of-check 后门防线）：下载内容 sha256 vs catalog 固化哈希。
    # CI 绿后作者改内容 → 此处发现即拒装（decision: hash_mismatch）。
    from .catalog_trust import check_download_hash, check_revocation

    hash_ok, hash_reason = check_download_hash(plugin_bytes, entry)
    if not hash_ok:
        raise PermissionError(f"插件 {plugin_id} 下载校验失败: {hash_reason}（fail-closed 拒装）")
    if hash_reason and "sha256_unknown" in hash_reason:
        LOGGER.info("插件 %s 存量无 sha256，跳过哈希校验: %s", plugin_id, hash_reason)

    # G2：吊销/弃用状态裁决——命中吊销禁安装（优先级最高）。
    allowed, revocation_reason = check_revocation(entry)
    if not allowed:
        raise PermissionError(f"插件 {plugin_id} 拒绝安装: {revocation_reason}")
    if revocation_reason:
        LOGGER.warning("插件 %s: %s", plugin_id, revocation_reason)

    dest_dir = Path(dest_root) / plugin_id
    if (dest_dir / "package.manifest.json").is_file():
        raise PermissionError(
            f"插件 {plugin_id} 已安装完整签名包，拒绝用旧式单文件目录覆盖"
        )
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
    if (dest_dir / "package.manifest.json").is_file():
        try:
            from .identity import public_key_bytes_from_pem
            from .package_manifest import verify_package

            verify_package(
                dest_dir,
                maintainer_public_key=public_key_bytes_from_pem(trust_source),
                require_maintainer=True,
            )
        except Exception as exc:  # noqa: BLE001
            return False, f"完整包校验失败: {exc}"
        return True, "verified-package"
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
    cache = catalog_cache_path(Path(dest_root) / ".catalog-cache", catalog_url)
    catalog = fetch_catalog_verified(
        catalog_url,
        trust_source,
        cache_path=cache,
        timeout=timeout,
        egress=egress,
    )
    entry = resolve_template_entry(catalog, template_id)
    if entry.get("package_manifest_file"):
        return _download_manifest_package(
            entry,
            catalog_url,
            Path(dest_root) / Path(*template_id.split("/")),
            trust_source,
            main_name="template.yaml",
            timeout=timeout,
            egress=egress,
        )
    template_bytes = fetch_resource(catalog_url, entry["template_file"], timeout=timeout, egress=egress)
    sig_bytes = fetch_resource(catalog_url, entry["signature_file"], timeout=timeout, egress=egress)
    if not verify_bytes(template_bytes, sig_bytes, trust_source):
        raise PermissionError(f"模板 {template_id} 签名校验失败（fail-closed 拒载）")

    dest_dir = Path(dest_root) / template_id
    if (dest_dir / "package.manifest.json").is_file():
        raise PermissionError(
            f"模板 {template_id} 已安装完整签名包，拒绝用旧式单文件目录覆盖"
        )
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
