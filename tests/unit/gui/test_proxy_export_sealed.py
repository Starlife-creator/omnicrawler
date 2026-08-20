"""S2.2.2 出口加密（settings.ini 代理池）。

验收：含凭据代理加密入 secrets_store，INI 只存 secret:// 引用；
AppSettings.proxy_list 解引用还原明文；解引用失败返回空串不泄漏。
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from omnicrawler.core import credentials
from omnicrawler.gui.views.stealth_settings import _seal_proxy_list


class FakeStore:
    def __init__(self) -> None:
        self.data: dict[str, str] = {}

    def set(self, key: str, value: str) -> None:
        self.data[key] = value

    def get(self, key: str) -> str | None:
        return self.data.get(key)

    def keys(self) -> list[str]:
        return list(self.data)


@pytest.fixture
def fake_store(monkeypatch: pytest.MonkeyPatch) -> FakeStore:
    store = FakeStore()
    monkeypatch.setattr(credentials, "SecretsStore", lambda *a, **k: store)
    return store


def test_s222_seal_proxy_list_empty_and_plain() -> None:
    assert _seal_proxy_list("") == ""
    assert _seal_proxy_list("   ") == ""
    assert _seal_proxy_list("http://1.2.3.4:8080") == "http://1.2.3.4:8080"
    assert _seal_proxy_list("http://1.2.3.4:8080\nsocks5://5.6.7.8:1080") == (
        "http://1.2.3.4:8080\nsocks5://5.6.7.8:1080"
    )


def test_s222_seal_proxy_list_with_credentials(fake_store: FakeStore) -> None:
    ref = _seal_proxy_list("http://user:pass@1.2.3.4:8080")
    assert ref == "secret://settings.proxy_list"
    assert fake_store.data["settings.proxy_list"] == "http://user:pass@1.2.3.4:8080"


def test_s222_seal_proxy_list_mixed_lines(fake_store: FakeStore) -> None:
    ref = _seal_proxy_list("http://a:secret@1.2.3.4:8080\nhttp://5.6.7.8:8080")
    assert ref == "secret://settings.proxy_list"
    assert fake_store.data["settings.proxy_list"] == "http://a:secret@1.2.3.4:8080\nhttp://5.6.7.8:8080"


def test_s222_seal_proxy_list_ref_idempotent() -> None:
    assert _seal_proxy_list("secret://settings.proxy_list") == "secret://settings.proxy_list"


def test_s222_seal_proxy_list_failure_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    class BrokenStore:
        def set(self, key: str, value: str) -> None:
            raise credentials.SecretsStoreError("no backend")

    monkeypatch.setattr(credentials, "SecretsStore", lambda *a, **k: BrokenStore())
    with pytest.raises(credentials.SecretsStoreError):
        _seal_proxy_list("http://user:pass@1.2.3.4:8080")


class FakeQSettings:
    def __init__(self, values: dict | None = None) -> None:
        self.values: dict = values or {}

    def value(self, key: str, default=None, type=None):  # noqa: A002
        return self.values.get(key, default)

    def isWritable(self) -> bool:
        return True

    def setValue(self, key: str, value) -> None:
        self.values[key] = value

    def status(self) -> int:
        return 0


def test_s222_proxy_list_property_resolves_ref(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        credentials, "get_secret", lambda name: "http://user:pass@1.2.3.4:8080"
    )
    from PySide6.QtWidgets import QApplication

    from omnicrawler.gui.settings import AppSettings

    QApplication.instance() or QApplication([])
    settings = AppSettings()
    settings._settings = FakeQSettings({"proxy/list": "secret://settings.proxy_list"})
    assert settings.proxy_list == "http://user:pass@1.2.3.4:8080"


def test_s222_proxy_list_property_unresolvable_returns_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def raising(name: str) -> str:
        raise ValueError(f"凭据 {name!r} 未配置")

    monkeypatch.setattr(credentials, "get_secret", raising)
    from PySide6.QtWidgets import QApplication

    from omnicrawler.gui.settings import AppSettings

    QApplication.instance() or QApplication([])
    settings = AppSettings()
    settings._settings = FakeQSettings({"proxy/list": "secret://settings.proxy_list"})
    assert settings.proxy_list == ""


def test_s222_proxy_list_property_plaintext_passthrough() -> None:
    from PySide6.QtWidgets import QApplication

    from omnicrawler.gui.settings import AppSettings

    QApplication.instance() or QApplication([])
    settings = AppSettings()
    settings._settings = FakeQSettings({"proxy/list": "http://1.2.3.4:8080"})
    assert settings.proxy_list == "http://1.2.3.4:8080"
