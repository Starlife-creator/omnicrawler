"""§11.8 U5：storage_state 快照的 AES-GCM 信封（session_crypto）。

验收要点（§11.8.1 / §11.8.3）：

1. 往返无损（cookies / origins 逐字段相等）；
2. ★ 落盘字节**不含任何 cookie 明文子串**（反向断言目标：把 seal 换成明文 dump ⇒ 守卫红）；
3. ★ 判定只认 magic（单点）：**信封解密失败绝不回退明文解析**（反向断言目标：加回
   明文回退 ⇒ 守卫红）；
4. 旧明文 JSON 读取时一次性静默迁移为信封；
5. 密钥经 SecretsStore 双轨保管；密钥不符 ⇒ 显式报错引导重新登录。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("cryptography")

from omnicrawler.core.secrets_store import SecretsStore
from omnicrawler.fetching import session_crypto
from omnicrawler.fetching.session_crypto import (
    SESSION_KEY_NAME,
    SESSION_MAGIC,
    SessionCryptoError,
    classify_snapshot,
    load_storage_state,
    open_storage_state,
    save_storage_state,
    seal_storage_state,
)

_SECRET = "COOKIE-PLAINTEXT-VALUE-MUST-NOT-APPEAR-ON-DISK"


def _state() -> dict[str, Any]:
    return {
        "cookies": [
            {
                "name": "sid",
                "value": _SECRET,
                "domain": "example.org",
                "path": "/",
                "httpOnly": True,
                "secure": True,
                "sameSite": "Lax",
            }
        ],
        "origins": [
            {
                "origin": "https://example.org",
                "localStorage": [{"name": "token", "value": "tok-1"}],
            }
        ],
    }


def _store(tmp_path: Path) -> SecretsStore:
    return SecretsStore(tmp_path / "secrets.bin", keyring_api=_FakeKeyring())


class _FakeKeyring:
    def __init__(self) -> None:
        self._data: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, account: str) -> str | None:
        return self._data.get((service, account))

    def set_password(self, service: str, account: str, password: str) -> None:
        self._data[(service, account)] = password


# ── 往返与明文不落盘 ─────────────────────────────────────────


def test_seal_open_roundtrip_is_lossless(tmp_path: Path) -> None:
    store = _store(tmp_path)
    blob = seal_storage_state(_state(), store=store)
    assert open_storage_state(blob, store=store) == _state()


def test_sealed_blob_contains_no_cookie_plaintext(tmp_path: Path) -> None:
    """★ 核心守卫：信封字节里不得出现 cookie 值、键名或 localStorage 值。

    反向断言（本文件末尾）：把 seal 换成明文 dump ⇒ 本用例必须红。
    """
    store = _store(tmp_path)
    blob = seal_storage_state(_state(), store=store)
    assert _SECRET.encode("utf-8") not in blob
    assert b'"cookies"' not in blob
    assert b"localStorage" not in blob


def test_key_is_created_once_in_the_store(tmp_path: Path) -> None:
    store = _store(tmp_path)
    assert store.get(SESSION_KEY_NAME) is None
    seal_storage_state(_state(), store=store)
    first = store.get(SESSION_KEY_NAME)
    assert first is not None
    # 第二次复用同一密钥（不轮换 ⇒ 旧信封仍可读）。
    blob2 = seal_storage_state(_state(), store=store)
    assert store.get(SESSION_KEY_NAME) == first
    assert open_storage_state(blob2, store=store) == _state()


# ── 判定单点 ─────────────────────────────────────────────────


def test_classify_only_looks_at_magic(tmp_path: Path) -> None:
    assert classify_snapshot(SESSION_MAGIC + b"whatever") == "envelope"
    assert classify_snapshot(json.dumps({"cookies": []}).encode("utf-8")) == "plaintext"
    assert classify_snapshot(b"") == "plaintext"


# ── 损坏与密钥不符：绝不回退明文 ─────────────────────────────


def test_corrupted_envelope_raises_never_falls_back_to_plaintext(tmp_path: Path) -> None:
    """★ 反「假兼容」核心守卫：信封损坏 ⇒ 报错，**绝不**当明文 JSON 再试一次。"""
    store = _store(tmp_path)
    blob = bytearray(seal_storage_state(_state(), store=store))
    blob[-1] ^= 0xFF  # 篡改密文最后一字节
    with pytest.raises(SessionCryptoError):
        open_storage_state(bytes(blob), store=store)


def test_wrong_key_raises_and_guides_relogin(tmp_path: Path) -> None:
    blob = seal_storage_state(_state(), store=_store(tmp_path))
    with pytest.raises(SessionCryptoError, match="重新登录"):
        open_storage_state(blob, store=_store(tmp_path / "other"))


def test_truncated_envelope_raises(tmp_path: Path) -> None:
    store = _store(tmp_path)
    blob = seal_storage_state(_state(), store=store)
    with pytest.raises(SessionCryptoError):
        open_storage_state(blob[: len(SESSION_MAGIC) + 4], store=store)


def test_plaintext_json_parse_failure_is_reported_not_swallowed(tmp_path: Path) -> None:
    with pytest.raises(SessionCryptoError):
        load_storage_state(_write(tmp_path, b"{not-json"), store=_store(tmp_path))


def test_plaintext_top_level_list_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(SessionCryptoError):
        load_storage_state(_write(tmp_path, b"[1, 2]"), store=_store(tmp_path))


# ── 统一读入口：一次性迁移 ───────────────────────────────────


def _write(tmp_path: Path, blob: bytes) -> Path:
    path = tmp_path / "snap.playwright.json"
    path.write_bytes(blob)
    return path


def test_load_migrates_legacy_plaintex_exactly_once(tmp_path: Path) -> None:
    legacy = json.dumps(_state(), ensure_ascii=False).encode("utf-8")
    path = _write(tmp_path, legacy)

    # 首读：返回明文内容，且盘上文件被升级为信封。
    assert load_storage_state(path, store=_store(tmp_path)) == _state()
    migrated = path.read_bytes()
    assert classify_snapshot(migrated) == "envelope"

    # 二读：直接走信封，内容仍相等。
    assert load_storage_state(path, store=_store(tmp_path)) == _state()

    # 迁移写盘后，明文字节不再存在于盘上。
    assert _SECRET.encode("utf-8") not in migrated


def test_load_can_skip_migration(tmp_path: Path) -> None:
    legacy = json.dumps(_state()).encode("utf-8")
    path = _write(tmp_path, legacy)
    assert load_storage_state(path, store=_store(tmp_path), migrate=False) == _state()
    assert classify_snapshot(path.read_bytes()) == "plaintext"


# ── 统一写入口：原子写 + 0600 ────────────────────────────────


def test_save_writes_envelope_atomically_without_tmp_leftover(tmp_path: Path) -> None:
    import os
    import stat

    path = tmp_path / "nested" / "snap.playwright.json"
    save_storage_state(_state(), path, store=_store(tmp_path))

    assert path.is_file()
    assert classify_snapshot(path.read_bytes()) == "envelope"
    assert not path.with_name(path.name + ".tmp").exists()
    if os.name == "posix":
        assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_open_rejects_non_envelope_input(tmp_path: Path) -> None:
    with pytest.raises(SessionCryptoError):
        open_storage_state(b'{"cookies": []}', store=_store(tmp_path))


# ── 反向断言：把缺口装回 ⇒ 守卫必须红 ⇒ 字节级还原 ───────────


def test_reverse_assertions(tmp_path: Path) -> None:
    """两条反向断言在**同一进程内**完成：装回缺口 ⇒ 守卫红 ⇒ 还原 ⇒ 守卫绿。

    ① 缺口＝seal 被换成明文 dump（明文落盘守卫转红）；
    ② 缺口＝open 对解密失败回退明文解析（防假兼容守卫转红）。
    还原＝``MonkeyPatch.context()`` 退出时撤销（等价于字节级还原：被替换的是
    函数引用，撤销后源码与行为逐位一致）。★ 用独立 context 而非 undo()，
    避免把 conftest fixture 设置的隔离环境变量一并撤掉。
    """
    store = _store(tmp_path)
    original_seal = session_crypto.seal_storage_state
    original_open = session_crypto.open_storage_state

    # ① 装回缺口：明文 dump。
    def _plain_dump(state: Any, **_kw: Any) -> bytes:
        return json.dumps(dict(state), ensure_ascii=False).encode("utf-8")

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(session_crypto, "seal_storage_state", _plain_dump)
        leaked = session_crypto.seal_storage_state(_state(), store=store)
        assert _SECRET.encode("utf-8") in leaked  # 缺口确实存在（守卫判据可判红）
        with pytest.raises(AssertionError):
            assert _SECRET.encode("utf-8") not in leaked
    assert session_crypto.seal_storage_state is original_seal  # 还原
    sealed = session_crypto.seal_storage_state(_state(), store=store)
    assert _SECRET.encode("utf-8") not in sealed  # 还原后守卫回绿

    # ② 装回缺口：解密失败回退明文解析。
    def _fallback_open(blob: bytes, **_kw: Any) -> dict[str, Any]:
        if classify_snapshot(blob) == "envelope":
            try:
                return original_open(blob, **_kw)
            except SessionCryptoError:
                return json.loads(blob.decode("utf-8"))
        return json.loads(blob.decode("utf-8"))

    corrupted = bytearray(sealed)
    corrupted[-1] ^= 0xFF
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(session_crypto, "open_storage_state", _fallback_open)
        with pytest.raises(Exception) as exc_info:
            # 缺口行为：损坏信封被当明文解析 ⇒ 抛出与实现无关的裸解码/JSON 错误，
            # 丢失"引导重新登录"的可行动语义。
            session_crypto.open_storage_state(bytes(corrupted), store=store)
        assert not isinstance(exc_info.value, SessionCryptoError)
    assert session_crypto.open_storage_state is original_open  # 还原
    with pytest.raises(SessionCryptoError):
        session_crypto.open_storage_state(bytes(corrupted), store=store)  # 还原后回绿
