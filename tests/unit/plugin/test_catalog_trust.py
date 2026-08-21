"""Phase 2a G1/G2/G3 客户端 catalog 信任链契约测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from omnicrawler.plugins import catalog_trust as ct

pytestmark = pytest.mark.plugin_contract


def _entry(version="1.0.0", **extra):
    base = {
        "id": "demo", "version": version,
        "versions": {version: {"sha256": ct.sha256_hex(b"plugin-content")}},
    }
    base.update(extra)
    return base


def test_download_hash_match_ok() -> None:
    ok, reason = ct.check_download_hash(b"plugin-content", _entry())
    assert ok and "匹配" in reason


def test_download_hash_mismatch_rejected() -> None:
    """G1：篡改内容 → 拒装（hash_mismatch）。"""
    ok, reason = ct.check_download_hash(b"tampered-content", _entry())
    assert not ok and "hash_mismatch" in reason


def test_download_hash_unknown_legacy_passes() -> None:
    """G1 存量迁移：sha256_unknown 仅提示不阻断。"""
    entry = {"version": "0.9", "versions": {"0.9": {"sha256_unknown": True}}}
    ok, reason = ct.check_download_hash(b"anything", entry)
    assert ok and "sha256_unknown" in reason


def test_revocation_denies_run() -> None:
    """G2：整插件吊销 → 禁运行。"""
    entry = _entry(revoked={"reason": "malicious", "revoked_at": "2026-08-21"})
    allowed, reason = ct.check_revocation(entry)
    assert not allowed and "revoked_denied" in reason


def test_revoked_version_denies() -> None:
    """G2：按版本吊销命中当前版本 → 禁。"""
    entry = _entry(version="1.0.0", revoked_versions=["1.0.0"])
    allowed, reason = ct.check_revocation(entry)
    assert not allowed
    # 其他版本不受影响
    allowed2, _ = ct.check_revocation(entry, version="1.1.0")
    assert allowed2


def test_deprecated_allows_with_warning() -> None:
    entry = _entry(deprecated={"reason": "moved to new plugin"})
    allowed, reason = ct.check_revocation(entry)
    assert allowed and "弃用" in reason


def test_anti_replay_rejects_stale_sequence() -> None:
    """G3：sequence 回退 → 拒绝（catalog_stale_rejected）。"""
    cached = {"sequence": 100, "generated_at": "2026-08-21T00:00:00+00:00"}
    incoming = {"sequence": 50, "generated_at": "2026-08-21T01:00:00+00:00"}
    accepted, reason = ct.check_catalog_stale(incoming, cached)
    assert not accepted and "catalog_stale_rejected" in reason


def test_anti_replay_rejects_stale_timestamp() -> None:
    cached = {"sequence": 100, "generated_at": "2026-08-21T10:00:00+00:00"}
    incoming = {"sequence": 100, "generated_at": "2026-08-21T09:00:00+00:00"}
    accepted, reason = ct.check_catalog_stale(incoming, cached)
    assert not accepted and "catalog_stale_rejected" in reason


def test_anti_replay_accepts_fresh_and_first() -> None:
    cached = {"sequence": 100, "generated_at": "2026-08-21T00:00:00+00:00"}
    fresh = {"sequence": 101, "generated_at": "2026-08-21T01:00:00+00:00"}
    assert ct.check_catalog_stale(fresh, cached)[0] is True
    assert ct.check_catalog_stale(fresh, None)[0] is True  # 首次缓存


def test_cached_catalog_roundtrip(tmp_path: Path) -> None:
    cache = tmp_path / "cache" / "catalog.json"
    catalog = {"sequence": 42, "plugins": []}
    ct.save_cached_catalog(cache, catalog)
    loaded = ct.load_cached_catalog(cache)
    assert loaded == catalog
    # 损坏缓存返回 None（不抛异常）
    cache.write_text("{broken json", encoding="utf-8")
    assert ct.load_cached_catalog(cache) is None
