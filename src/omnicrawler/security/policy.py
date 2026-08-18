from __future__ import annotations

import asyncio
import ipaddress
import re
import socket
import threading
import time
import urllib.error
import urllib.request
import urllib.robotparser
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

from ..core.config import AppConfig
from ..core.errors import PolicyBlockedError, ResponseTooLargeError


def is_private_target(url: str) -> bool:
    host = (urlsplit(url).hostname or "").strip("[]").lower()
    if host in {"localhost", "localhost.localdomain"} or host.endswith(".localhost"):
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return bool(
        address.is_private or address.is_loopback or address.is_link_local
        or address.is_reserved or address.is_multicast or address.is_unspecified
    )


def is_disallowed_address(value: str) -> bool:
    try:
        address = ipaddress.ip_address(value.split("%", 1)[0])
    except ValueError:
        return True
    return bool(
        address.is_private or address.is_loopback or address.is_link_local
        or address.is_reserved or address.is_multicast or address.is_unspecified
    )


class NetworkTargetPolicy:
    """Validates literal and DNS-resolved targets before a network hop."""

    def __init__(self, config: AppConfig) -> None:
        http = config.section("http")
        self.allow_private = bool(http.get("allow_private_network", False))
        self.resolve_dns = bool(http.get("resolve_dns", True))
        self.fail_closed = bool(http.get("dns_fail_closed", True))
        self.ttl = max(0.0, float(http.get("dns_cache_ttl_seconds", 60)))
        self._cache: dict[tuple[str, int], tuple[float, tuple[str, ...]]] = {}
        self._lock = threading.Lock()

    def addresses(self, host: str, port: int) -> tuple[str, ...]:
        key = (host.casefold(), port)
        now = time.monotonic()
        with self._lock:
            cached = self._cache.get(key)
            if cached and cached[0] > now:
                return cached[1]
        try:
            values = tuple(dict.fromkeys(
                str(item[4][0]) for item in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
            ))
        except socket.gaierror as exc:
            if self.fail_closed:
                raise PolicyBlockedError(f"DNS解析失败，已按安全策略阻止访问: {host}: {exc}") from exc
            return ()
        with self._lock:
            self._cache[key] = (now + self.ttl, values)
        return values

    def approved_addresses(self, host: str, port: int) -> tuple[str, ...]:
        """Resolve once, reject unsafe answers, and return addresses safe to connect to.

        Callers must establish the socket with one of the returned address literals.
        Resolving the hostname again in the transport would reintroduce a DNS-rebinding
        time-of-check/time-of-use gap.
        """

        if not self.resolve_dns:
            return (host,)
        values = self.addresses(host, port)
        if not values:
            if self.fail_closed:
                raise PolicyBlockedError(f"DNS解析未返回可用地址: {host}")
            return (host,)
        if not self.allow_private:
            blocked = [value for value in values if is_disallowed_address(value)]
            if blocked:
                raise PolicyBlockedError(f"域名解析到内网或保留地址: {', '.join(blocked)}")
        return values

    def allowed(self, url: str) -> tuple[bool, str]:
        parts = urlsplit(url)
        if parts.scheme not in {"http", "https"} or not parts.hostname:
            return False, "仅允许HTTP/HTTPS地址"
        if not self.allow_private and is_private_target(url):
            return False, "默认禁止访问本机、内网或保留地址"
        port = parts.port or (443 if parts.scheme == "https" else 80)
        try:
            self.approved_addresses(parts.hostname, port)
        except PolicyBlockedError as exc:
            return False, str(exc)
        return True, ""

    def require(self, url: str) -> None:
        allowed, reason = self.allowed(url)
        if not allowed:
            raise PolicyBlockedError(reason)


class HostRateLimiter:
    def __init__(self, delay_seconds: float) -> None:
        self.delay = max(0.0, delay_seconds)
        self._last: dict[str, float] = {}
        self._lock = threading.Lock()

    def wait(self, url: str) -> None:
        host = (urlsplit(url).hostname or "").lower()
        with self._lock:
            now = time.monotonic()
            scheduled = max(now, self._last.get(host, 0.0) + self.delay)
            # Reserve this host's slot while holding the lock, then sleep outside it.
            # Otherwise one delayed host serializes requests to every other host.
            self._last[host] = scheduled
        remaining = scheduled - now
        if remaining > 0:
            time.sleep(remaining)


class AsyncHostRateLimiter:
    def __init__(self, delay_seconds: float) -> None:
        self.delay = max(0.0, delay_seconds)
        self._last: dict[str, float] = {}
        self._lock = asyncio.Lock()

    async def wait(self, url: str) -> None:
        host = (urlsplit(url).hostname or "").lower()
        async with self._lock:
            now = time.monotonic()
            scheduled = max(now, self._last.get(host, 0.0) + self.delay)
            self._last[host] = scheduled
        remaining = scheduled - now
        if remaining > 0:
            await asyncio.sleep(remaining)


