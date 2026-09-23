"""U2 会话桥验收（《优化方案》§11.1 U2）。

三条判据各有专门用例：
  * **逐字段**：HttpOnly / SameSite / domain 前导点 / expires / secure / path；
  * **按 domain 归还、不全量倒 jar**（反向对照：第三方域必须被挡住）；
  * **枚举为空必须报错**（没有匹配项 / hosts 传空 / 没有 cookies 字段）。
另加"凭据不进日志"与"落盘走既有加密模块（无新增明文）"两条红线用例。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

pytest.importorskip("cryptography")

from omnicrawler.core.config import AppConfig, load_config
from omnicrawler.core.secrets_store import FILE_MAGIC, SecretsStore
from omnicrawler.fetching.session import CookieSession
from omnicrawler.fetching.session_bridge import (
    BridgeResult,
    SessionBridgeError,
    bridge_from_storage_state_file,
    bridge_storage_state,
    cookie_applies_to_host,
    playwright_cookie_to_cookie,
)
from omnicrawler.fetching.session_state import SessionPersistenceDisabledError

_SECRET = "SESSION-COOKIE-SECRET-VALUE"


class _FakeKeyring:
    def __init__(self) -> None:
        self._data: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, account: str) -> str | None:
        return self._data.get((service, account))

    def set_password(self, service: str, account: str, password: str) -> None:
        self._data[(service, account)] = password


def _record(**overrides: Any) -> dict[str, Any]:
    record: dict[str, Any] = {
        "name": "sid",
        "value": _SECRET,
        "domain": "example.org",
        "path": "/",
        "expires": -1,
        "httpOnly": True,
        "secure": True,
        "sameSite": "Lax",
    }
    record.update(overrides)
    return record


def _config(tmp_path: Path, *, persist: bool = True) -> AppConfig:
    config_path = tmp_path / "task.yaml"
    config_path.write_text(
        "project: {name: u2, workspace: work}\n"
        "source: {kind: static_html, seeds: [https://example.org/]}\n"
        f"session: {{persist_cookies: {str(persist).lower()}, name: default}}\n",
        encoding="utf-8",
    )
    return load_config(config_path)


def _session(tmp_path: Path) -> CookieSession:
    return CookieSession(tmp_path / "work" / "sessions" / "default.cookies")


def _jar_index(session: CookieSession) -> set[tuple[str, str]]:
    return {(cookie.name, cookie.domain) for cookie in session.jar}


# ── 1. 逐字段 ────────────────────────────────────────────


def test_http_only_is_preserved_in_rest() -> None:
    # stdlib 的 Cookie 没有 HttpOnly 属性；约定放在 nonstandard attr（= _rest）里，
    # 与它自己解析 Set-Cookie 时同一形态。用**公开访问器**读取。
    cookie = playwright_cookie_to_cookie(_record(httpOnly=True))
    assert cookie.has_nonstandard_attr("HttpOnly")
    plain = playwright_cookie_to_cookie(_record(httpOnly=False))
    assert not plain.has_nonstandard_attr("HttpOnly")


@pytest.mark.parametrize("same_site", ["Strict", "Lax", "None"])
def test_same_site_values_are_preserved(same_site: str) -> None:
    cookie = playwright_cookie_to_cookie(_record(sameSite=same_site))
    assert cookie.get_nonstandard_attr("SameSite") == same_site


def test_absent_same_site_is_not_invented() -> None:
    cookie = playwright_cookie_to_cookie(_record(sameSite=""))
    assert not cookie.has_nonstandard_attr("SameSite")


def test_unknown_same_site_is_rejected() -> None:
    """语义不明的字段不许悄悄改写或丢弃 ⇒ 显式报错。"""
    with pytest.raises(SessionBridgeError):
        playwright_cookie_to_cookie(_record(sameSite="Weird"))


@pytest.mark.parametrize(
    ("domain", "expected_dot", "expected_specified"),
    [
        (".example.org", True, True),
        ("example.org", False, False),
        (".sub.example.org", True, True),
    ],
)
def test_domain_leading_dot_marks_domain_cookie(
    domain: str, expected_dot: bool, expected_specified: bool
) -> None:
    cookie = playwright_cookie_to_cookie(_record(domain=domain))
    assert cookie.domain == domain
    assert cookie.domain_initial_dot is expected_dot
    assert cookie.domain_specified is expected_specified


@pytest.mark.parametrize(
    ("expires", "expected_expires", "expected_discard"),
    [
        (-1, None, True),
        (0, None, True),
        (None, None, True),
        (1_700_000_000, 1_700_000_000, False),
        (1_700_000_000.75, 1_700_000_000, False),
    ],
)
def test_expires_mapping(expires: Any, expected_expires: int | None, expected_discard: bool) -> None:
    cookie = playwright_cookie_to_cookie(_record(expires=expires))
    assert cookie.expires == expected_expires
    assert cookie.discard is expected_discard


def test_non_numeric_expires_is_rejected() -> None:
    with pytest.raises(SessionBridgeError):
        playwright_cookie_to_cookie(_record(expires="tomorrow"))


def test_secure_and_path_are_preserved() -> None:
    cookie = playwright_cookie_to_cookie(_record(secure=True, path="/app"))
    assert cookie.secure is True
    assert cookie.path == "/app"
    assert cookie.path_specified is True

    defaulted = playwright_cookie_to_cookie(_record(path=""))
    assert defaulted.path == "/"
    assert defaulted.path_specified is False


def test_missing_name_or_domain_is_rejected() -> None:
    with pytest.raises(SessionBridgeError):
        playwright_cookie_to_cookie(_record(name=""))
    with pytest.raises(SessionBridgeError):
        playwright_cookie_to_cookie(_record(domain=""))


# ── 2. 域名匹配 ──────────────────────────────────────────


@pytest.mark.parametrize(
    ("cookie_domain", "host", "expected"),
    [
        (".example.org", "example.org", True),
        (".example.org", "www.example.org", True),
        (".example.org", "deep.www.example.org", True),
        ("example.org", "example.org", True),
        ("example.org", "www.example.org", False),  # host-only 不外扩
        (".example.org", "notexample.org", False),  # 后缀不是子域边界
        (".example.org", "example.org.evil.net", False),
        (".EXAMPLE.ORG", "www.example.org", True),  # 大小写不敏感
        (".example.org.", "example.org", True),  # 尾点归一
        ("", "example.org", False),
    ],
)
def test_cookie_applies_to_host_rules(cookie_domain: str, host: str, expected: bool) -> None:
    assert cookie_applies_to_host(cookie_domain, host) is expected


# ── 3. 桥接：按 domain 归还 + 空枚举报错 ─────────────────


def test_foreign_domains_are_not_poured_into_the_jar(tmp_path: Path) -> None:
    """★ 只归还目标站点的 cookie；第三方/统计域**不得**被倒进 jar。"""
    session = _session(tmp_path)
    state = {
        "cookies": [
            _record(name="mine", domain="example.org"),
            _record(name="shared", domain=".example.org"),
            _record(name="tracker", domain=".analytics.example.net"),
            _record(name="other", domain="unrelated.test"),
        ],
        "origins": [],
    }

    result = bridge_storage_state(state, session, hosts=["example.org", "www.example.org"])

    assert result.added == 2
    assert result.foreign_skipped == 2
    assert _jar_index(session) == {("mine", "example.org"), ("shared", ".example.org")}
    assert result.domains == (".example.org", "example.org")


def test_host_only_cookie_is_not_returned_for_a_subdomain(tmp_path: Path) -> None:
    """host-only cookie 只覆盖它自己那个主机（RFC 6265）—— 宁少勿多。

    少归还只是"这次没登录上"（会显式报错让人知道），多归还会把账号凭据发给
    无关站点。这条钉住我们**比** stdlib 的"自由匹配"更严的方向。
    """
    session = _session(tmp_path)
    state = {
        "cookies": [
            _record(name="host_only", domain="example.org"),
            _record(name="domain_wide", domain=".example.org"),
        ]
    }

    result = bridge_storage_state(state, session, hosts=["www.example.org"])

    assert result.added == 1
    assert _jar_index(session) == {("domain_wide", ".example.org")}


def test_empty_hosts_list_is_rejected(tmp_path: Path) -> None:
    """★ 枚举为空即报错：没给目标站点时"归还给谁"未定义，不能默认全给。"""
    session = _session(tmp_path)
    with pytest.raises(SessionBridgeError):
        bridge_storage_state({"cookies": [_record()]}, session, hosts=[])


def test_no_matching_cookie_is_rejected(tmp_path: Path) -> None:
    """★ 假成功比失败更危险：一条都没匹配上必须报错，不许静默通过。"""
    session = _session(tmp_path)
    state = {"cookies": [_record(domain=".unrelated.test")]}
    with pytest.raises(SessionBridgeError):
        bridge_storage_state(state, session, hosts=["example.org"])
    assert len(session.jar) == 0


def test_empty_cookie_list_is_rejected(tmp_path: Path) -> None:
    session = _session(tmp_path)
    with pytest.raises(SessionBridgeError):
        bridge_storage_state({"cookies": []}, session, hosts=["example.org"])


def test_storage_state_without_cookies_key_is_rejected(tmp_path: Path) -> None:
    session = _session(tmp_path)
    with pytest.raises(SessionBridgeError):
        bridge_storage_state({"origins": []}, session, hosts=["example.org"])


def test_non_list_cookies_is_rejected(tmp_path: Path) -> None:
    session = _session(tmp_path)
    with pytest.raises(SessionBridgeError):
        bridge_storage_state({"cookies": {"sid": "x"}}, session, hosts=["example.org"])


def test_origins_are_counted_but_not_bridged(tmp_path: Path) -> None:
    """§11.5 已裁定：本期**不**给 HTTP 引擎注入 localStorage token。"""
    session = _session(tmp_path)
    state = {
        "cookies": [_record()],
        "origins": [
            {"origin": "https://example.org", "localStorage": [{"name": "tok", "value": "x"}]}
        ],
    }
    result = bridge_storage_state(state, session, hosts=["example.org"])
    assert result.added == 1
    assert result.origins_ignored == 1


def test_bridge_is_additive_and_keeps_existing_cookies(tmp_path: Path) -> None:
    session = _session(tmp_path)
    existing = playwright_cookie_to_cookie(_record(name="existing", value="old"))
    session.jar.set_cookie(existing)

    bridge_storage_state({"cookies": [_record(name="fresh")]}, session, hosts=["example.org"])

    assert _jar_index(session) == {("existing", "example.org"), ("fresh", "example.org")}


def test_bridge_requires_persistence_enabled(tmp_path: Path) -> None:
    session = CookieSession(None)
    with pytest.raises(SessionPersistenceDisabledError):
        bridge_storage_state({"cookies": [_record()]}, session, hosts=["example.org"])


# ── 4. ★ 凭据不进日志 / 不进返回值 ────────────────────────


def test_bridge_does_not_log_cookie_values(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    session = _session(tmp_path)
    with caplog.at_level(logging.DEBUG):
        result = bridge_storage_state(
            {"cookies": [_record()], "origins": []}, session, hosts=["example.org"]
        )

    assert _SECRET not in caplog.text
    assert _SECRET not in repr(result)
    # 正对照：确实发生了桥接（否则上面两条"因为什么都没做"而恒真）
    assert result.added == 1
    assert next(iter(session.jar)).value == _SECRET


# ── 5. 端到端：落盘走既有加密模块，不新增明文 ─────────────


def test_bridge_from_file_persists_through_encrypted_store(tmp_path: Path) -> None:
    """★ 桥接结果必须走既有 CookieSession（AES-GCM）落盘 —— 不得新增明文存储。"""
    state_path = tmp_path / "work" / "sessions" / "default-abc.playwright.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(
        json.dumps({"cookies": [_record(), _record(domain=".other.test")], "origins": []}),
        encoding="utf-8",
    )
    config = _config(tmp_path, persist=True)
    store = SecretsStore(tmp_path / "s.bin", keyring_api=_FakeKeyring())

    with patch("omnicrawler.fetching.session.SecretsStore", return_value=store):
        result = bridge_from_storage_state_file(config, state_path, hosts=["example.org"])
        cookie_path = config.workspace / "sessions" / "default.cookies"
        raw = cookie_path.read_bytes()
        reopened = CookieSession(cookie_path)

    assert result.added == 1
    assert result.foreign_skipped == 1
    assert raw.startswith(FILE_MAGIC)
    assert _SECRET.encode() not in raw  # 磁盘上没有明文
    restored = next(iter(reopened.jar))
    assert restored.value == _SECRET
    assert restored.has_nonstandard_attr("HttpOnly")  # 逐字段信息在往返后仍保留


def test_bridge_from_file_rejects_missing_snapshot(tmp_path: Path) -> None:
    config = _config(tmp_path)
    with pytest.raises(SessionBridgeError):
        bridge_from_storage_state_file(config, tmp_path / "nope.json", hosts=["example.org"])


def test_bridge_from_file_rejects_corrupt_snapshot(tmp_path: Path) -> None:
    config = _config(tmp_path)
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    with pytest.raises(SessionBridgeError):
        bridge_from_storage_state_file(config, bad, hosts=["example.org"])


def test_bridge_from_file_rejects_non_object_snapshot(tmp_path: Path) -> None:
    config = _config(tmp_path)
    bad = tmp_path / "list.json"
    bad.write_text("[1, 2, 3]", encoding="utf-8")
    with pytest.raises(SessionBridgeError):
        bridge_from_storage_state_file(config, bad, hosts=["example.org"])


def test_bridge_result_shape_is_metadata_only() -> None:
    result = BridgeResult(added=2, foreign_skipped=1, domains=("example.org",), origins_ignored=0)
    assert set(result.__slots__) == {"added", "foreign_skipped", "domains", "origins_ignored"}
