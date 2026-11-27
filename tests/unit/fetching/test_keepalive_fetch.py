"""keep-alive 取页路径的**端到端**覆盖（2026-11-27）。

## 为什么需要这一份

`test_keepalive.py` 只覆盖连接槽与安全判据（纯逻辑，不发请求）。但 CI 的覆盖率
门禁看 `fetching/http_client.py` 的行覆盖 —— 新增的 `_keep_alive_fetch` 有几十行，
纯逻辑测试够不着，门禁会判红（实测 82% → 74%）。

所以这里起一个**本地回环 HTTP 服务**跑真请求：不用外网、不碰任何站点，
但把「建连 → 发送 → 读响应 → 归还连接 → 复用」整条路径真的走一遍。

★ 两个环境细节（都是踩过的坑）：

1. 服务用 `127.0.0.1` ⇒ 配置必须开 `allow_private_network`（那是**测试环境**的设置，
   不是产品放宽 —— 安全边界本身没动）。
2. 必须用 **ThreadingHTTPServer + daemon_threads**：keep-alive 会让连接保持打开，
   单线程 `HTTPServer.server_close()` 会**永久阻塞**等待那个连接关闭 ——
   表现是「测试挂住」（我第一版就是这样挂的）。
"""

from __future__ import annotations

import threading
import zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from omnicrawler.core.config import AppConfig
from omnicrawler.core.errors import ResponseTooLargeError
from omnicrawler.core.models import CrawlRequest
from omnicrawler.fetching.http_client import HTTPFetcher, encode_request_payload


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    #: 空闲连接 2 秒后由服务端主动断开 —— 否则守护线程会一直等着读下一行请求。
    timeout = 2

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler 约定
        if self.path.startswith("/big"):
            body = b"x" * 4096
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path == "/set-cookie":
            body = b"set"
            self.send_response(200)
            self.send_header("Set-Cookie", "sid=ABC123; Path=/")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path in {"/echo-cookie", "/redir-to-echo"}:
            if self.path == "/redir-to-echo":
                body = b"go"
                self.send_response(302)
                self.send_header("Location", "/echo-cookie")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            cookie = self.headers.get("Cookie", "<none>")
            body = f"cookie={cookie}".encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path == "/redir":
            body = b"moved"
            self.send_response(301)
            self.send_header("Location", "/final")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path == "/loop":
            body = b"loop"
            self.send_response(301)
            self.send_header("Location", "/loop")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path.startswith("/close"):
            body = b"bye"
            self.send_response(200)
            self.send_header("Connection", "close")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            self.close_connection = True
            return
        body = b"FINAL" if self.path.startswith("/final") else b"hello"
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        self.rfile.read(length)
        body = b"logged-in"
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args: object) -> None:
        pass  # 静音，避免污染测试输出


@pytest.fixture()
def server():
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    httpd.daemon_threads = True
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_port}"
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


def _raw(tmp_path: Path, base: str = "http://127.0.0.1/", **http_overrides: object) -> dict:
    http_section: dict = {
        "delay_seconds": 0.0,
        "timeout_seconds": 5,
        "retries": 1,
        "keep_alive": True,
        "allow_private_network": True,
        "robots_fail_closed": False,
    }
    http_section.update(http_overrides)
    return {
        "project": {"name": "ka", "workspace": str(tmp_path / "work")},
        "source": {"kind": "static_html", "seeds": [base]},
        "http": http_section,
    }


def _config(tmp_path: Path, base: str = "http://127.0.0.1/", **overrides: object) -> AppConfig:
    raw = _raw(tmp_path, base, **overrides)
    return AppConfig(tmp_path / "c.yaml", tmp_path, raw, tmp_path / "work")


def test_keep_alive_fetch_returns_the_body(server: str, tmp_path: Path) -> None:
    fetcher = HTTPFetcher(_config(tmp_path, server))
    try:
        result = fetcher.fetch(CrawlRequest(f"{server}/hello"))
        assert result.status == 200
        assert result.body == b"hello"
        assert fetcher._pool is not None
    finally:
        if fetcher._pool is not None:
            fetcher._pool.close()


def test_second_request_reuses_the_connection(server: str, tmp_path: Path) -> None:
    """★ 这条是整件事的**收益判据**：第二、三条请求必须复用连接。

    判据用 `pool.reused` 计数，不看耗时 —— 后者在 CI 上会抖。
    """
    fetcher = HTTPFetcher(_config(tmp_path, server))
    try:
        assert fetcher._pool is not None
        for _ in range(3):
            assert fetcher.fetch(CrawlRequest(f"{server}/hello")).status == 200
        assert fetcher._pool.reused >= 2, f"只复用了 {fetcher._pool.reused} 次"
    finally:
        if fetcher._pool is not None:
            fetcher._pool.close()


