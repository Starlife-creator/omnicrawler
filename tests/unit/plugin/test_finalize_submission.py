from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("cryptography")

from omnicrawler.plugins.identity import IdentityStore
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

    script = Path(__file__).resolve().parents[4] / "OmniCrawler-market" / "tools" / "finalize_submission.py"
    spec = importlib.util.spec_from_file_location("finalize_submission_test", script)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "REGISTRY", market)
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
