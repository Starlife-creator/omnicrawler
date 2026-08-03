from __future__ import annotations

import http.client
import json
import socket
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib
from typing import Any

from ..core.config import AppConfig
from ..core.errors import PermanentFetchError, ResponseTooLargeError
from ..core.models import CrawlRequest, FetchResult
from ..core.utils import user_agent
from ..security.egress import EgressBroker, NetworkCapability
from ..security.policy import HostRateLimiter, NetworkTargetPolicy
from .retry import RETRYABLE_STATUS, backoff_seconds, parse_retry_config, retry_after_seconds
from .session import get_cookie_session


class SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    def __init__(
        self,
        target_policy: NetworkTargetPolicy,
        max_redirects: int,
        egress: EgressBroker | None = None,
        capability: NetworkCapability | None = None,
        purpose: str = "redirect",
    ) -> None:
        super().__init__()
        self.target_policy = target_policy
        self.egress = egress
        self.capability = capability
        self.purpose = purpose
        self.max_redirections = max_redirects  # type: ignore[misc]
        self.max_repeats = min(4, max_redirects)  # type: ignore[misc]

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        resolved = urllib.parse.urljoin(req.full_url, newurl)
        if self.egress is not None:
            self.egress.authorize(
                resolved,
                purpose=self.purpose,
                headers={str(key): str(value) for key, value in req.header_items()},
                capability=self.capability,
            )
        else:
            self.target_policy.require(resolved)
        return super().redirect_request(req, fp, code, msg, headers, resolved)


class _ApprovedConnectionMixin:
    """Connect to a policy-approved address literal without resolving twice."""

    target_policy: NetworkTargetPolicy
    host: str
    port: int
    timeout: Any
    source_address: tuple[str, int] | None

    def _install_approved_connector(self, target_policy: NetworkTargetPolicy) -> None:
        self.target_policy = target_policy
        self._create_connection = self._create_approved_connection  # type: ignore[attr-defined]

    def _create_approved_connection(
        self,
        _address: tuple[str, int],
        timeout: Any = None,
        source_address: tuple[str, int] | None = None,
    ) -> socket.socket:
        addresses = self.target_policy.approved_addresses(self.host, self.port)
        last_error: OSError | None = None
        for address in addresses:
            try:
                # address is a numeric literal returned by the approved DNS lookup;
                # socket.create_connection therefore cannot switch to a later DNS answer.
                return socket.create_connection((address, self.port), timeout, source_address)
            except OSError as exc:
                last_error = exc
        if last_error is not None:
            raise last_error
        raise OSError(f"没有可连接的已批准地址: {self.host}:{self.port}")


class PinnedHTTPConnection(_ApprovedConnectionMixin, http.client.HTTPConnection):
    def __init__(self, host: str, *, target_policy: NetworkTargetPolicy, **kwargs: Any) -> None:
        super().__init__(host, **kwargs)
        self._install_approved_connector(target_policy)


class PinnedHTTPSConnection(_ApprovedConnectionMixin, http.client.HTTPSConnection):
    def __init__(self, host: str, *, target_policy: NetworkTargetPolicy, **kwargs: Any) -> None:
        super().__init__(host, **kwargs)
        self._install_approved_connector(target_policy)


class PinnedHTTPHandler(urllib.request.HTTPHandler):
    def __init__(self, target_policy: NetworkTargetPolicy) -> None:
        super().__init__()
        self.target_policy = target_policy

    def http_open(self, req):
        def connection(host: str, **kwargs: Any) -> PinnedHTTPConnection:
            return PinnedHTTPConnection(host, target_policy=self.target_policy, **kwargs)

        return self.do_open(connection, req)


class PinnedHTTPSHandler(urllib.request.HTTPSHandler):
    def __init__(self, target_policy: NetworkTargetPolicy, *, context: ssl.SSLContext) -> None:
        super().__init__(context=context)
        self.target_policy = target_policy
        self.context = context

    def https_open(self, req):
        def connection(host: str, **kwargs: Any) -> PinnedHTTPSConnection:
            return PinnedHTTPSConnection(host, target_policy=self.target_policy, **kwargs)

        return self.do_open(connection, req, context=self.context)


