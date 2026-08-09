"""S2.2.2 出口加密（.env AI key / secrets 引用）。

验收：GUI 保存 API key 只写 secret:// 引用；load_ai_env 解引用还原明文；
引用不可解时保留引用串（不泄漏明文）。
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from omnicrawl.core import credentials
from omnicrawl.core.ai_env import ai_env_path, load_ai_env, parse_env_file, save_ai_env


class FakeStore:
    def __init__(self) -> None:
        self.data: dict[str, str] = {}

    def set(self, key: str, value: str) -> None:
        self.data[key] = value

    def get(self, key: str) -> str | None:
        return self.data.get(key)

    def delete(self, key: str) -> bool:
        return self.data.pop(key, None) is not None

    def keys(self) -> list[str]:
        return list(self.data)


@pytest.fixture
def fake_store(monkeypatch: pytest.MonkeyPatch) -> FakeStore:
    store = FakeStore()
    monkeypatch.setattr(credentials, "SecretsStore", lambda *a, **k: store)
    return store


def test_s222_seal_secret_idempotent_for_refs() -> None:
    assert credentials.seal_secret("k", "secret://ai.env.KEY") == "secret://ai.env.KEY"
    assert credentials.seal_secret("k", "secret://ai.env.KEY ") == "secret://ai.env.KEY"


def test_s222_seal_secret_stores_encrypted(fake_store: FakeStore) -> None:
    ref = credentials.seal_secret("ai.env.OMNICRAWL_AI_API_KEY", "sk-plain-xyz")
    assert ref == "secret://ai.env.OMNICRAWL_AI_API_KEY"
    assert fake_store.data["ai.env.OMNICRAWL_AI_API_KEY"] == "sk-plain-xyz"


def test_s222_seal_secret_failure_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    class BrokenStore:
        def set(self, key: str, value: str) -> None:
            raise credentials.SecretsStoreError("no backend")

    monkeypatch.setattr(credentials, "SecretsStore", lambda *a, **k: BrokenStore())
    with pytest.raises(credentials.SecretsStoreError):
        credentials.seal_secret("k", "plain")


def test_s222_save_ai_env_writes_ref_not_plaintext(
    tmp_path: Path, fake_store: FakeStore
) -> None:
    ref = credentials.seal_secret("ai.env.OMNICRAWL_AI_API_KEY", "sk-gui-secret")
    path = save_ai_env(
        {"OMNICRAWL_AI_MODEL": "gpt-x", "OMNICRAWL_AI_API_KEY": ref}, project_root=tmp_path
    )
    assert path == ai_env_path(tmp_path)
    values = parse_env_file(path)
    assert values["OMNICRAWL_AI_API_KEY"] == "secret://ai.env.OMNICRAWL_AI_API_KEY"
    assert "sk-gui-secret" not in path.read_text(encoding="utf-8")
    assert values["OMNICRAWL_AI_MODEL"] == "gpt-x"


def test_s222_load_ai_env_resolves_refs(tmp_path: Path, fake_store: FakeStore) -> None:
    fake_store.data["ai.env.OMNICRAWL_AI_API_KEY"] = "sk-stored"
    save_ai_env(
        {"OMNICRAWL_AI_API_KEY": "secret://ai.env.OMNICRAWL_AI_API_KEY"}, project_root=tmp_path
    )
    loaded = load_ai_env(tmp_path)
    assert loaded["OMNICRAWL_AI_API_KEY"] == "sk-stored"


def test_s222_load_ai_env_keeps_unresolvable_ref(tmp_path: Path) -> None:
    save_ai_env(
        {"OMNICRAWL_AI_API_KEY": "secret://ai.env.GONE"}, project_root=tmp_path
    )
    loaded = load_ai_env(tmp_path)
    assert loaded["OMNICRAWL_AI_API_KEY"] == "secret://ai.env.GONE"


def test_s222_load_ai_env_prefers_os_environ(tmp_path: Path) -> None:
    save_ai_env(
        {"OMNICRAWL_AI_API_KEY": "secret://ai.env.OMNICRAWL_AI_API_KEY"}, project_root=tmp_path
    )
    os.environ["OMNICRAWL_AI_API_KEY"] = "from-env"
    try:
        assert load_ai_env(tmp_path)["OMNICRAWL_AI_API_KEY"] == "from-env"
    finally:
        os.environ.pop("OMNICRAWL_AI_API_KEY", None)
