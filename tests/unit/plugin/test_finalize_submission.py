from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from .conftest import MARKET_TOOLS

pytest.importorskip("cryptography")
if MARKET_TOOLS is None:
    pytest.skip("OmniCrawler-market 未 clone，跳过跨仓发布验证", allow_module_level=True)
assert MARKET_TOOLS is not None

from omnicrawler.plugins.identity import IdentityStore
from omnicrawler.plugins.market_client import download_and_verify, verify_installed
from omnicrawler.plugins.plugin_packaging import build_plugin_submission
from omnicrawler.plugins.signing import generate_keypair


def test_maintainer_finalize_preserves_creator_package_and_publishes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OMNICRAWL_SECRET_STORE_PATH", str(tmp_path / "secrets.bin"))
    monkeypatch.setenv("OMNICRAWL_KEYRING_DISABLE", "1")
    monkeypatch.setenv("OMNICRAWL_MASTER_PASSWORD", "test-master-key")
    monkeypatch.setattr("omnicrawler.plugins.trust.DEFAULT_TRUST_LIST", tmp_path / "trusted.json")
    IdentityStore().create("alice", "pw")
    package = tmp_path / "author" / "demo"
    package.mkdir(parents=True)
    (package / "plugin.py").write_text(
        "PLUGIN_METADATA={\n"
        "'name':'Demo','version':'1.0.0','description':'safe demo',\n"
        "'plugin_types':['source'],'permissions':[],'license':'MIT',\n"
        "'execution_mode':'subprocess','dependencies':[]}\n"
        "def handle(operation, payload): return {}\n",
        encoding="utf-8",
    )
    payload = build_plugin_submission(
        package, username="alice", password="pw", listing="# Demo\n"
    )
    market = tmp_path / "market"
    for rel, content in payload.items():
        target = market / Path(*rel.split("/"))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    (market / "plugins").mkdir()
    (market / "templates").mkdir()
    (market / "authors").mkdir()
    (market / "keys").mkdir()
    private, public = generate_keypair()
    (market / "keys" / "plugin_trust.pub.pem").write_bytes(public)
    private_path = tmp_path / "maintainer-private.pem"
    private_path.write_bytes(private)
    submission_dir = next((market / "submissions").rglob("submission.json")).parent
    manifest_before = (submission_dir / "package.manifest.json").read_bytes()

    script = MARKET_TOOLS / "finalize_submission.py"
    spec = importlib.util.spec_from_file_location("finalize_submission_test", script)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "REGISTRY", market)

    # A wrong maintainer key must fail before creating or changing any market file.
    wrong_private, _ = generate_keypair()
    wrong_private_path = tmp_path / "wrong-maintainer-private.pem"
    wrong_private_path.write_bytes(wrong_private)
    before_wrong_key = {
        path.relative_to(market).as_posix(): path.read_bytes()
        for path in market.rglob("*")
        if path.is_file()
    }
    with pytest.raises(ValueError, match="维护者私钥与市场信任根不匹配"):
        module.finalize(
            SimpleNamespace(
                submission_dir=str(submission_dir),
                reviewed_manifest_sha256=__import__("hashlib")
                .sha256(manifest_before)
                .hexdigest(),
                maintainer_key=str(wrong_private_path),
                market_id=None,
            )
        )
    after_wrong_key = {
        path.relative_to(market).as_posix(): path.read_bytes()
        for path in market.rglob("*")
        if path.is_file()
    }
    assert after_wrong_key == before_wrong_key

    result = module.finalize(
        SimpleNamespace(
            submission_dir=str(submission_dir),
            reviewed_manifest_sha256=__import__("hashlib").sha256(manifest_before).hexdigest(),
            maintainer_key=str(private_path),
            market_id=None,
        )
    )
    assert result == 0
    published = market / "plugins" / "demo"
    assert (published / "package.manifest.json").read_bytes() == manifest_before
    assert (published / "package.manifest.maintainer.sig").is_file()
    assert (market / "authors" / "alice.yaml").is_file()
    assert (market / "catalog.json.sig").is_file()

    # A later release is stored beside, not over, the original signed bytes.
    update_package = tmp_path / "author-update" / "demo"
    update_package.mkdir(parents=True)
    (update_package / "plugin.py").write_text(
        "PLUGIN_METADATA={\n"
        "'name':'Demo','version':'1.1.0','description':'safe update',\n"
        "'plugin_types':['source'],'permissions':[],'license':'MIT',\n"
        "'execution_mode':'subprocess','dependencies':[]}\n"
        "def handle(operation, payload): return {'updated': True}\n",
        encoding="utf-8",
    )
    update_payload = build_plugin_submission(
        update_package,
        username="alice",
        password="pw",
        listing="# Demo 1.1\n",
    )
    for rel, content in update_payload.items():
        target = market / Path(*rel.split("/"))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    update_submission_key = next(key for key in update_payload if key.endswith("submission.json"))
    update_submission = market / Path(*update_submission_key.split("/")).parent
    update_manifest = (update_submission / "package.manifest.json").read_bytes()

    assert module.finalize(
        SimpleNamespace(
            submission_dir=str(update_submission),
            reviewed_manifest_sha256=__import__("hashlib").sha256(update_manifest).hexdigest(),
            maintainer_key=str(private_path),
            market_id=None,
        )
    ) == 0
    assert (published / "package.manifest.json").read_bytes() == manifest_before
    versioned = published / "versions" / "1.1.0"
    assert (versioned / "package.manifest.json").read_bytes() == update_manifest
    catalog = json.loads((market / "catalog.json").read_text(encoding="utf-8"))
    assert catalog["plugins"][0]["version"] == "1.1.0"
    assert catalog["plugins"][0]["plugin_file"].endswith("versions/1.1.0/plugin.py")

    installed_root = tmp_path / "installed"
    installed = download_and_verify(
        "demo", str(market), installed_root, public.decode("utf-8")
    )
    assert "updated" in installed.read_text(encoding="utf-8")
    assert verify_installed(installed_root, "demo", public.decode("utf-8")) == (
        True,
        "verified-package",
    )
    with pytest.raises(ValueError, match="must increase monotonically"):
        module.finalize(
            SimpleNamespace(
                submission_dir=str(update_submission),
                reviewed_manifest_sha256=__import__("hashlib").sha256(update_manifest).hexdigest(),
                maintainer_key=str(private_path),
                market_id=None,
            )
        )
