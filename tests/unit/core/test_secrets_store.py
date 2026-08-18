"""S2.2.1：secrets_store 基础设施（AES-GCM + keyring 优先 / 密码派生 fallback）。

验收：密钥可存取；OS keyring 不可用时自动 fallback 密码派生，不抛未捕获异常。
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("cryptography")

from omnicrawler.core.secrets_store import (
    ENV_PASSWORD,
    FILE_MAGIC,
    FILE_MAGIC_V2,
    SecretsStore,
    SecretsStoreError,
)


class _FakeKeyring:
    def __init__(self) -> None:
        self._data: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, account: str) -> str | None:
        return self._data.get((service, account))

    def set_password(self, service: str, account: str, password: str) -> None:
        self._data[(service, account)] = password


class _BrokenKeyring:
    def get_password(self, *_args, **_kwargs):
        raise RuntimeError("no keyring backend")

    def set_password(self, *_args, **_kwargs):
        raise RuntimeError("no keyring backend")


def _store(tmp_path: Path, keyring_api=None, **kwargs) -> SecretsStore:
    return SecretsStore(tmp_path / "secrets.bin", keyring_api=keyring_api, **kwargs)


def test_s221_roundtrip_with_keyring(tmp_path: Path) -> None:
    store = _store(tmp_path, keyring_api=_FakeKeyring())
    store.set("api_key", "sk-live-123")
    assert store.get("api_key") == "sk-live-123"
    assert store.get("missing") is None
    assert store.keys() == ["api_key"]
    assert store.delete("api_key") is True
    assert store.delete("api_key") is False


def test_s221_file_contains_no_plaintext(tmp_path: Path) -> None:
    store = _store(tmp_path, keyring_api=_FakeKeyring())
    store.set("api_key", "sk-super-secret-value")
    raw = (tmp_path / "secrets.bin").read_bytes()
    # B05-001：v2 格式（带随机盐）为默认；v1 magic 也保留兼容
    assert raw.startswith((FILE_MAGIC, FILE_MAGIC_V2))
    assert b"sk-super-secret-value" not in raw
    assert b"api_key" not in raw


def test_s221_keyring_failure_falls_back_to_password(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv(ENV_PASSWORD, "correct horse battery staple")
    store = _store(tmp_path, keyring_api=_BrokenKeyring())
    store.set("token", "t0ken")
    assert store.get("token") == "t0ken"
    reopened = _store(tmp_path, keyring_api=_BrokenKeyring())
    assert reopened.get("token") == "t0ken"


def test_s221_no_keyring_no_password_raises_clear_error(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv(ENV_PASSWORD, raising=False)
    store = _store(tmp_path, keyring_api=_BrokenKeyring())
    with pytest.raises(SecretsStoreError, match=ENV_PASSWORD):
        store.set("k", "v")


def test_s221_master_key_resides_in_keyring(tmp_path: Path) -> None:
    keyring = _FakeKeyring()
    store = _store(tmp_path, keyring_api=keyring)
    store.set("k", "v")
    assert keyring.get_password("omnicrawler", "secrets-master") is not None


def test_s221_reopen_reads_same_value(tmp_path: Path) -> None:
    keyring = _FakeKeyring()
    first = _store(tmp_path, keyring_api=keyring)
    first.set("secret", "value-1")
    second = _store(tmp_path, keyring_api=keyring)
    assert second.get("secret") == "value-1"


def test_s221_corrupt_file_raises(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv(ENV_PASSWORD, "pw")
    target = tmp_path / "secrets.bin"
    target.write_bytes(b"garbage-not-magic")
    store = SecretsStore(target, keyring_api=_FakeKeyring())
    with pytest.raises(SecretsStoreError, match="格式损坏"):
        store.get("x")


def test_s221_wrong_key_fails_decrypt(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv(ENV_PASSWORD, "pw-a")
    store = _store(tmp_path, keyring_api=_BrokenKeyring())
    store.set("k", "v")
    monkeypatch.setenv(ENV_PASSWORD, "pw-b")
    reopened = _store(tmp_path, keyring_api=_BrokenKeyring())
    with pytest.raises(SecretsStoreError, match="解密失败"):
        reopened.get("k")


def test_s221_headers_and_api() -> None:
    assert FILE_MAGIC.startswith(b"OMNICRWL-SECRETS")
    assert "get" in dir(SecretsStore)
    assert "set" in dir(SecretsStore)
    assert "delete" in dir(SecretsStore)


# ── P9-B4（B05-001）：v2 随机 salt + v1 兼容 ───────────────────────


def test_v1_file_still_readable_after_v2_upgrade(tmp_path: Path, monkeypatch) -> None:
    """旧 v1 文件（无盐段）仍可读——兼容升级不丢凭据。"""
    from omnicrawler.core.secrets_store import DERIVE_SALT, _derived_key

    monkeypatch.setenv("OMNICRAWL_KEYRING_DISABLE", "1")
    monkeypatch.setenv("OMNICRAWL_MASTER_PASSWORD", "compat-pw")
    # 手工构造 v1 文件：MAGIC + nonce + ciphertext（固定 salt 派生）
    import base64
    import json

    key = _derived_key("compat-pw", salt=DERIVE_SALT)
    nonce = b"\x00" * 12
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    plaintext = json.dumps({"old_key": base64.b64encode(b"old-value").decode("ascii")}).encode()
    ct = AESGCM(key).encrypt(nonce, plaintext, FILE_MAGIC)
    store_path = tmp_path / "secrets.bin"
    store_path.write_bytes(FILE_MAGIC + nonce + ct)
    store = SecretsStore(store_path)
    assert store.get("old_key") == "old-value"


def test_v2_file_uses_random_salt(tmp_path: Path) -> None:
    """B05-001：新文件带头随机盐，两次写盘盐不同。"""
    store = _store(tmp_path, keyring_api=_FakeKeyring())
    store.set("api_key", "sk-abc")
    raw1 = (tmp_path / "secrets.bin").read_bytes()
    assert raw1.startswith(FILE_MAGIC_V2)
    store.set("other", "v2")
    raw2 = (tmp_path / "secrets.bin").read_bytes()
    assert raw1 != raw2  # 盐+nonce 随机
