"""`SecretsStore` 不得在构造时导入 keyring。

为什么要有这个文件（2026-11-25 实测）：

`import keyring` 实测 **238 ms**（它拉起 `jaraco.*` 共 16 个模块）。
而 GUI 启动路径上 `_gui_app_config()` 会构造 `SecretsStore()` —— 构造时
同步导入就等于让窗口出现多等 238 ms。

实测（MainWindow 构造）：

```
优化前  SecretsStore() 首次 179 ms
优化后  SecretsStore() 首次 0.1 ms
```

而且大多数启动路径**根本用不到** keyring：

* `OMNICRAWL_KEYRING_DISABLE` 已设 ⇒ 直接 None；
* 设了 `OMNICRAWL_MASTER_PASSWORD` ⇒ 走密码派生，从不碰 keyring；
* 只读配置、不读凭据。

本文件钉住「构造零开销」，并证明惰性化**不改变语义**。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from omnicrawler.core import secrets_store
from omnicrawler.core.secrets_store import SecretsStore


@pytest.fixture
def clean_keyring_state(tmp_path: Path, monkeypatch):
    """重置模块级 keyring 缓存，让「是否已导入」可控。"""
    monkeypatch.setattr(secrets_store, "_keyring", secrets_store._UNSET)
    monkeypatch.delenv("OMNICRAWL_KEYRING_DISABLE", raising=False)
    monkeypatch.setenv("OMNICRAWL_SECRET_STORE_PATH", str(tmp_path / "s.bin"))
    yield
    monkeypatch.setattr(secrets_store, "_keyring", secrets_store._UNSET)


def test_construction_does_not_import_keyring(tmp_path: Path, monkeypatch) -> None:
    """构造 SecretsStore 不得触发 keyring 导入。"""
    monkeypatch.setattr(secrets_store, "_keyring", secrets_store._UNSET)
    calls = [0]
    real = secrets_store._load_keyring

    def counting():
        calls[0] += 1
        return real()

    monkeypatch.setattr(secrets_store, "_load_keyring", counting)
    monkeypatch.setenv("OMNICRAWL_SECRET_STORE_PATH", str(tmp_path / "s.bin"))

    SecretsStore()
    assert calls[0] == 0, "构造时就解析了 keyring —— 冷启动会白等 238ms"


def test_keyring_resolves_on_first_access(tmp_path: Path, monkeypatch) -> None:
    """第一次真的读 keyring 时才解析，且只解析一次。"""
    monkeypatch.setattr(secrets_store, "_keyring", secrets_store._UNSET)
    calls = [0]
    real = secrets_store._load_keyring

    def counting():
        calls[0] += 1
        return real()

    monkeypatch.setattr(secrets_store, "_load_keyring", counting)
    monkeypatch.setenv("OMNICRAWL_SECRET_STORE_PATH", str(tmp_path / "s.bin"))

    store = SecretsStore()
    assert calls[0] == 0
    _ = store.keyring          # 第一次访问
    assert calls[0] == 1, "第一次访问没有解析 keyring"
    _ = store.keyring          # 第二次
    assert calls[0] == 1, "重复解析了 keyring（应缓存）"


def test_disable_env_never_resolves_keyring(tmp_path: Path, monkeypatch) -> None:
    """`OMNICRAWL_KEYRING_DISABLE` ⇒ 永远不导入 keyring，返回 None。"""
    monkeypatch.setattr(secrets_store, "_keyring", secrets_store._UNSET)
    monkeypatch.setenv("OMNICRAWL_KEYRING_DISABLE", "1")
    monkeypatch.setenv("OMNICRAWL_SECRET_STORE_PATH", str(tmp_path / "s.bin"))

    store = SecretsStore()
    assert store.keyring is None, "DISABLE 生效时仍拿到了 keyring 后端"


def test_injected_backend_still_wins(tmp_path: Path, monkeypatch) -> None:
    """注入的 keyring_api 优先 —— 惰性化不得覆盖它。"""
    monkeypatch.setattr(secrets_store, "_keyring", secrets_store._UNSET)
    monkeypatch.setenv("OMNICRAWL_SECRET_STORE_PATH", str(tmp_path / "s.bin"))

    class _Fake:
        def get_password(self, service, username): return None
        def set_password(self, service, username, password): pass

    fake = _Fake()
    store = SecretsStore(keyring_api=fake)
    assert store.keyring is fake


def test_setter_still_works(tmp_path: Path, monkeypatch) -> None:
    """`store.keyring = x` 仍可替换后端（既有注入路径依赖它）。"""
    monkeypatch.setattr(secrets_store, "_keyring", secrets_store._UNSET)
    monkeypatch.setenv("OMNICRAWL_SECRET_STORE_PATH", str(tmp_path / "s.bin"))
    store = SecretsStore()
    sentinel = object()
    store.keyring = sentinel
    assert store.keyring is sentinel


def test_disable_env_beats_injected_backend(tmp_path: Path, monkeypatch) -> None:
    """`OMNICRAWL_KEYRING_DISABLE` **压过**注入的后端 —— 这是改造前的语义。

    为什么单独钉这一条（2026-11-25 真实回归）：惰性化第一版写成
    「注入了就先解析」，于是 `test_session_crypto` 里每个 `_store()` 都新建一个
    空 `_FakeKeyring`，第二个实例拿不到第一个写入的主密钥 ⇒ 解密失败。

    语义必须逐字保持：`None if DISABLE else (注入 or _load_keyring())`。
    """
    monkeypatch.setenv("OMNICRAWL_KEYRING_DISABLE", "1")
    monkeypatch.setenv("OMNICRAWL_SECRET_STORE_PATH", str(tmp_path / "s.bin"))

    class _Fake:
        def get_password(self, service, username): return "should-not-be-used"
        def set_password(self, service, username, password): pass

    store = SecretsStore(keyring_api=_Fake())
    assert store.keyring is None, "DISABLE 生效时注入的后端不应被采用"


def test_injected_backend_is_used_without_disable(tmp_path: Path, monkeypatch) -> None:
    """没有 DISABLE 时，注入的后端立即生效且不触发 keyring 导入。"""
    monkeypatch.delenv("OMNICRAWL_KEYRING_DISABLE", raising=False)
    monkeypatch.setattr(secrets_store, "_keyring", secrets_store._UNSET)
    monkeypatch.setenv("OMNICRAWL_SECRET_STORE_PATH", str(tmp_path / "s.bin"))
    calls = [0]
    real = secrets_store._load_keyring

    def counting():
        calls[0] += 1
        return real()

    monkeypatch.setattr(secrets_store, "_load_keyring", counting)

    class _Fake:
        def get_password(self, service, username): return None
        def set_password(self, service, username, password): pass

    fake = _Fake()
    store = SecretsStore(keyring_api=fake)
    assert store.keyring is fake
    assert calls[0] == 0, "注入了后端却还去导入 keyring"
