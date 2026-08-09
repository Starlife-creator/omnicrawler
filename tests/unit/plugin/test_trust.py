"""Tests for the three-tier trust model and local trust list."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("cryptography")

from omnicrawl.plugins import signing
from omnicrawl.plugins.identity import CreatorIdentity
from omnicrawl.plugins.trust import (
    TrustedUser,
    TrustedUserList,
    TrustLevel,
    load_decision,
    verify_plugin_trust,
)


def _make_plugin(plugin_dir: Path) -> Path:
    plugin_dir.mkdir(parents=True, exist_ok=True)
    path = plugin_dir / "plugin.py"
    path.write_text("def register(registry): pass\n", encoding="utf-8")
    return path


def _make_keypair(tmp_path: Path) -> tuple[bytes, Path]:
    private_pem, public_pem = signing.generate_keypair()
    trust = tmp_path / "trust.pub.pem"
    trust.write_bytes(public_pem)
    return private_pem, trust


def _make_creator(tmp_path: Path) -> tuple[CreatorIdentity, bytes]:
    """生成创作者身份（公开身份 + 测试用私钥）。"""
    from cryptography.hazmat.primitives.serialization import load_pem_private_key

    from omnicrawl.plugins.identity import _fingerprint

    private_pem, _ = signing.generate_keypair()
    key = load_pem_private_key(private_pem, password=None)
    public_bytes = key.public_key().public_bytes_raw()
    identity = CreatorIdentity(
        username="alice",
        public_key=public_bytes,
        key_fingerprint=_fingerprint(public_bytes),
    )
    return identity, private_pem


def _sign_with_private(private_pem: bytes, data: bytes) -> bytes:
    from cryptography.hazmat.primitives.serialization import load_pem_private_key

    key = load_pem_private_key(private_pem, password=None)
    return key.sign(data)


def test_maintainer_signed_auto_trust(tmp_path: Path) -> None:
    plugin_dir = tmp_path / "plug"
    plugin = _make_plugin(plugin_dir)
    private_pem, trust = _make_keypair(tmp_path)
    plugin_dir.joinpath("maintainer.sig").write_bytes(_sign_with_private(private_pem, plugin.read_bytes()))

    decision = verify_plugin_trust(plugin_dir, str(trust), TrustedUserList(tmp_path / "trusted.json"))
    assert decision.level == TrustLevel.MaintainerSigned
    ok, _ = load_decision(decision)
    assert ok is True


def test_legacy_plugin_sig_auto_trust(tmp_path: Path) -> None:
    plugin_dir = tmp_path / "plug"
    plugin = _make_plugin(plugin_dir)
    private_pem, trust = _make_keypair(tmp_path)
    plugin_dir.joinpath("plugin.py.sig").write_bytes(_sign_with_private(private_pem, plugin.read_bytes()))

    decision = verify_plugin_trust(plugin_dir, str(trust), TrustedUserList(tmp_path / "trusted.json"))
    assert decision.level == TrustLevel.MaintainerSigned


def test_creator_signed_untrusted_prompts(tmp_path: Path) -> None:
    plugin_dir = tmp_path / "plug"
    plugin = _make_plugin(plugin_dir)
    creator, private_pem = _make_creator(tmp_path)
    plugin_dir.joinpath("creator.identity").write_text(json.dumps(creator.to_dict()), encoding="utf-8")
    plugin_dir.joinpath("creator.sig").write_bytes(_sign_with_private(private_pem, plugin.read_bytes()))
    _, trust = _make_keypair(tmp_path)

    decision = verify_plugin_trust(plugin_dir, str(trust), TrustedUserList(tmp_path / "trusted.json"))
    assert decision.level == TrustLevel.CreatorUntrusted
    assert decision.creator is not None
    assert decision.creator.username == "alice"
    ok, reason = load_decision(decision)
    assert ok is False
    assert "alice" in reason


def test_creator_signed_trusted_loads(tmp_path: Path) -> None:
    plugin_dir = tmp_path / "plug"
    plugin = _make_plugin(plugin_dir)
    creator, private_pem = _make_creator(tmp_path)
    plugin_dir.joinpath("creator.identity").write_text(json.dumps(creator.to_dict()), encoding="utf-8")
    plugin_dir.joinpath("creator.sig").write_bytes(_sign_with_private(private_pem, plugin.read_bytes()))
    _, trust = _make_keypair(tmp_path)

    trusted = TrustedUserList(tmp_path / "trusted.json")
    trusted.add(creator, source="p2p")
    decision = verify_plugin_trust(plugin_dir, str(trust), trusted)
    assert decision.level == TrustLevel.CreatorTrusted
    ok, _ = load_decision(decision)
    assert ok is True


def test_unsigned_rejected(tmp_path: Path) -> None:
    plugin_dir = tmp_path / "plug"
    _make_plugin(plugin_dir)
    _, trust = _make_keypair(tmp_path)
    decision = verify_plugin_trust(plugin_dir, str(trust), TrustedUserList(tmp_path / "trusted.json"))
    assert decision.level == TrustLevel.Unsigned
    ok, _ = load_decision(decision)
    assert ok is False


def test_tampered_creator_signature_rejected(tmp_path: Path) -> None:
    plugin_dir = tmp_path / "plug"
    plugin = _make_plugin(plugin_dir)
    creator, private_pem = _make_creator(tmp_path)
    plugin_dir.joinpath("creator.identity").write_text(json.dumps(creator.to_dict()), encoding="utf-8")
    plugin_dir.joinpath("creator.sig").write_bytes(_sign_with_private(private_pem, plugin.read_bytes()))
    plugin.write_bytes(plugin.read_bytes() + b"\n# tampered\n")
    _, trust = _make_keypair(tmp_path)

    trusted = TrustedUserList(tmp_path / "trusted.json")
    trusted.add(creator)
    decision = verify_plugin_trust(plugin_dir, str(trust), trusted)
    assert decision.level == TrustLevel.Unsigned


def test_trust_list_persists_and_revokes(tmp_path: Path) -> None:
    path = tmp_path / "trusted.json"
    creator = CreatorIdentity(username="bob", public_key=b"\x00" * 32, key_fingerprint="f" * 32)

    trusted = TrustedUserList(path)
    assert trusted.add(creator) is True
    assert trusted.add(creator) is False  # 幂等

    reloaded = TrustedUserList(path)
    assert reloaded.contains(creator.key_fingerprint)
    users = reloaded.list_users()
    assert len(users) == 1
    assert isinstance(users[0], TrustedUser)
    assert users[0].username == "bob"
    assert users[0].source == "manual"

    assert reloaded.revoke(creator.key_fingerprint) is True
    assert reloaded.revoke(creator.key_fingerprint) is False
    assert not TrustedUserList(path).contains(creator.key_fingerprint)


def test_trusted_without_signature_never_loads(tmp_path: Path) -> None:
    plugin_dir = tmp_path / "plug"
    _make_plugin(plugin_dir)
    creator = CreatorIdentity(username="carol", public_key=b"\x00" * 32, key_fingerprint="a" * 32)
    _, trust = _make_keypair(tmp_path)
    trusted = TrustedUserList(tmp_path / "trusted.json")
    trusted.add(creator)
    decision = verify_plugin_trust(plugin_dir, str(trust), trusted)
    assert decision.level == TrustLevel.Unsigned  # 信任列表不能替代签名本身
