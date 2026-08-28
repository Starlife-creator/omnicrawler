from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("cryptography")

from omnicrawler.plugins.identity import IdentityStore
from omnicrawler.plugins.plugin_packaging import build_plugin_submission


def _payload(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, bytes]:
    monkeypatch.setattr("omnicrawler.plugins.trust.DEFAULT_TRUST_LIST", tmp_path / "trusted.json")
    monkeypatch.setenv("OMNICRAWL_SECRET_STORE_PATH", str(tmp_path / "secrets.bin"))
    monkeypatch.setenv("OMNICRAWL_KEYRING_DISABLE", "1")
    monkeypatch.setenv("OMNICRAWL_MASTER_PASSWORD", "test-master-key")
    identity = IdentityStore().create("alice", "pw")
    assert identity.key_fingerprint
    package = tmp_path / "demo"
    package.mkdir()
    (package / "plugin.py").write_text(
        "PLUGIN_METADATA={'name':'demo','version':'1.0.0','description':'d','license':'MIT'}\n"
        "def handle(operation, payload): return {}\n",
        encoding="utf-8",
    )
    return build_plugin_submission(
        package, username="alice", password="pw", listing="# Demo\n"
    )


def _write(registry: Path, files: dict[str, bytes]) -> None:
    for rel, content in files.items():
        target = registry / Path(*rel.split("/"))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)


def test_market_submission_validator_accepts_gui_payload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    registry = tmp_path / "market"
    _write(registry, _payload(tmp_path, monkeypatch))
    script = Path(__file__).resolve().parents[4] / "OmniCrawler-market" / "tools" / "validate_submission.py"
    result = subprocess.run(
        [sys.executable, str(script), "--registry", str(registry)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_market_submission_validator_rejects_tampering(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    registry = tmp_path / "market"
    files = _payload(tmp_path, monkeypatch)
    plugin_key = next(key for key in files if key.endswith("/plugin.py"))
    files[plugin_key] += b"\n# changed"
    _write(registry, files)
    script = Path(__file__).resolve().parents[4] / "OmniCrawler-market" / "tools" / "validate_submission.py"
    result = subprocess.run(
        [sys.executable, str(script), "--registry", str(registry)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
    )
    assert result.returncode == 1
    assert "哈希不一致" in result.stdout
