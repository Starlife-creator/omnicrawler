"""Tests for the local identity system (creator signing, OS keyring storage)."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

pytest.importorskip("cryptography")

from omnicrawl.core.secrets_store import SecretsStore
from omnicrawl.plugins.identity import (
    CreatorIdentity,
    IdentityError,
    IdentityStore,
)


class _FakeKeyring:
    def __init__(self) -> None:
        self.data: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, username: str) -> str | None:
        return self.data.get((service, username))

    def set_password(self, service: str, username: str, password: str) -> None:
        self.data[(service, username)] = password


def _make_store(tmp_path: Path) -> IdentityStore:
    store = SecretsStore(tmp_path / "secrets.bin", keyring_api=_FakeKeyring())
    return IdentityStore(store=store)


def test_create_and_load_roundtrip(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    identity = store.create("alice", "correct horse")
    assert identity.key_fingerprint
    assert len(identity.key_fingerprint) == 32
    assert all(char in "0123456789abcdef" for char in identity.key_fingerprint)

    loaded = store.load("alice", "correct horse")
    assert loaded.username == "alice"
    assert loaded.key_fingerprint == identity.key_fingerprint
    assert store.list_usernames() == ["alice"]


def test_duplicate_username_rejected(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    store.create("alice", "password-a")
    with pytest.raises(IdentityError, match="已存在"):
        store.create("alice", "password-b")


def test_wrong_password_rejected(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    store.create("alice", "correct")
    with pytest.raises(IdentityError, match="密码错误"):
        store.load("alice", "wrong")
    with pytest.raises(IdentityError, match="不存在"):
        store.load("nobody", "x")


def test_sign_and_export_identity_excludes_private_key(tmp_path: Path) -> None:
    import json

    store = _make_store(tmp_path)
    identity = store.create("alice", "pw")
    data = b"def register(registry): pass\n"
    signature = identity.sign_bytes(data)
    assert len(signature) == 64  # ed25519 signature size

    creator = identity.export_identity()
    assert isinstance(creator, CreatorIdentity)
    assert creator.username == "alice"
    assert creator.key_fingerprint == identity.key_fingerprint

    payload = json.dumps(creator.to_dict()).lower()
    assert set(json.loads(payload)) == {
        "username",
        "public_key",
        "key_fingerprint",
        "fingerprint_algorithm",
    }
    assert "private" not in payload


def test_creator_identity_roundtrip_dict(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    identity = store.create("bob", "pw")
    creator = identity.export_identity()
    restored = CreatorIdentity.from_dict(creator.to_dict())
    assert restored == creator


def test_public_key_matches_fingerprint(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    identity = store.create("carol", "pw")
    public_bytes = identity.export_identity().public_key
    expected = hashlib.sha256(public_bytes).hexdigest()[:32]
    assert identity.key_fingerprint == expected
    assert len(public_bytes) == 32


def test_delete_requires_password(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    store.create("dave", "pw")
    with pytest.raises(IdentityError):
        store.delete("dave", "wrong")
    assert store.exists("dave")
    assert store.delete("dave", "pw") is True
    assert not store.exists("dave")


def test_signature_verifiable_with_public_key(tmp_path: Path) -> None:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    store = _make_store(tmp_path)
    identity = store.create("erin", "pw")
    data = b"plugin payload"
    signature = identity.sign_bytes(data)
    public = Ed25519PublicKey.from_public_bytes(identity.export_identity().public_key)
    public.verify(signature, data)  # 不抛异常即有效


def test_empty_password_rejected(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    with pytest.raises(IdentityError, match="密码"):
        store.create("alice", "")