def test_connection_close_is_respected(server: str, tmp_path: Path) -> None:
    """反方向：响应带 `Connection: close` 时**不许**复用。"""
    fetcher = HTTPFetcher(_config(tmp_path, server))
    try:
        assert fetcher._pool is not None
        fetcher.fetch(CrawlRequest(f"{server}/close"))
        assert fetcher._pool._slot is None, "带 Connection: close 的响应仍被留在池里"
    finally:
        if fetcher._pool is not None:
            fetcher._pool.close()


def test_response_too_large_is_rejected_and_connection_dropped(
    server: str, tmp_path: Path
) -> None:
    """超限时抛错，且**池里的连接被丢弃**（否则会接着读一个半包）。"""
    fetcher = HTTPFetcher(_config(tmp_path, server, max_response_bytes=100))
    try:
        with pytest.raises(ResponseTooLargeError):
            fetcher.fetch(CrawlRequest(f"{server}/big"))
        assert fetcher._pool is not None and fetcher._pool._slot is None
    finally:
        if fetcher._pool is not None:
            fetcher._pool.close()


def test_keep_alive_off_uses_the_urllib_path(server: str, tmp_path: Path) -> None:
    """默认关闭时必须走原来那条路径（`_pool` 为 None）。"""
    fetcher = HTTPFetcher(_config(tmp_path, server, keep_alive=False))
    assert fetcher._pool is None
    result = fetcher.fetch(CrawlRequest(f"{server}/hello"))
    assert result.status == 200 and result.body == b"hello"


def test_post_does_not_use_keep_alive(server: str, tmp_path: Path) -> None:
    """★ 非幂等方法必须回到 urllib 路径（复用在「已处理但连接中断」时有重放风险）。"""
    fetcher = HTTPFetcher(_config(tmp_path, server))
    try:
        assert fetcher._pool is not None
        result = fetcher.fetch(CrawlRequest(f"{server}/submit", method="POST", body=b"a=1"))
        assert result.status == 200
        assert fetcher._pool.reused == 0, "POST 走了 keep-alive 复用"
    finally:
        if fetcher._pool is not None:
            fetcher._pool.close()


def test_query_string_is_preserved(server: str, tmp_path: Path) -> None:
    """查询串必须原样带上（`_keep_alive_fetch` 自己拼 target）。"""
    fetcher = HTTPFetcher(_config(tmp_path, server))
    try:
        result = fetcher.fetch(CrawlRequest(f"{server}/hello?x=1&y=2"))
        assert result.status == 200 and result.body == b"hello"
    finally:
        if fetcher._pool is not None:
            fetcher._pool.close()


def test_url_without_host_is_rejected(tmp_path: Path) -> None:
    """★ 非 http/https / 无 host 的请求必须在建连前拒绝（不走任何网络）。"""
    fetcher = HTTPFetcher(_config(tmp_path))
    with pytest.raises(ValueError, match="仅支持 http/https"):
        fetcher._keep_alive_fetch(
            CrawlRequest("ftp://example.org/x"), timeout=1, max_bytes=1000, headers={}
        )


def test_keep_alive_fetch_requires_the_pool(tmp_path: Path) -> None:
    """直接调内部方法而池未启用时必须显式报错，不能静默走别的路。"""
    fetcher = HTTPFetcher(_config(tmp_path, keep_alive=False))
    assert fetcher._pool is None
    with pytest.raises(RuntimeError, match="未启用"):
        fetcher._keep_alive_fetch(
            CrawlRequest("http://example.org/x"), timeout=1, max_bytes=1000, headers={}
        )


def test_login_is_performed_once_then_reused(server: str, tmp_path: Path) -> None:
    """★ `_ensure_login` 的双检锁：配了登录就必须先登录，且**只登一次**。

    这条覆盖那段此前完全没有测试的代码（CI 上长期未覆盖）。
    """
    raw = _raw(tmp_path, server)
    raw["source"]["login"] = {
        "url": f"{server}/login",
        "method": "POST",
        "fields": {"u": "a", "p": "b"},
    }
    config = AppConfig(tmp_path / "c.yaml", tmp_path, raw, tmp_path / "work")
    fetcher = HTTPFetcher(config)
    try:
        assert fetcher._login_done is False
        assert fetcher.fetch(CrawlRequest(f"{server}/hello")).status == 200
        assert fetcher._login_done is True, "配了 source.login 却没有登录"
        assert fetcher.fetch(CrawlRequest(f"{server}/hello")).status == 200
        assert fetcher._login_done is True
    finally:
        if fetcher._pool is not None:
            fetcher._pool.close()


