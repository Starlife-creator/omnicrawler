"""S2.2.3：cookie 原子写 + 加密落盘。

验收：中途崩溃不残留损坏 cookie 文件（原子 rename）；cookie 文件无明文（AES-GCM）；
加载失败显式告警；旧版明文 LWP 格式向后兼容。
"""

from __future__ import annotations

import logging
from http.cookiejar import Cookie
from pathlib import Path
from unittest.mock import patch

import pytest

pytest.importorskip("cryptography")

from omnicrawl.core.secrets_store import FILE_MAGIC, SecretsStore
from omnicrawl.fetching.session import CookieSession


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


def _cookie(name: str = "session", value: str = "s3cr3t-value") -> Cookie:
    return Cookie(
        version=0, name=name, value=value,
        port=None, port_specified=False, domain="example.org",
        domain_specified=True, domain_initial_dot=False,
        path="/", path_specified=True, secure=False, expires=None, discard=True,
        comment=None, comment_url=None, rest={}, rfc2109=False,
    )


def _session(tmp_path: Path) -> CookieSession:
    path = tmp_path / "default.cookies"
    return CookieSession(path)


def test_s223_save_writes_encrypted_blob_no_plaintext(tmp_path: Path) -> None:
    store = SecretsStore(tmp_path / "s.bin", keyring_api=_FakeKeyring())
    with __import__("unittest.mock").mock.patch("omnicrawl.fetching.session.SecretsStore", return_value=store):
        session = _session(tmp_path)
        session.jar.set_cookie(_cookie())
        session.save()
    raw = session.path.read_bytes()
    assert raw.startswith(FILE_MAGIC)
    assert b"s3cr3t-value" not in raw
    assert b"EXAMPLE.COM" not in raw and b"example.com" not in raw


def test_s223_reload_restores_cookies(tmp_path: Path) -> None:
    store = SecretsStore(tmp_path / "s.bin", keyring_api=_FakeKeyring())
    with patch("omnicrawl.fetching.session.SecretsStore", return_value=store):
        session = _session(tmp_path)
        session.jar.set_cookie(_cookie())
        session.save()
        reopened = CookieSession(session.path)
    cookie = next(iter(reopened.jar))
    assert cookie.name == "session"
    assert cookie.value == "s3cr3t-value"


def test_s223_atomic_replace_preserves_previous_file(tmp_path: Path, monkeypatch) -> None:
    import os as _os

    store = SecretsStore(tmp_path / "s.bin", keyring_api=_FakeKeyring())
    with patch("omnicrawl.fetching.session.SecretsStore", return_value=store):
        session = _session(tmp_path)
        session.jar.set_cookie(_cookie("first"))
        session.save()
        snapshot = session.path.read_bytes()
        real_replace = _os.replace
        monkeypatch.setattr("os.replace", lambda _a, _b: (_ for _ in ()).throw(OSError("boom")))
        session.jar.set_cookie(_cookie("second"))
        with pytest.raises(_os.error):
            session.save()  # 替换抛错 → 原文件保持完整
        assert session.path.read_bytes() == snapshot
        monkeypatch.setattr("os.replace", real_replace)
        session.save()
        assert not session.path.with_name(session.path.name + ".tmp").exists()
        reopened = CookieSession(session.path)
    assert next(iter(reopened.jar)).name == "first"


def test_s223_load_failure_warns(caplog, tmp_path: Path) -> None:
    target = tmp_path / "bad.cookies"
    target.write_bytes(b"### THIS IS NOT VALID LWP DATA # broken")
    store = SecretsStore(tmp_path / "s.bin", keyring_api=_FakeKeyring())
    with patch("omnicrawl.fetching.session.SecretsStore", return_value=store):
        with caplog.at_level(logging.WARNING):
            CookieSession(target)
    assert any("cookie 文件加载失败" in record.message for record in caplog.records)


def test_s223_legacy_plaintext_lwp_still_loads(tmp_path: Path) -> None:
    target = tmp_path / "legacy.cookies"
    target.write_text(
        "#LWP-Cookies-2.0\n"
        "Set-Cookie3: sid=legacy-value; path=\"/\"; domain=example.org; "
        "path_spec; discard; version=0\n",
        encoding="utf-8",
    )
    session = CookieSession(target)
    cookie = next(iter(session.jar))
    assert cookie.value == "legacy-value"


def test_s223_encrypt_failure_skips_disk(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("OMNICRAWL_MASTER_PASSWORD", raising=False)
    store = SecretsStore(tmp_path / "s.bin", keyring_api=_BrokenKeyring())
    with patch("omnicrawl.fetching.session.SecretsStore", return_value=store):
        session = _session(tmp_path)
        session.jar.set_cookie(_cookie())
        session.save()
        assert not session.path.exists()


def test_s223_no_persist_path_is_noop(tmp_path: Path) -> None:
    session = CookieSession(None)
    session.jar.set_cookie(_cookie())
    session.save()
    assert True
