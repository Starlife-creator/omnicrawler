"""代理池落盘前的凭据密封（`_seal_proxy_list`）——安全边界守卫。

为什么要有这个文件：这段逻辑决定「代理密码会不会被明文写进 INI」。它此前
**零测试**，而它恰好有一个真实泄漏（2026-11-24 实测）：

    用户手写代理池时常省略 scheme，直接写 `user:pass@proxy:8080`。
    旧实现用 `"://" not in text` 判断「没有凭据」⇒ 整串**原样**返回
    ⇒ 密码明文写进配置文件。

也就是说，判据当时检的是「有没有 scheme」，而不是「有没有凭据」——
这两个问题在用户实际输入里并不等价。

本文件钉住三层：

1. `_has_proxy_credentials` 的判定矩阵（含**无 scheme** 与**不该误判**两类）；
2. 有凭据 ⇒ 一律密封成 `secret://` 引用，往返可取回；
3. 加密不可用 ⇒ **抛异常**，调用方据此取消保存 —— 绝不回退写明文。
"""

from __future__ import annotations

import pytest

from omnicrawler.gui.views.stealth_settings import _has_proxy_credentials, _seal_proxy_list


@pytest.fixture
def sealing_store(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """把 secrets_store 指到临时库，并强制走密码派生（不碰系统 keyring）。"""
    monkeypatch.setenv("OMNICRAWL_SECRET_STORE_PATH", str(tmp_path / "secrets.bin"))
    monkeypatch.setenv("OMNICRAWL_MASTER_PASSWORD", "test-only-master-password")
    monkeypatch.setenv("OMNICRAWL_KEYRING_DISABLE", "1")


@pytest.mark.parametrize(
    "text",
    [
        "http://user:pass@proxy:8080",
        "https://user:pass@proxy:8080",
        "socks5://u:p@h:1080",
        # ★ 下面这条就是实测泄漏的那一种：省略 scheme
        "user:pass@proxy:8080",
        "HTTP://USER:PASS@PROXY:8080",
        "http://a:b@h:1\nhttp://c:d@h:2",
        "http://a:b@h:1\nhttp://plain:8080",
    ],
)
def test_credentials_are_detected_with_or_without_a_scheme(text: str) -> None:
    """有凭据就必须判出来 —— 与有没有 `scheme://` 无关。"""
    assert _has_proxy_credentials(text), f"{text!r} 含 user:pass@ 却没被判为凭据"


@pytest.mark.parametrize(
    "text",
    [
        "",
        "http://proxy:8080",
        "socks5://proxy:1080",
        # `@` 出现在路径/查询里，不是授权段
        "http://host/path?a=b@c",
        "secret://settings.proxy_list",
        "proxy.example.com:8080",
    ],
)
def test_plain_proxies_are_not_mistaken_for_credentials(text: str) -> None:
    """误判会让「改个端口」也去动加密库 —— 判据必须是凭据形态本身。"""
    assert not _has_proxy_credentials(text), f"{text!r} 没有凭据却被判为凭据"


def test_sealing_round_trips_including_the_schemeless_form(sealing_store: None) -> None:
    """密封后可原样取回；无 scheme 的形态也必须走同一条路。"""
    from omnicrawler.core.credentials import get_secret

    for original in ("http://user:pass@proxy:8080", "user:pass@proxy:8080"):
        sealed = _seal_proxy_list(original)
        assert sealed.startswith("secret://"), f"{original!r} 未被密封：{sealed!r}"
        assert "pass" not in sealed, "引用串里不得残留密码"
        assert get_secret(sealed[len("secret://") :]) == original, "往返后必须取回原文"


def test_plain_proxy_list_is_stored_as_is(sealing_store: None) -> None:
    """没有凭据就不该动加密库 —— 否则每次保存都要付一次派生代价。"""
    text = "http://proxy1:8080\nsocks5://proxy2:1080"
    assert _seal_proxy_list(text) == text


def test_already_sealed_reference_is_idempotent(sealing_store: None) -> None:
    """保存引用串不重复加密（否则引用会被当成明文再封一层）。"""
    ref = "secret://settings.proxy_list"
    assert _seal_proxy_list(ref) == ref


def test_sealing_failure_raises_instead_of_falling_back_to_plaintext(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """加密不可用时**必须抛异常**，绝不能「加密失败就写明文」。

    这是 `_save_and_accept` 里那段 `except → 取消保存` 的前提：
    上游能正确取消，完全依赖这里不静默降级。
    """
    monkeypatch.setenv("OMNICRAWL_SECRET_STORE_PATH", str(tmp_path / "secrets.bin"))
    monkeypatch.delenv("OMNICRAWL_MASTER_PASSWORD", raising=False)
    monkeypatch.setenv("OMNICRAWL_KEYRING_DISABLE", "1")

    def _no_keyring() -> None:
        raise RuntimeError("no keyring in this environment")

    import omnicrawler.core.secrets_store as store_module

    monkeypatch.setattr(store_module, "_load_keyring", _no_keyring)

    with pytest.raises(Exception) as excinfo:
        _seal_proxy_list("user:pass@proxy:8080")
    message = str(excinfo.value)
    assert "user:pass" not in message, "异常信息里不得回显密码"
    assert "pass" not in message.split("proxy")[0], "异常信息里不得回显密码"
