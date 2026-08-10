"""Tests for tools/sign_plugin.py — offline signing with pre-publish scan
and transparency log."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("cryptography")

from omnicrawl.plugins import signing  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[3]
SIGN_TOOL = REPO_ROOT / "tools" / "sign_plugin.py"

_UTF8_ENV = {**os.environ, "PYTHONIOENCODING": "utf-8"}


def _run(*args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SIGN_TOOL), *args],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env or _UTF8_ENV,
    )


def _make_keypair(tmp_path: Path) -> tuple[Path, Path]:
    """进程内生成密钥对（子进程生成密钥在 Windows 上偶发熵源失败）。"""
    private_pem, public_pem = signing.generate_keypair()
    keys = tmp_path / "keys"
    keys.mkdir()
    private = keys / "private.pem"
    public = keys / "public.pem"
    private.write_bytes(private_pem)
    public.write_bytes(public_pem)
    return private, public


def _make_clean_plugin(tmp_path: Path) -> Path:
    plugin_dir = tmp_path / "demo_plug"
    plugin_dir.mkdir()
    (plugin_dir / "plugin.py").write_text(
        "def register(registry):\n    registry.register_source('demo', object)\n",
        encoding="utf-8",
    )
    return plugin_dir / "plugin.py"


def test_sign_writes_sig_and_transparency_log(tmp_path: Path) -> None:
    private, public = _make_keypair(tmp_path)

    plugin = _make_clean_plugin(tmp_path)
    log = tmp_path / "signing_transparency.jsonl"
    signed = _run("sign", str(plugin), "--private-key", str(private), "--log", str(log))
    assert signed.returncode == 0, signed.stdout + signed.stderr
    assert plugin.with_suffix(".py.sig").is_file()

    entries = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    assert len(entries) == 1
    entry = entries[0]
    assert entry["operation"] == "sign"
    assert entry["plugin_sha256"]
    assert entry["operator"]
    assert entry["timestamp"]

    verified = _run("verify", str(plugin), "--trust", str(public))
    assert verified.returncode == 0, verified.stdout


def test_sign_blocks_on_scan_failure(tmp_path: Path) -> None:
    private, _ = _make_keypair(tmp_path)

    dirty = tmp_path / "dirty"
    dirty.mkdir()
    plugin = dirty / "plugin.py"
    plugin.write_text("def register(registry): pass\n", encoding="utf-8")
    (dirty / ".env").write_text("AWS_SECRET_KEY=AKIAIOSFODNN7EXAMPLE\n", encoding="utf-8")

    signed = _run("sign", str(plugin), "--private-key", str(private), "--log", str(tmp_path / "log.jsonl"))
    assert signed.returncode != 0
    assert not plugin.with_suffix(".py.sig").is_file()
    assert "发现问题" in signed.stdout + signed.stderr


def test_scan_subcommand_reports_issues(tmp_path: Path) -> None:
    dirty = tmp_path / "dirty"
    dirty.mkdir()
    (dirty / "plugin.py").write_text("def register(registry): pass\n", encoding="utf-8")
    (dirty / "credentials.json").write_text("{}", encoding="utf-8")
    result = _run("scan", str(dirty))
    assert result.returncode == 1
    assert "credentials.json" in result.stdout


def _identity_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, username: str, password: str
) -> dict[str, str]:
    """临时身份环境：隔离 secrets 文件 + 禁用真实 keyring（走密码派生主密钥）。"""
    from omnicrawl.plugins.identity import IdentityStore

    secrets_path = tmp_path / "secrets.bin"
    env = {
        "OMNICRAWL_IDENTITY_PASSWORD": password,
        "OMNICRAWL_SECRET_STORE_PATH": str(secrets_path),
        "OMNICRAWL_KEYRING_DISABLE": "1",
        "OMNICRAWL_MASTER_PASSWORD": "test-master-key",
    }
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    IdentityStore().create(username, password)
    return {**_UTF8_ENV, **env}


def test_creator_sign_end_to_end(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """创建即签名：creator-sign → 三层信任评估 → 信任 → 加载。"""
    import json

    from omnicrawl.plugins import trust as trust_model
    from omnicrawl.plugins.identity import CreatorIdentity

    env = _identity_env(tmp_path, monkeypatch, "alice", "pw")
    plugin_dir = tmp_path / "plug"
    plugin_dir.mkdir()
    plugin = plugin_dir / "plugin.py"
    plugin.write_text("def register(registry): pass\n", encoding="utf-8")

    signed = _run("creator-sign", str(plugin_dir), "--username", "alice", "--skip-scan", env=env)
    assert signed.returncode == 0, signed.stdout + signed.stderr
    assert (plugin_dir / "creator.sig").is_file()
    assert (plugin_dir / "creator.identity").is_file()
    creator = CreatorIdentity.from_dict(
        json.loads((plugin_dir / "creator.identity").read_text(encoding="utf-8"))
    )
    assert creator.username == "alice"
    assert len(creator.key_fingerprint) == 32

    _, trust_pem = signing.generate_keypair()
    trust_path = tmp_path / "trust.pub.pem"
    trust_path.write_bytes(trust_pem)

    decision = trust_model.verify_plugin_trust(
        plugin_dir, str(trust_path), trust_model.TrustedUserList(tmp_path / "trusted.json")
    )
    assert decision.level == trust_model.TrustLevel.CreatorUntrusted

    trusted = trust_model.TrustedUserList(tmp_path / "trusted.json")
    trusted.add(creator)
    decision = trust_model.verify_plugin_trust(plugin_dir, str(trust_path), trusted)
    assert decision.level == trust_model.TrustLevel.CreatorTrusted


def test_maintainer_sign_generates_maintainer_sig(tmp_path: Path) -> None:
    from omnicrawl.plugins import trust as trust_model

    private, public = _make_keypair(tmp_path)
    plugin_dir = tmp_path / "plug"
    plugin_dir.mkdir()
    plugin = plugin_dir / "plugin.py"
    plugin.write_text("def register(registry): pass\n", encoding="utf-8")

    log = tmp_path / "signing_transparency.jsonl"
    signed = _run("maintainer-sign", str(plugin_dir), "--private-key", str(private), "--log", str(log))
    assert signed.returncode == 0, signed.stdout + signed.stderr
    assert (plugin_dir / "maintainer.sig").is_file()
    assert log.is_file()

    decision = trust_model.verify_plugin_trust(
        plugin_dir, str(public), trust_model.TrustedUserList(tmp_path / "trusted.json")
    )
    assert decision.level == trust_model.TrustLevel.MaintainerSigned


def test_creator_sign_targets_template_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """创建即签名支持 --file template.yaml（模板入市的三件套之二）。"""
    import json

    from omnicrawl.plugins.identity import CreatorIdentity

    env = _identity_env(tmp_path, monkeypatch, "tina", "pw")
    template_dir = tmp_path / "tpl"
    template_dir.mkdir()
    template = template_dir / "template.yaml"
    template.write_text(
        "template_version: 1\n"
        "template:\n"
        "  id: demo/template\n"
        "  name: Demo\n"
        "  category: generic\n"
        "  version: 1.0.0\n"
        "  publisher: tina\n"
        "project: {name: demo, workspace: work/demo}\n"
        "source: {kind: static_html, seeds: ['https://example.com']}\n",
        encoding="utf-8",
    )
    signed = _run(
        "creator-sign",
        str(template_dir),
        "--file",
        "template.yaml",
        "--username",
        "tina",
        "--skip-scan",
        env=env,
    )
    assert signed.returncode == 0, signed.stdout + signed.stderr
    assert (template_dir / "creator.sig").is_file()
    assert (template_dir / "creator.identity").is_file()
    creator = CreatorIdentity.from_dict(
        json.loads((template_dir / "creator.identity").read_text(encoding="utf-8"))
    )
    assert creator.username == "tina"

    # 签名覆盖 template.yaml 内容（验签通过）
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    public = Ed25519PublicKey.from_public_bytes(creator.public_key)
    public.verify((template_dir / "creator.sig").read_bytes(), template.read_bytes())


def test_loader_rejects_untrusted_creator_plugin(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """三层信任接入加载链：创作者签名未信任 → 拒绝加载。"""
    from omnicrawl.core.config import AppConfig
    from omnicrawl.plugins.plugins import Registry, load_local_plugins
    from omnicrawl.plugins.signing import PluginSignatureError

    _identity_env(tmp_path, monkeypatch, "carol", "pw")
    env = {
        **_UTF8_ENV,
        "OMNICRAWL_IDENTITY_PASSWORD": "pw",
        "OMNICRAWL_SECRET_STORE_PATH": str(tmp_path / "secrets.bin"),
        "OMNICRAWL_KEYRING_DISABLE": "1",
        "OMNICRAWL_MASTER_PASSWORD": "test-master-key",
    }
    trust_pem = signing.generate_keypair()[1]
    trust_path = tmp_path / "trust.pub.pem"
    trust_path.write_bytes(trust_pem)

    plugin_dir = tmp_path / "plug"
    plugin_dir.mkdir()
    plugin = plugin_dir / "plugin.py"
    plugin.write_text(
        "PLUGIN_METADATA = {'name': 'carolplug', 'version': '1.0.0'}\n"
        "def register(registry):\n"
        "    registry.register_source('carol_src', lambda *a, **k: None)\n",
        encoding="utf-8",
    )
    assert (
        _run("creator-sign", str(plugin_dir), "--username", "carol", "--skip-scan", env=env).returncode == 0
    )

    config = AppConfig(
        Path("<memory>"),
        tmp_path,
        {"plugins": {"trust_public_key": str(trust_path)}},
        tmp_path,
    )
    with pytest.raises(PluginSignatureError, match="信任列表"):
        load_local_plugins(
            Registry(), [str(plugin)], tmp_path, config=config,
            signature_policy="strict",
        )