@dataclass(slots=True)
class _RobotsCacheEntry:
    expires_at: float
    policy: urllib.robotparser.RobotFileParser | bool


class RobotsPolicy:
    def __init__(
        self,
        config: AppConfig,
        *,
        opener: Any | None = None,
        egress: Any | None = None,
    ) -> None:
        self.config = config
        http = config.section("http")
        self.enabled = bool(http.get("respect_robots", True))
        self.fail_closed = bool(http.get("robots_fail_closed", True))
        self.user_agent = str(http.get("user_agent", "OmniCrawler"))
        self.timeout = float(http.get("timeout_seconds", 25))
        self.ttl = max(0.0, float(http.get("robots_cache_ttl_seconds", 3600)))
        self.max_bytes = max(1024, int(http.get("robots_max_bytes", 2_000_000)))
        self._cache: dict[str, _RobotsCacheEntry] = {}
        self._origin_locks: dict[str, threading.Lock] = {}
        self._lock = threading.Lock()
        self._opener = opener
        self._egress = egress
        self._opener_lock = threading.Lock()

    def allowed(self, url: str) -> bool:
        if not self.enabled:
            return True
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        now = time.monotonic()
        with self._lock:
            entry = self._cache.get(origin)
            origin_lock = self._origin_locks.setdefault(origin, threading.Lock())
        if entry is None or entry.expires_at <= now:
            with origin_lock:
                with self._lock:
                    entry = self._cache.get(origin)
                if entry is not None and entry.expires_at > time.monotonic():
                    policy = entry.policy
                else:
                    policy = self._load(origin)
                    with self._lock:
                        self._cache[origin] = _RobotsCacheEntry(time.monotonic() + self.ttl, policy)
        else:
            policy = entry.policy
        if isinstance(policy, bool):
            return policy
        return policy.can_fetch(self.user_agent, url)

    def _load(self, origin: str) -> urllib.robotparser.RobotFileParser | bool:
        parser = urllib.robotparser.RobotFileParser(f"{origin}/robots.txt")
        parser.set_url(f"{origin}/robots.txt")
        try:
            opener = self._safe_opener()
            request = urllib.request.Request(
                f"{origin}/robots.txt",
                headers={"User-Agent": self.user_agent, "Accept": "text/plain,*/*;q=0.1"},
            )
            if self._egress is None:
                with opener.open(request, timeout=self.timeout) as response:
                    body = response.read(self.max_bytes + 1)
            else:
                with self._egress.request(
                    request.full_url,
                    purpose="robots",
                    headers={str(key): str(value) for key, value in request.header_items()},
                ):
                    with opener.open(request, timeout=self.timeout) as response:
                        body = response.read(self.max_bytes + 1)
                    self._egress.record_response(len(body), url=request.full_url)
            if len(body) > self.max_bytes:
                raise ResponseTooLargeError("robots.txt超过配置的大小上限")
            parser.parse(body.decode("utf-8", errors="replace").splitlines())
            return parser
        except urllib.error.HTTPError as exc:
            return True if 400 <= exc.code < 500 else not self.fail_closed
        except Exception:
            return not self.fail_closed

    def _safe_opener(self) -> Any:
        if self._opener is not None:
            return self._opener
        with self._opener_lock:
            if self._opener is None:
                # Local import avoids a module cycle: http_client depends on this
                # module for NetworkTargetPolicy.
                from ..fetching.http_client import build_safe_opener
                from .egress import EgressBroker

                if self._egress is None:
                    self._egress = EgressBroker(self.config)
                self._opener = build_safe_opener(
                    self.config,
                    target_policy=self._egress.policy,
                    include_cookies=False,
                    egress=self._egress,
                )
        return self._opener


@dataclass(slots=True)
class ScopePolicy:
    config: AppConfig
    _network: NetworkTargetPolicy = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._network = NetworkTargetPolicy(self.config)

    def allowed(self, url: str, root_url: str | None = None) -> tuple[bool, str]:
        parts = urlsplit(url)
        network_allowed, reason = self._network.allowed(url)
        if not network_allowed:
            return False, reason
        crawl = self.config.section("crawl")
        host = (parts.hostname or "").lower()
        allow_domains = [str(item).lower() for item in crawl.get("allow_domains", [])]
        if allow_domains and not any(host == item or host.endswith("." + item) for item in allow_domains):
            return False, "域名不在allow_domains中"
        if root_url and crawl.get("same_host", True):
            root_host = (urlsplit(root_url).hostname or "").lower()
            if host != root_host:
                return False, "超出种子站点"
        for pattern in crawl.get("deny_patterns", []):
            if re.search(str(pattern), url):
                return False, "命中deny_patterns"
        allow_patterns = crawl.get("allow_patterns", [])
        if allow_patterns and not any(re.search(str(pattern), url) for pattern in allow_patterns):
            return False, "未命中allow_patterns"
        return True, ""