def build_safe_opener(
    config: AppConfig,
    *,
    target_policy: NetworkTargetPolicy | None = None,
    cookie_jar: Any | None = None,
    include_cookies: bool = True,
    egress: EgressBroker | None = None,
    capability: NetworkCapability | None = None,
    purpose: str = "redirect",
) -> urllib.request.OpenerDirector:
    """Build the shared redirect-safe, DNS-pinned urllib opener.

    A configured proxy is treated as a trusted network boundary: the proxy socket
    itself is pinned and validated, while origin DNS resolution is delegated to the
    proxy. With no configured proxy, environment proxy variables are deliberately
    ignored so they cannot silently bypass the direct-connection policy.
    """

    http = config.section("http")
    policy = target_policy or NetworkTargetPolicy(config)
    handlers: list[Any] = []
    if include_cookies and cookie_jar is not None:
        handlers.append(urllib.request.HTTPCookieProcessor(cookie_jar))
    handlers.append(
        SafeRedirectHandler(
            policy,
            int(http.get("max_redirects", 10)),
            egress,
            capability,
            purpose,
        )
    )
    proxy = str(http.get("proxy", "")).strip()
    if proxy:
        policy.require(proxy)
        handlers.append(urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
    else:
        handlers.append(urllib.request.ProxyHandler({}))
    verify = bool(http.get("verify_tls", True))
    context = ssl.create_default_context() if verify else ssl._create_unverified_context()
    handlers.extend((PinnedHTTPHandler(policy), PinnedHTTPSHandler(policy, context=context)))
    return urllib.request.build_opener(*handlers)


class HTTPFetcher:
    def __init__(
        self,
        config: AppConfig,
        limiter: HostRateLimiter | None = None,
        egress: EgressBroker | None = None,
        capability: NetworkCapability | None = None,
        purpose: str = "fetch",
    ) -> None:
        self.config = config
        self.http = config.section("http")
        self.limiter = limiter or HostRateLimiter(float(self.http.get("delay_seconds", 1)))
        self.target_policy = NetworkTargetPolicy(config)
        self.egress = egress or EgressBroker(config, policy=self.target_policy)
        self.capability = capability
        self.purpose = purpose
        self.cookie_session = get_cookie_session(config)
        self.cookie_jar = self.cookie_session.jar
        self.opener = build_safe_opener(
            config,
            target_policy=self.target_policy,
            cookie_jar=self.cookie_jar,
            egress=self.egress,
            capability=self.capability,
            purpose=self.purpose,
        )
        self._login_done = False

    def fetch(self, request: CrawlRequest) -> FetchResult:
        self._ensure_login()
        retries = max(1, int(self.http.get("retries", 3)))
        timeout = float(self.http.get("timeout_seconds", 25))
        max_bytes = int(self.http.get("max_response_bytes", 50_000_000))
        headers = {
            "User-Agent": str(self.http.get("user_agent", user_agent())),
            "Accept": "*/*",
            "Accept-Encoding": "gzip, deflate",
            **{str(k): str(v) for k, v in self.http.get("headers", {}).items()},
            **{str(k): str(v) for k, v in self.config.section("source").get("headers", {}).items()},
            **request.headers,
        }
        last_error: Exception | None = None
        for attempt in range(retries):
            self.limiter.wait(request.url)
            started = time.monotonic()
            raw_request = urllib.request.Request(
                request.url,
                data=request.body,
                headers=headers,
                method=request.method.upper(),
            )
            try:
                with self.egress.request(
                    request.url,
                    purpose=self.purpose,
                    headers=headers,
                    capability=self.capability,
                ):
                    with self.opener.open(raw_request, timeout=timeout) as response:
                        response_headers = {k.lower(): v for k, v in response.headers.items()}
                        declared = response_headers.get("content-length", "")
                        if declared.isdigit() and int(declared) > max_bytes:
                            raise ResponseTooLargeError(f"响应超过大小限制: {declared} > {max_bytes}")
                        wire_body = response.read(max_bytes + 1)
                        if len(wire_body) > max_bytes:
                            raise ResponseTooLargeError(f"响应超过大小限制: > {max_bytes}")
                        self.egress.record_response(len(wire_body), url=response.geturl())
                        self.egress.record_success(response.geturl())
                        body = self._decode_content(
                            wire_body, response_headers.get("content-encoding", ""), max_bytes
                        )
                        self.cookie_session.save()
                        return FetchResult(
                            request=request,
                            final_url=response.geturl(),
                            status=int(getattr(response, "status", 200)),
                            headers=response_headers,
                            body=body,
                            elapsed_seconds=time.monotonic() - started,
                        )
            except urllib.error.HTTPError as exc:
                last_error = exc
                if exc.code == 304:
                    self.egress.record_success(request.url)
                    response_headers = {k.lower(): v for k, v in exc.headers.items()}
                    return FetchResult(
                        request=request,
                        final_url=exc.geturl() or request.url,
                        status=304,
                        headers=response_headers,
                        body=b"",
                        elapsed_seconds=time.monotonic() - started,
                        meta={"not_modified": True},
                    )
                if exc.code not in RETRYABLE_STATUS or attempt + 1 >= retries:
                    raise PermanentFetchError(f"HTTP {exc.code}: {request.url}") from exc
                self.egress.record_failure(request.url, error=f"HTTP {exc.code}")
                wait = retry_after_seconds(dict(exc.headers.items()))
                if wait is None:
                    retry_cfg = parse_retry_config(self.http)
                    wait = backoff_seconds(
                        attempt,
                        base=retry_cfg["base_seconds"],
                        maximum=retry_cfg["max_seconds"],
                        jitter=retry_cfg["jitter"],
                    )
                time.sleep(wait)
            except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
                last_error = exc
                self.egress.record_failure(request.url, error=str(exc))
                if attempt + 1 >= retries:
                    raise
                retry_cfg = parse_retry_config(self.http)
                time.sleep(backoff_seconds(
                    attempt,
                    base=retry_cfg["base_seconds"],
                    maximum=retry_cfg["max_seconds"],
                    jitter=retry_cfg["jitter"],
                ))
        assert last_error is not None
        raise last_error

    def _ensure_login(self) -> None:
        login = self.config.section("source").get("login")
        if self._login_done or not login:
            return
        url = str(login.get("url", ""))
        if not url:
            raise ValueError("source.login.url不能为空")
        method = str(login.get("method", "POST")).upper()
        content_type = str(login.get("content_type", "application/x-www-form-urlencoded"))
        body, payload_headers = encode_request_payload(method, login.get("fields", {}), content_type)
        headers = {
            "User-Agent": str(self.http.get("user_agent", user_agent())),
            **payload_headers,
            **{str(k): str(v) for k, v in login.get("headers", {}).items()},
        }
        self.limiter.wait(url)
        raw = urllib.request.Request(url, data=body, headers=headers, method=method)
        with self.egress.request(url, purpose="login", headers=headers):
            with self.opener.open(raw, timeout=float(self.http.get("timeout_seconds", 25))) as response:
                response_body = response.read(1024 * 1024)
                self.egress.record_response(len(response_body), url=response.geturl())
                self.egress.record_success(response.geturl())
                if int(getattr(response, "status", 200)) >= 400:
                    raise RuntimeError(f"登录失败: HTTP {response.status}")
        self._login_done = True
        self.cookie_session.save()

    @staticmethod
    def _decode_content(body: bytes, encoding: str, max_bytes: int) -> bytes:
        encoding = encoding.lower()
        if encoding not in {"gzip", "deflate"}:
            return body
        window_bits = zlib.MAX_WBITS | 16 if encoding == "gzip" else zlib.MAX_WBITS
        try:
            result = HTTPFetcher._bounded_decompress(body, window_bits, max_bytes)
        except zlib.error:
            if encoding != "deflate":
                raise
            result = HTTPFetcher._bounded_decompress(body, -zlib.MAX_WBITS, max_bytes)
        return result

    @staticmethod
    def _bounded_decompress(body: bytes, window_bits: int, max_bytes: int) -> bytes:
        decompressor = zlib.decompressobj(window_bits)
        result = decompressor.decompress(body, max_bytes + 1)
        if len(result) > max_bytes or decompressor.unconsumed_tail:
            raise ResponseTooLargeError(f"解压后响应超过大小限制: > {max_bytes}")
        result += decompressor.flush(max_bytes + 1 - len(result))
        if len(result) > max_bytes:
            raise ResponseTooLargeError(f"解压后响应超过大小限制: > {max_bytes}")
        if not decompressor.eof:
            try:
                raise ValueError("压缩响应不完整")
            finally:
                del decompressor
        return result


def encode_request_payload(method: str, payload: Any, content_type: str) -> tuple[bytes | None, dict[str, str]]:
    if payload is None:
        return None, {}
    if "json" in content_type:
        return json.dumps(payload, ensure_ascii=False).encode("utf-8"), {"Content-Type": "application/json"}
    return urllib.parse.urlencode(payload, doseq=True).encode("utf-8"), {
        "Content-Type": "application/x-www-form-urlencoded"
    }
