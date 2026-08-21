"""客户端 catalog 信任链（Phase 2a G1/G2/G3）。

三层防线：
- G1：先验签 catalog.json（catalog.json.sig）再信任其 sha256 清单；下载插件
  后对比实测 sha256 vs catalog 声明——time-of-check 后门防线。
- G2：revoked/deprecated 状态裁决——命中吊销即禁运行（优先级最高，豁免表
  /legacy_in_process/sandbox_escape 均不能复活），审计 decision=revoked_denied。
- G3：防重放——拒绝 sequence/generated_at 旧于本地缓存的 catalog
  （decision=catalog_stale_rejected），吊销不可被旧版 catalog 隐藏。

全部 fail-closed：验签失败/哈希不匹配/命中吊销 → 拒绝，不静默降级。
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import signing

LOGGER = logging.getLogger(__name__)


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def verify_catalog_signature(catalog_bytes: bytes, sig_bytes: bytes, trust_source: str) -> bool:
    """G1：验签 catalog.json（先验签再信任其 sha256 清单）。"""
    return signing.verify_bytes(catalog_bytes, sig_bytes, trust_source)


def check_download_hash(
    plugin_bytes: bytes, entry: dict[str, Any]
) -> tuple[bool, str]:
    """G1：下载内容 sha256 vs catalog 声明（time-of-check 后门防线）。

    catalog versions[version].sha256 存在 → 严格比对；
    存量无哈希（sha256_unknown）→ 提示不阻断（G1 存量迁移语义）。
    返回 (ok, reason)。
    """
    version = str(entry.get("version", ""))
    versions = entry.get("versions") or {}
    version_info = versions.get(version) or {}
    declared = str(version_info.get("sha256", "")).strip()
    if not declared:
        if version_info.get("sha256_unknown"):
            return True, "存量无 sha256（sha256_unknown，仅提示不阻断）"
        return True, "catalog 未固化 sha256（旧 catalog，跳过哈希校验）"
    actual = sha256_hex(plugin_bytes)
    if actual != declared:
        return False, (
            f"sha256 不匹配（declared={declared[:16]}… actual={actual[:16]}…）——"
            "内容可能被篡改或缓存陈旧（decision: hash_mismatch）"
        )
    return True, "sha256 匹配"


def check_revocation(entry: dict[str, Any], version: str | None = None) -> tuple[bool, str]:
    """G2：吊销/弃用状态裁决。返回 (allowed, reason)。

    - revoked（整插件吊销）或 revoked_versions 命中当前版本 → 禁运行
    - deprecated → 允许但提示（作者停更/迁移）
    吊销优先级最高：豁免表/逃生开关均不能复活（调用方保证）。
    """
    revoked = entry.get("revoked")
    if revoked:
        reason = revoked.get("reason", "") if isinstance(revoked, dict) else str(revoked)
        return False, f"插件已被吊销（{reason}）——decision: revoked_denied"
    target_version = version or str(entry.get("version", ""))
    revoked_versions = entry.get("revoked_versions") or []
    if target_version in revoked_versions:
        return False, f"版本 {target_version} 已被吊销——decision: revoked_denied"
    deprecated = entry.get("deprecated")
    if deprecated:
        reason = deprecated.get("reason", "") if isinstance(deprecated, dict) else str(deprecated)
        return True, f"插件已弃用（{reason}），建议迁移"
    return True, ""


def check_catalog_stale(
    incoming: dict[str, Any], cached: dict[str, Any] | None
) -> tuple[bool, str]:
    """G3：防重放——拒绝 sequence/generated_at 旧于本地缓存的 catalog。

    返回 (accepted, reason)。cached 为 None（首次）→ 接受。
    攻击者/恶意镜像提供旧版 catalog（无吊销记录）隐藏吊销 → 拒绝。
    """
    if cached is None:
        return True, "首次缓存，接受"
    in_seq = _as_int(incoming.get("sequence"))
    cached_seq = _as_int(cached.get("sequence"))
    if in_seq is not None and cached_seq is not None and in_seq < cached_seq:
        return False, (
            f"catalog 序列号回退（incoming={in_seq} < cached={cached_seq}）——"
            "疑似重放攻击（decision: catalog_stale_rejected）"
        )
    in_ts = _parse_ts(incoming.get("generated_at"))
    cached_ts = _parse_ts(cached.get("generated_at"))
    if in_ts is not None and cached_ts is not None and in_ts < cached_ts:
        return False, (
            "catalog generated_at 旧于本地缓存——疑似重放（decision: catalog_stale_rejected）"
        )
    return True, "catalog 新鲜度通过"


def _as_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _parse_ts(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def load_cached_catalog(cache_path: Path) -> dict[str, Any] | None:
    """读取本地缓存的 catalog（防重放比对用）；损坏返回 None。"""
    try:
        return json.loads(cache_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def save_cached_catalog(cache_path: Path, catalog: dict[str, Any]) -> None:
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(catalog, ensure_ascii=False, indent=2), encoding="utf-8")
