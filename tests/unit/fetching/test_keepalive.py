"""keep-alive 连接复用（2026-11-27）。

## 为什么值得做

同一个站点连续取 10 页：每页新建连接 **14.0 s**，复用一条 keep-alive **3.7 s**（3.7x）。
成本全在 DNS + TCP + TLS 三次握手。

## 这些用例守什么

★ 这是本项目**改动面最大**的一处性能优化（绕开了 urllib 的 `do_open`），所以判据分两类：

**正确性**（连接槽的行为）—— 复用判据三条同时成立，缺一个就是安全或数据问题；
**安全边界**（不因为"自己发请求"就把边界丢掉）—— 这才是真正的风险点。
"""

from __future__ import annotations

import http.client
import ssl

from omnicrawler.fetching.keepalive import (
    KeepAlivePool,
    make_connection,
    response_is_reusable,
)


class _FakeConn(http.client.HTTPConnection):
    """不真连的假连接（只为验证槽的存取语义）。"""

    def __init__(self, name: str = "c") -> None:
        super().__init__("example.org", timeout=1)
        self.name = name
        self.closed = False

    def close(self) -> None:
        self.closed = True


# ── 连接槽的正确性 ───────────────────────────────────────────────────────


def test_take_returns_the_connection_that_was_given_back() -> None:
    pool = KeepAlivePool()
    key = pool.key_for("https", "example.org", 443, verify=True)
    connection = _FakeConn()
    pool.give_back(key, connection)
    assert pool.take(key) is connection
    assert pool.take(key) is None, "同一条连接被取走了两次"


def test_different_endpoint_never_reuses() -> None:
    """★ 判据 1：不同 (scheme, host, port) 不许复用 —— 那会把请求发到错的站点。"""
    pool = KeepAlivePool()
    https_key = pool.key_for("https", "example.org", 443, verify=True)
    http_key = pool.key_for("http", "example.org", 80, verify=True)
    other_host = pool.key_for("https", "other.example", 443, verify=True)
    other_port = pool.key_for("https", "example.org", 8443, verify=True)
    pool.give_back(https_key, _FakeConn())
    assert pool.take(http_key) is None
    assert pool.take(other_host) is None
    assert pool.take(other_port) is None


def test_different_tls_settings_never_reuse() -> None:
    """★ 判据 2：换 TLS 校验设置不许复用 —— 复用等于把"关闭校验"的洞留在后面。"""
    pool = KeepAlivePool()
    verified = pool.key_for("https", "example.org", 443, verify=True)
    insecure = pool.key_for("https", "example.org", 443, verify=False)
    assert verified != insecure
    pool.give_back(verified, _FakeConn())
    assert pool.take(insecure) is None


def test_giving_back_a_new_connection_closes_the_old_one() -> None:
    """只留一条：替换时旧连接必须被关（否则 socket 泄漏）。"""
    pool = KeepAlivePool()
    key = pool.key_for("https", "example.org", 443, verify=True)
    first = _FakeConn("first")
    second = _FakeConn("second")
    pool.give_back(key, first)
    pool.give_back(key, second)
    assert first.closed, "被替换掉的旧连接没有关闭"
    assert pool.take(key) is second


def test_discard_closes_and_clears() -> None:
    pool = KeepAlivePool()
    key = pool.key_for("https", "example.org", 443, verify=True)
    connection = _FakeConn()
    pool.give_back(key, connection)
    pool.discard()
    assert connection.closed
    assert pool.take(key) is None


# ── 响应是否允许复用 ─────────────────────────────────────────────────────


def test_connection_close_forbids_reuse() -> None:
    assert response_is_reusable({"connection": "close"}) is False
    assert response_is_reusable({"connection": "Close"}) is False
    assert response_is_reusable({"connection": "keep-alive, close"}) is False


def test_normal_response_allows_reuse() -> None:
    assert response_is_reusable({}) is True
    assert response_is_reusable({"connection": "keep-alive"}) is True


def test_bodyless_status_allows_reuse() -> None:
    """204/304 没有响应体，连接本来就可复用。"""
    assert response_is_reusable({"connection": "close"}, status=204) is True
    assert response_is_reusable({"connection": "close"}, status=304) is True


# ── 安全边界（这才是真正的风险点）──────────────────────────────────────


def test_pinned_factory_uses_only_approved_addresses() -> None:
    """★ 连接必须固定在 `approved_addresses()` 返回的 IP 上，不许在传输层重新解析。

    这条守的是 DNS rebinding：判据是「构造连接时向 policy 要地址」，
    而不是「连接真的能建起来」（后者在有网时恒真）。
    """
    from omnicrawler.fetching.http_client import _pinned_factory

    class _Policy:
        calls: list = []

        def approved_addresses(self, host: str, port: int):
            self.calls.append((host, port))
            return ("203.0.113.7",)

    policy = _Policy()
    context = ssl.create_default_context()
    factory = _pinned_factory(policy, verify=True, context=context)
    connection = factory("example.org", port=443, timeout=5)
    assert policy.calls == [("example.org", 443)], policy.calls
    assert connection._pinned == "203.0.113.7"
    assert connection.host == "example.org", "Host/SNI 必须是原域名"


def test_pinned_factory_refuses_when_no_approved_address() -> None:
    """没有已批准地址时必须报错，**不许**退化成"让系统去解析"。"""
    from omnicrawler.fetching.http_client import _pinned_factory

    class _Policy:
        def approved_addresses(self, host: str, port: int):
            return ()

    factory = _pinned_factory(_Policy(), verify=True, context=ssl.create_default_context())
    try:
        factory("example.org", port=443, timeout=5)
    except OSError as exc:
        assert "已批准地址" in str(exc), exc
    else:
        raise AssertionError("没有已批准地址却建了连接")


def test_keep_alive_is_off_by_default() -> None:
    """★ 默认关闭：不配置时行为与今天**完全一致**（这是能安全上线的前提）。"""
    from omnicrawler.core.config_defaults import DEFAULTS

    assert DEFAULTS["http"]["keep_alive"] is False


def test_make_connection_picks_the_right_scheme_class() -> None:
    context = ssl.create_default_context()
    https_conn = make_connection("https", "example.org", 443, timeout=5, context=context)
    http_conn = make_connection("http", "example.org", 80, timeout=5)
    assert isinstance(https_conn, http.client.HTTPSConnection)
    assert isinstance(http_conn, http.client.HTTPConnection)
    assert not isinstance(http_conn, http.client.HTTPSConnection)