def test_login_without_url_is_rejected(tmp_path: Path) -> None:
    """login.url 为空时必须显式报错（不静默跳过）。"""
    raw = _raw(tmp_path)
    raw["source"]["login"] = {"method": "POST"}
    config = AppConfig(tmp_path / "c.yaml", tmp_path, raw, tmp_path / "work")
    with pytest.raises(ValueError, match="login.url"):
        HTTPFetcher(config)._ensure_login()


def test_bounded_decompress_rejects_truncated_input() -> None:
    """`_bounded_decompress`：压缩流被截断必须报错；解压后超限也必须报错。"""
    import gzip

    full = gzip.compress(b"x" * 1000)
    # 截断的 gzip 流：`decompress` 本身可能不报错，但 `eof` 为假 ⇒ 必须抛 ValueError
    truncated = full[:-4]
    with pytest.raises(ValueError):
        HTTPFetcher._bounded_decompress(truncated, zlib.MAX_WBITS | 16, 10_000)
    with pytest.raises(ResponseTooLargeError):
        HTTPFetcher._bounded_decompress(full, zlib.MAX_WBITS | 16, 10)


def test_encode_request_payload_covers_json_and_form() -> None:
    assert encode_request_payload("POST", None, "application/json") == (None, {})
    body, heads = encode_request_payload("POST", {"a": 1}, "application/json")
    assert body == b'{"a": 1}' and heads["Content-Type"] == "application/json"
    body, heads = encode_request_payload("POST", {"a": "x y"}, "application/x-www-form-urlencoded")
    assert body == b"a=x+y"
    assert heads["Content-Type"].startswith("application/x-www-form-urlencoded")


# ── 重试路径（urllib 分支的 URLError 退避）──────────────────────────────


