"""Tests for the signature policy gate (strict/developer) and trust prompting.

Covers:
- strict: unsigned plugins rejected on every path;
- strict: market directory (plugins_installed/) only accepts maintainer-sign;
- strict: local creator-signed plugins require trust-list membership or a
  trust prompt (TRUST_AND_LOAD / LOAD_ONCE / REJECT / no prompter);
- creator signature verifies even without a trust root configured.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("cryptography")

from omnicrawler.plugins import signing
from omnicrawler.plugins.identity import IdentityStore, UserIdentity
from omnicrawler.plugins.plugins import (
    SIGNATURE_POLICY_DEVELOPER,
    SIGNATURE_POLICY_STRICT,
    Registry,
    TrustPromptResult,
    load_local_plugins,
)
from omnicrawler.plugins.trust import TrustedUserList


@pytest.fixture()
def identity(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> UserIdentity:
    monkeypatch.setenv("OMNICRAWL_IDENTITY_PASSWORD", "pw")
    monkeypatch.setenv("OMNICRAWL_SECRET_STORE_PATH", str(tmp_path / "secrets.bin"))
    monkeypatch.setenv("OMNICRAWL_KEYRING_DISABLE", "1")
    monkeypatch.setenv("OMNICRAWL_MASTER_PASSWORD", "test-master-key")
    store = IdentityStore()
    store.create("alice", "pw")
    return store.load("alice", "pw")


def _write_plugin(plugin_dir: Path, *, name: str = "aliceplug") -> Path:
    plugin_dir.mkdir(parents=True, exist_ok=True)
    plugin = plugin_dir / "plugin.py"
    plugin.write_text(
        f"PLUGIN_METADATA = {{'name': '{name}', 'version': '1.0.0'}}\n"
        "def register(registry):\n"
        "    registry.register_source('alice_src', lambda *a, **k: None)\n",
        encoding="utf-8",
    )
    return plugin


def _creator_sign(plugin_dir: Path, user: UserIdentity) -> None:
    plugin = plugin_dir / "plugin.py"
    creator = user.export_identity()
    (plugin_dir / "creator.identity").write_text(
        json.dumps(creator.to_dict(), ensure_ascii=False) + "\n", encoding="utf-8"
    )
    signature = user.sign_bytes(plugin.read_bytes())
    (plugin_dir / "creator.sig").write_bytes(signature)


def _trust_root_sign(plugin_dir: Path, private_pem: bytes) -> None:
    plugin = plugin_dir / "plugin.py"
    signature = signing.sign_bytes(plugin.read_bytes(), private_pem)
    (plugin_dir / "plugin.py.sig").write_bytes(signature)


def test_strict_rejects_unsigned_local_plugin(tmp_path: Path) -> None:
    plugin = _write_plugin(tmp_path / "plug")
    with pytest.raises(signing.PluginSignatureError):
        load_local_plugins(Registry(), [str(plugin)], tmp_path, signature_policy=SIGNATURE_POLICY_STRICT)


def test_developer_warns_and_loads_unsigned_plugin(tmp_path: Path) -> None:
    plugin = _write_plugin(tmp_path / "plug")
    registry = Registry()
    load_local_plugins(registry, [str(plugin)], tmp_path, signature_policy=SIGNATURE_POLICY_DEVELOPER)
    assert registry.plugins[0].name == "aliceplug"


def test_strict_market_dir_rejects_unsigned(tmp_path: Path) -> None:
    plugin = _write_plugin(tmp_path / "plugins_installed" / "demo")
    with pytest.raises(signing.PluginSignatureError, match="市场插件"):
        load_local_plugins(Registry(), [str(plugin)], tmp_path, signature_policy=SIGNATURE_POLICY_STRICT)


def test_strict_market_dir_rejects_creator_signed(tmp_path: Path, identity: UserIdentity) -> None:
    plugin_dir = tmp_path / "plugins_installed" / "demo"
    plugin = _write_plugin(plugin_dir)
    _creator_sign(plugin_dir, identity)
    with pytest.raises(signing.PluginSignatureError, match="市场插件"):
        load_local_plugins(Registry(), [str(plugin)], tmp_path, signature_policy=SIGNATURE_POLICY_STRICT)


def test_strict_market_dir_accepts_maintainer_signed(tmp_path: Path) -> None:
    from omnicrawler.core.config import AppConfig

    private_pem, public_pem = signing.generate_keypair()
    trust_path = tmp_path / "trust.pub.pem"
    trust_path.write_bytes(public_pem)
    plugin_dir = tmp_path / "plugins_installed" / "demo"
    plugin = _write_plugin(plugin_dir)
    _trust_root_sign(plugin_dir, private_pem)
    config = AppConfig(
        Path("<memory>"),
        tmp_path,
        {"plugins": {"trust_public_key": str(trust_path)}},
        tmp_path,
    )
    registry = Registry()
    load_local_plugins(
        registry,
        [str(plugin)],
        tmp_path,
        signature_policy=SIGNATURE_POLICY_STRICT,
        config=config,
    )
    assert registry.plugins[0].name == "aliceplug"


def test_creator_signed_without_trust_root_verifies_but_requires_prompt(
    tmp_path: Path, identity: UserIdentity
) -> None:
    """无信任根也能完成创作者层验证；未信任 → 无询问器时拒载并给出信任命令。"""
    plugin_dir = tmp_path / "plug"
    plugin = _write_plugin(plugin_dir)
    _creator_sign(plugin_dir, identity)
    with pytest.raises(signing.PluginSignatureError, match="信任列表"):
        load_local_plugins(Registry(), [str(plugin)], tmp_path, signature_policy=SIGNATURE_POLICY_STRICT)


def test_prompt_reject_blocks_loading(tmp_path: Path, identity: UserIdentity) -> None:
    plugin_dir = tmp_path / "plug"
    plugin = _write_plugin(plugin_dir)
    _creator_sign(plugin_dir, identity)
    with pytest.raises(signing.PluginSignatureError):
        load_local_plugins(
            Registry(),
            [str(plugin)],
            tmp_path,
            signature_policy=SIGNATURE_POLICY_STRICT,
            trust_prompter=lambda *args: TrustPromptResult.REJECT,
        )


def test_prompt_load_once_loads_without_trusting(tmp_path: Path, identity: UserIdentity) -> None:
    plugin_dir = tmp_path / "plug"
    plugin = _write_plugin(plugin_dir)
    _creator_sign(plugin_dir, identity)
    seen: list[tuple[str, str, str]] = []
    registry = Registry()

    def prompter(plugin_id: str, username: str, fingerprint: str) -> TrustPromptResult:
        seen.append((plugin_id, username, fingerprint))
        return TrustPromptResult.LOAD_ONCE

    load_local_plugins(
        registry,
        [str(plugin)],
        tmp_path,
        signature_policy=SIGNATURE_POLICY_STRICT,
        trust_prompter=prompter,
    )
    assert registry.plugins[0].name == "aliceplug"
    assert seen and seen[0][1] == "alice"
    assert seen[0][2] == identity.key_fingerprint
    assert not TrustedUserList().contains(identity.key_fingerprint)


def test_prompt_trust_and_load_adds_to_trust_list(
    tmp_path: Path, identity: UserIdentity, monkeypatch: pytest.MonkeyPatch
) -> None:
    plugin_dir = tmp_path / "plug"
    plugin = _write_plugin(plugin_dir)
    _creator_sign(plugin_dir, identity)
    trust_list_path = tmp_path / "trusted.json"
    monkeypatch.setattr("omnicrawler.plugins.trust.DEFAULT_TRUST_LIST", trust_list_path)
    registry = Registry()
    load_local_plugins(
        registry,
        [str(plugin)],
        tmp_path,
        signature_policy=SIGNATURE_POLICY_STRICT,
        trust_prompter=lambda *args: TrustPromptResult.TRUST_AND_LOAD,
    )
    assert registry.plugins[0].name == "aliceplug"
    assert trust_list_path.is_file()
    assert TrustedUserList().contains(identity.key_fingerprint)


def test_already_trusted_creator_loads_without_prompt(
    tmp_path: Path, identity: UserIdentity, monkeypatch: pytest.MonkeyPatch
) -> None:
    plugin_dir = tmp_path / "plug"
    plugin = _write_plugin(plugin_dir)
    _creator_sign(plugin_dir, identity)
    trust_list_path = tmp_path / "trusted.json"
    monkeypatch.setattr("omnicrawler.plugins.trust.DEFAULT_TRUST_LIST", trust_list_path)
    TrustedUserList().add(identity.export_identity(), source="manual")
    registry = Registry()
    load_local_plugins(registry, [str(plugin)], tmp_path, signature_policy=SIGNATURE_POLICY_STRICT)
    assert registry.plugins[0].name == "aliceplug"


# ── P9-B3（B01-006）：is_market 祖先判定 ─────────────────────────


def test_strict_nested_market_subdir_still_market(tmp_path: Path) -> None:
    """嵌套路径 <root>/plugins_installed/<id>/sub/plugin.py 仍判为市场插件。"""
    plugin = _write_plugin(tmp_path / "plugins_installed" / "demo" / "nested")
    with pytest.raises(signing.PluginSignatureError, match="市场插件"):
        load_local_plugins(Registry(), [str(plugin)], tmp_path, signature_policy=SIGNATURE_POLICY_STRICT)


def test_strict_spoofed_market_segment_not_market(tmp_path: Path) -> None:
    """伪造路径 <root>/vendor/plugins_installed/... 中的 plugins_installed 段不算市场。"""
    plugin = _write_plugin(tmp_path / "vendor" / "plugins_installed" / "evil")
    with pytest.raises(signing.PluginSignatureError) as exc_info:
        load_local_plugins(Registry(), [str(plugin)], tmp_path, signature_policy=SIGNATURE_POLICY_STRICT)
    assert "市场插件" not in str(exc_info.value)  # 按本地插件路径处理，而非市场目录
