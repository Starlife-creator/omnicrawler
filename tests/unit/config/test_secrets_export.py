"""S2.2.2：GUI api_key 出口加密 + get_secret secrets_store 兜底。

验收：to_yaml/autosave 落盘无明文凭据（写 secret:// 引用，值加密进 secrets_store）；
load_config 经 get_secret 兜底可解密回真实值。
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from omnicrawl.core.secrets_store import SecretsStore


class _FakeKeyring:
    def __init__(self) -> None:
        self._data: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, account: str) -> str | None:
        return self._data.get((service, account))

    def set_password(self, service: str, account: str, password: str) -> None:
        self._data[(service, account)] = password


class _BrokenKeyring:
    def get_password(self, *_a, **_k):
        raise RuntimeError("no backend")

    def set_password(self, *_a, **_k):
        raise RuntimeError("no backend")


def _isolated_store(tmp_path: Path, keyring_api=None):
    return SecretsStore(tmp_path / "secrets.bin", keyring_api=keyring_api)


def _make_config() -> object:
    from omnicrawl.gui.core.config_serializer import from_yaml

    config = from_yaml(
        "project: {name: sec-t, workspace: work}\n"
        "source: {kind: crawl, seeds: [https://example.com/]}\n"
        "ai: {mode: cloud, default_provider: openai, providers: {openai: {type: openai_compatible}}}\n"
    )
    config.user_agent = "OmniCrawler-Test/1.0 (+contact: tester@example.org)"
    config.ai_mode = "cloud"
    config.ai_provider = "openai"
    config.ai_api_key_ref = "sk-plain-secret-value"
    return config


def test_s222_yaml_has_no_plaintext_and_writes_reference(tmp_path: Path) -> None:
    from omnicrawl.gui.core.config_serializer import to_yaml

    store = _isolated_store(tmp_path)
    with patch("omnicrawl.gui.core.config_serializer.SecretsStore", return_value=store):
        yaml_str = to_yaml(_make_config())
    assert "sk-plain-secret-value" not in yaml_str
    assert "secret://ai.openai.api_key" in yaml_str


def test_s222_reference_passthrough_preserved(tmp_path: Path) -> None:
    from omnicrawl.gui.core.config_serializer import to_yaml

    config = _make_config()
    config.ai_api_key_ref = "secret://ai.openai.api_key"
    store = _isolated_store(tmp_path)
    with patch("omnicrawl.gui.core.config_serializer.SecretsStore", return_value=store):
        yaml_str = to_yaml(config)
    assert "secret://ai.openai.api_key" in yaml_str
    assert not store.keys()


def test_s222_encrypted_reference_resolves_through_load_config(tmp_path: Path) -> None:
    from omnicrawl.core.config import load_config
    from omnicrawl.gui.core.config_serializer import to_yaml

    store = _isolated_store(tmp_path)
    config = _make_config()
    with patch("omnicrawl.gui.core.config_serializer.SecretsStore", return_value=store):
        yaml_str = to_yaml(config)
    path = tmp_path / "sec.yaml"
    path.write_text(yaml_str, encoding="utf-8")
    with patch("omnicrawl.core.credentials.SecretsStore", return_value=store):
        loaded = load_config(path)
    assert loaded.raw["ai"]["providers"]["openai"]["api_key"] == "sk-plain-secret-value"


def test_s222_unstoreable_key_raises_not_fallbacks_plaintext(tmp_path: Path, monkeypatch) -> None:
    from omnicrawl.gui.core.config_serializer import to_yaml

    monkeypatch.delenv("OMNICRAWL_MASTER_PASSWORD", raising=False)
    store = _isolated_store(tmp_path, keyring_api=_BrokenKeyring())
    with patch("omnicrawl.gui.core.config_serializer.SecretsStore", return_value=store):
        with pytest.raises(ValueError, match="无法安全保存 API key"):
            to_yaml(_make_config())


def test_s222_get_secret_falls_back_to_store(tmp_path: Path, monkeypatch) -> None:
    from omnicrawl.core.credentials import get_secret

    store = _isolated_store(tmp_path)
    store.set("ai.demo.api_key", "stored-value")
    monkeypatch.delenv("OMNICRAWL_SECRET_AI_DEMO_API_KEY", raising=False)
    monkeypatch.delenv("OMNICRAW_SECRET_AI_DEMO_API_KEY", raising=False)
    with patch("omnicrawl.core.credentials.SecretsStore", return_value=store):
        assert get_secret("ai.demo.api_key") == "stored-value"


def test_s222_get_secret_env_wins_over_store(tmp_path: Path, monkeypatch) -> None:
    from omnicrawl.core.credentials import get_secret

    store = _isolated_store(tmp_path)
    store.set("ai.demo.api_key", "stored-value")
    monkeypatch.setenv("OMNICRAWL_SECRET_AI_DEMO_API_KEY", "env-value")
    with patch("omnicrawl.core.credentials.SecretsStore", return_value=store):
        assert get_secret("ai.demo.api_key") == "env-value"
