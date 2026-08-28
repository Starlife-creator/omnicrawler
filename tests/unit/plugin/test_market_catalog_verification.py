from __future__ import annotations

import json
from pathlib import Path

import pytest

from omnicrawler.plugins import market_client, signing

pytestmark = pytest.mark.plugin_contract


def _write_catalog(root: Path, private_key: bytes, *, sequence: int) -> bytes:
    catalog = {
        "schema_version": 1,
        "sequence": sequence,
        "generated_at": f"2026-08-28T00:00:{sequence:02d}+00:00",
        "plugins": [],
        "templates": [],
    }
    raw = (json.dumps(catalog, sort_keys=True) + "\n").encode()
    (root / "catalog.json").write_bytes(raw)
    (root / "catalog.json.sig").write_bytes(signing.sign_bytes(raw, private_key))
    return raw


def test_verified_catalog_authenticates_before_use_and_caches(tmp_path: Path) -> None:
    private_key, public_key = signing.generate_keypair()
    market = tmp_path / "market"
    market.mkdir()
    _write_catalog(market, private_key, sequence=2)
    trust = public_key.decode()
    cache = tmp_path / "cache" / "catalog.json"

    result = market_client.fetch_catalog_verified(str(market), trust, cache_path=cache)

    assert result["sequence"] == 2
    assert json.loads(cache.read_text(encoding="utf-8"))["sequence"] == 2


def test_verified_catalog_rejects_tampering(tmp_path: Path) -> None:
    private_key, public_key = signing.generate_keypair()
    market = tmp_path / "market"
    market.mkdir()
    _write_catalog(market, private_key, sequence=2)
    (market / "catalog.json").write_text('{"plugins": []}', encoding="utf-8")

    with pytest.raises(PermissionError, match="签名校验失败"):
        market_client.fetch_catalog_verified(str(market), public_key.decode())


def test_verified_catalog_rejects_signed_replay(tmp_path: Path) -> None:
    private_key, public_key = signing.generate_keypair()
    market = tmp_path / "market"
    market.mkdir()
    cache = tmp_path / "cache.json"
    _write_catalog(market, private_key, sequence=2)
    market_client.fetch_catalog_verified(str(market), public_key.decode(), cache_path=cache)

    _write_catalog(market, private_key, sequence=1)
    with pytest.raises(PermissionError, match="catalog_stale_rejected"):
        market_client.fetch_catalog_verified(str(market), public_key.decode(), cache_path=cache)

    assert json.loads(cache.read_text(encoding="utf-8"))["sequence"] == 2
