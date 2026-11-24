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


def test_silent_keyring_write_uses_recoverable_password_key(tmp_path, monkeypatch):
    class NoopKeyring:
        def get_password(self, *args):
            return None
        def set_password(self, *args):
            pass
    monkeypatch.setenv(ENV_PASSWORD, "test-master")
    path = tmp_path / "silent.bin"
    SecretsStore(path, keyring_api=NoopKeyring()).set("sample", "test-value")
    assert SecretsStore(path, keyring_api=NoopKeyring()).get("sample") == "test-value"

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


def test_key_derivation_is_memoised_but_never_reuses_a_stale_password(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """派生结果可以缓存，但**换密码必须换密钥**。

    背景（2026-11-24 实测）：`PBKDF2_ITERATIONS = 600_000` 单次 74ms，而
    `encrypt` / `decrypt` 每次都取主密钥 —— `CookieSession.save()` 因此每次落盘
    多付 74ms，一条并发用例跑了 8.8 秒纯派生。缓存是必要的；但缓存键必须
    包含密码本身，否则改 `OMNICRAWL_MASTER_PASSWORD` 之后会拿旧密钥去解新文件。
    """
    from omnicrawler.core import secrets_store as module

    module._DERIVED_KEY_CACHE.clear()
    first = module._derived_key("pw-one")
    second = module._derived_key("pw-one")
    assert first == second, "同一密码同一盐必须得到同一密钥"
    assert len(module._DERIVED_KEY_CACHE) == 1, "第二次不应再派生（缓存命中）"

    other = module._derived_key("pw-two")
    assert other != first, "换密码必须换密钥 —— 否则旧密钥会去解新文件"
    assert len(module._DERIVED_KEY_CACHE) == 2

    # 不同盐同样必须分键（v2 文件头是随机盐）
    salted = module._derived_key("pw-one", salt=b"another-salt-16b")
    assert salted != first
    assert len(module._DERIVED_KEY_CACHE) == 3


def test_encrypt_is_fast_after_the_first_derivation(monkeypatch: pytest.MonkeyPatch) -> None:
    """重复加密不得反复付 PBKDF2 的代价（这是实测 8.8 秒的来源）。"""
    import time

    from omnicrawler.core import secrets_store as module

    module._DERIVED_KEY_CACHE.clear()
    monkeypatch.setenv(module.ENV_PASSWORD, "speed-check-password")
    monkeypatch.setenv("OMNICRAWL_KEYRING_DISABLE", "1")
    store = module.SecretsStore()

    store.encrypt(b"warm")  # 首次含一次派生
    started = time.perf_counter()
    for _ in range(20):
        store.encrypt(b"payload")
    elapsed = time.perf_counter() - started
    assert elapsed < 1.0, f"20 次加密耗时 {elapsed:.2f}s，派生缓存未生效"
