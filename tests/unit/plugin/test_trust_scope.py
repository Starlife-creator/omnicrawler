from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("cryptography")

from omnicrawler.plugins.identity import CreatorIdentity
from omnicrawler.plugins.signing import generate_keypair
from omnicrawler.plugins.trust import TrustedUserList


def _creator() -> CreatorIdentity:
    from cryptography.hazmat.primitives import serialization

    _, public_pem = generate_keypair()
    public = serialization.load_pem_public_key(public_pem).public_bytes_raw()
    return CreatorIdentity(username="alice", public_key=public)


def test_plugin_scoped_trust_does_not_trust_all_author_packages(tmp_path: Path) -> None:
    creator = _creator()
    trusted = TrustedUserList(tmp_path / "trusted.json")
    assert trusted.add(creator, source="p2p", scope="plugin", plugin_id="weather")
    assert trusted.contains_key(creator.public_key, plugin_id="weather")
    assert not trusted.contains_key(creator.public_key, plugin_id="finance")
    assert not trusted.contains_key(creator.public_key)


def test_author_scope_upgrades_existing_plugin_scope(tmp_path: Path) -> None:
    creator = _creator()
    path = tmp_path / "trusted.json"
    trusted = TrustedUserList(path)
    trusted.add(creator, source="p2p", scope="plugin", plugin_id="weather")
    assert trusted.add(creator, source="manual", scope="author")
    restored = TrustedUserList(path)
    assert restored.contains_key(creator.public_key, plugin_id="anything")