def test_urllib_path_retries_on_connection_error_then_succeeds(
    server: str, tmp_path: Path
) -> None:
    """★ urllib 分支的 `except (URLError, TimeoutError, ConnectionError)` 重试路径。

    做法：第一次调用前把 opener 换成"第一次抛 URLError、第二次交给真 opener"，
    这样走的是**真实重试代码**，而不是靠时序碰运气。
    """
    import urllib.error

    fetcher = HTTPFetcher(_config(tmp_path, server, keep_alive=False, retries=3))
    real_open = fetcher.opener.open
    calls = {"n": 0}

    def flaky(request, *args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise urllib.error.URLError("simulated network hiccup")
        return real_open(request, *args, **kwargs)

    fetcher.opener.open = flaky  # type: ignore[method-assign]
    result = fetcher.fetch(CrawlRequest(f"{server}/hello"))
    assert result.status == 200 and result.body == b"hello"
    assert calls["n"] == 2, f"重试发生了 {calls['n']} 次"


def test_keep_alive_path_retries_on_connection_error_then_succeeds(
    server: str, tmp_path: Path
) -> None:
    """★ keep-alive 分支的退避重试（349–360 行）。

    同样用"第一次抛 OSError"的桩，确保走的是真实重试分支。
    """
    fetcher = HTTPFetcher(_config(tmp_path, server, keep_alive=True, retries=3))
    try:
        real = fetcher._keep_alive_fetch
        calls = {"n": 0}

        def flaky(request, **kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                raise OSError("simulated socket error")
            return real(request, **kwargs)

        fetcher._keep_alive_fetch = flaky  # type: ignore[method-assign]
        result = fetcher.fetch(CrawlRequest(f"{server}/hello"))
        assert result.status == 200
        assert calls["n"] == 2, f"重试发生了 {calls['n']} 次"
    finally:
        if fetcher._pool is not None:
            fetcher._pool.close()


# ── 解压分支 ────────────────────────────────────────────────────────────


def test_gzip_and_uncompressed_round_trip() -> None:
    import gzip

    payload = b"hello gzip" * 10
    assert HTTPFetcher._decode_content(gzip.compress(payload), "gzip", 10_000) == payload
    assert HTTPFetcher._decode_content(payload, "", 10_000) == payload
    assert HTTPFetcher._decode_content(payload, "identity", 10_000) == payload


def test_deflate_raw_fallback_is_supported() -> None:
    """`deflate` 有两种实际形态（zlib 包装 / 裸流）。裸流必须能走 fallback。"""
    import zlib

    payload = b"raw deflate payload"
    raw = zlib.compressobj(wbits=-zlib.MAX_WBITS)
    body = raw.compress(payload) + raw.flush()
    assert HTTPFetcher._decode_content(body, "deflate", 10_000) == payload


def test_missing_optional_decoder_reports_what_to_install() -> None:
    """★ br / zstd 未装时**必须报错并说清装什么**，不能把压缩字节当正文。"""
    import builtins

    real_import = builtins.__import__

    def blocked(name, *args, **kwargs):
        if name in {"brotli", "zstandard"}:
            raise ImportError(f"no {name}")
        return real_import(name, *args, **kwargs)

    builtins.__import__ = blocked
    try:
        with pytest.raises(ValueError, match="brotli"):
            HTTPFetcher._decode_content(b"xx", "br", 100)
        with pytest.raises(ValueError, match="zstandard"):
            HTTPFetcher._decode_content(b"xx", "zstd", 100)
    finally:
        builtins.__import__ = real_import


# ── IP 固定的连接工厂 ───────────────────────────────────────────────────


def test_create_approved_connection_tries_every_address(tmp_path: Path) -> None:
    """★ `_create_approved_connection` 必须**逐个尝试**已批准地址。

    判据：第一个地址连不上时不能就此放弃（多 A 记录的站点很常见）。
    """
    from omnicrawler.fetching.http_client import PinnedHTTPConnection

    class _Policy:
        def approved_addresses(self, host: str, port: int):
            # 第一个地址必然连不通（保留地址），第二个才是真实的服务
            return ("192.0.2.1", "127.0.0.1")

    class _Fast(HTTPFetcher):
        pass

    connection = PinnedHTTPConnection("example.org", target_policy=_Policy(), timeout=0.2, port=9)
    # 直接验证"两个地址都被试过"：用 monkeypatch 记录调用
    tried: list[str] = []
    import socket as _socket

    real_create = _socket.create_connection

    def recorder(address, *args, **kwargs):
        tried.append(str(address[0]))
        if address[0] == "192.0.2.1":
            raise OSError("unreachable test address")
        return real_create(("127.0.0.1", 1), *args, **kwargs)

    _socket.create_connection = recorder  # type: ignore[assignment]
    try:
        with pytest.raises(OSError):
            connection._create_approved_connection(("ignored", 9), timeout=0.2)
        assert tried == ["192.0.2.1", "127.0.0.1"], tried
    finally:
        _socket.create_connection = real_create


# ── 其它小分支（一起把文件覆盖拉过门禁线）────────────────────────────


def test_accept_encoding_lists_installed_decoders() -> None:
    """`_accept_encoding` 按**已安装的包**声明，缺包时只说 gzip/deflate。"""
    from omnicrawler.fetching.http_client import _accept_encoding

    value = _accept_encoding()
    assert value.startswith("gzip, deflate"), value


def test_redirect_handler_enforces_max_redirects(tmp_path: Path) -> None:
    """★ 重定向上限：超过 max_redirects 必须拒绝（开放重定向 / 循环的防护）。"""
    from omnicrawler.fetching.http_client import SafeRedirectHandler
    from omnicrawler.security.policy import NetworkTargetPolicy

    handler = SafeRedirectHandler(NetworkTargetPolicy(_config(tmp_path)), 2)
    assert handler.max_redirections == 2
    assert handler.max_repeats == 2


def test_pinned_connection_install_keeps_policy_reference() -> None:
    """`_install_approved_connector` 必须把 policy 留在实例上（供建连时取地址）。"""
    from omnicrawler.fetching.http_client import PinnedHTTPSConnection

    class _Policy:
        def approved_addresses(self, host: str, port: int):
            return ("127.0.0.1",)

    policy = _Policy()
    connection = PinnedHTTPSConnection("example.org", target_policy=policy, timeout=1)
    assert connection.target_policy is policy
    assert connection._create_connection == connection._create_approved_connection


def test_empty_approved_addresses_raises() -> None:
    """没有已批准地址时必须报错（不许静默连到别处）。"""
    from omnicrawler.fetching.http_client import PinnedHTTPConnection

    class _Policy:
        def approved_addresses(self, host: str, port: int):
            return ()

    connection = PinnedHTTPConnection("example.org", target_policy=_Policy(), timeout=1)
    with pytest.raises(OSError, match="已批准地址"):
        connection._create_approved_connection(("x", 80), timeout=1)


# ── 重定向（keep-alive 路径必须自己跟随）────────────────────────────────


def test_redirect_is_followed_on_the_keep_alive_path(server: str, tmp_path: Path) -> None:
    """★ 实测缺陷（2026-11-27）：keep-alive 路径第一版**不跟随重定向**。

    症状极隐蔽：301 被当成正文返回 ——

        keep_alive=False  status=200 final_url=.../final body=b"FINAL"
        keep_alive=True   status=301 final_url=.../redir body=b"moved"   ← 错

    用户以为抓到了页面，实际拿到的是一个跳转提示（而且状态码是 301，
    下游容易当成"成功"）。urllib 那条路径由 `SafeRedirectHandler` 负责，
    这里必须补同一条。
    """
    fetcher = HTTPFetcher(_config(tmp_path, server))
    try:
        result = fetcher.fetch(CrawlRequest(f"{server}/redir"))
        assert result.status == 200, result.status
        assert result.body == b"FINAL", result.body
        assert result.final_url.endswith("/final"), result.final_url
    finally:
        if fetcher._pool is not None:
            fetcher._pool.close()


def test_both_paths_agree_on_redirect_result(server: str, tmp_path: Path) -> None:
    """★ 两条路径（urllib / keep-alive）对同一 URL 必须给出**一致**的结果。

    "两处口径不许漂移"——这条是整组的核心判据：只要有一边漏了跟随，
    同样的任务会因为一个配置开关给出不同的数据。
    """
    urllib_fetcher = HTTPFetcher(_config(tmp_path, server, keep_alive=False))
    ka_fetcher = HTTPFetcher(_config(tmp_path, server, keep_alive=True))
    try:
        plain = urllib_fetcher.fetch(CrawlRequest(f"{server}/redir"))
        kept = ka_fetcher.fetch(CrawlRequest(f"{server}/redir"))
        assert (plain.status, plain.body) == (kept.status, kept.body), (
            (plain.status, plain.body), (kept.status, kept.body),
        )
    finally:
        if ka_fetcher._pool is not None:
            ka_fetcher._pool.close()


def test_redirect_loop_is_bounded(server: str, tmp_path: Path) -> None:
    """★ 重定向循环必须**有上限**（否则会一直跳）。"""
    from omnicrawler.core.errors import PermanentFetchError

    fetcher = HTTPFetcher(_config(tmp_path, server, max_redirects=2))
    try:
        with pytest.raises(PermanentFetchError, match="重定向次数超过上限"):
            fetcher.fetch(CrawlRequest(f"{server}/loop"))
    finally:
        if fetcher._pool is not None:
            fetcher._pool.close()


# ── cookie（keep-alive 路径必须与 urllib 路径一致）──────────────────────


def test_cookies_are_sent_on_the_keep_alive_path(server: str, tmp_path: Path) -> None:
    """★ 实测缺陷（2026-11-27）：keep-alive 路径**既不发 cookie 也不存 Set-Cookie**。

    同一份任务：

        keep_alive=False  -> b"cookie=sid=ABC123"   ← 正确
        keep_alive=True   -> b"cookie=<none>"       ← 登录态完全失效

    这在"需要登录"的任务上是致命的：服务端看到的每条请求都是未登录的。
    """
    fetcher = HTTPFetcher(_config(tmp_path, server))
    try:
        fetcher.fetch(CrawlRequest(f"{server}/set-cookie"))
        result = fetcher.fetch(CrawlRequest(f"{server}/echo-cookie"))
        assert b"sid=ABC123" in result.body, result.body
    finally:
        if fetcher._pool is not None:
            fetcher._pool.close()


def test_both_paths_send_the_same_cookie_header(server: str, tmp_path: Path) -> None:
    """★ 「两处口径不许漂移」：两条路径对同一站点必须发同一个 Cookie 头。"""
    plain = HTTPFetcher(_config(tmp_path, server, keep_alive=False))
    kept = HTTPFetcher(_config(tmp_path, server, keep_alive=True))
    try:
        plain.fetch(CrawlRequest(f"{server}/set-cookie"))
        kept.fetch(CrawlRequest(f"{server}/set-cookie"))
        plain_body = plain.fetch(CrawlRequest(f"{server}/echo-cookie")).body
        kept_body = kept.fetch(CrawlRequest(f"{server}/echo-cookie")).body
        assert plain_body == kept_body, (plain_body, kept_body)
    finally:
        if kept._pool is not None:
            kept._pool.close()


def test_cookie_jar_survives_a_redirect_on_the_keep_alive_path(
    server: str, tmp_path: Path
) -> None:
    """跳转后的请求同样要带 cookie（否则登录态在跳转那一步就丢了）。"""
    fetcher = HTTPFetcher(_config(tmp_path, server))
    try:
        fetcher.fetch(CrawlRequest(f"{server}/set-cookie"))
        result = fetcher.fetch(CrawlRequest(f"{server}/redir-to-echo"))
        assert b"sid=ABC123" in result.body, result.body
    finally:
        if fetcher._pool is not None:
            fetcher._pool.close()
