from __future__ import annotations

import asyncio
import ipaddress
import logging
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

LOGGER = logging.getLogger(__name__)


def classify_robots_error(exc: Exception) -> tuple[str, str]:
    """A-P2-7：把 robots.txt 加载异常拆成可诊断的错误类型。

    返回 ``(kind, detail)``；kind ∈ ``dns / timeout / http / too_large / parse / other``。
    分类只用于诊断日志与审计事件，**不改变** ``fail_closed`` 放行/拒绝语义。
    """
    if isinstance(exc, urllib.error.HTTPError):
        return "http", f"HTTP {exc.code}"
    reason = getattr(exc, "reason", exc)
    if isinstance(exc, socket.gaierror) or isinstance(reason, socket.gaierror):
        return "dns", f"DNS 解析失败: {reason}"
    if isinstance(exc, (socket.timeout, TimeoutError)) or isinstance(reason, (socket.timeout, TimeoutError)):
        return "timeout", f"请求超时: {reason}"
    if isinstance(exc, ResponseTooLargeError):
        return "too_large", str(exc)
    if isinstance(exc, ValueError):
        return "parse", f"robots.txt 解析失败: {exc}"
    return "other", f"{type(exc).__name__}: {exc}"


# ── A-P3-1：无界内存表容量上限（FIFO 淘汰最旧条目），防止海量域名 / 循环键撑大堆。 ──
_DNS_CACHE_MAX_HOSTS = 512
_HOST_RATE_MAX_HOSTS = 1024
_ROBOTS_CACHE_MAX_ORIGINS = 512


def _bound_fifo(table: dict[Any, Any], capacity: int) -> None:
    """在持锁状态下把 ``table`` 压到 ``capacity`` 以内，FIFO 淘汰最旧插入条目。"""
    while len(table) > capacity:
        oldest = next(iter(table), None)
        if oldest is None:
            break
        table.pop(oldest, None)


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
            _bound_fifo(self._cache, _DNS_CACHE_MAX_HOSTS)
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
            _bound_fifo(self._last, _HOST_RATE_MAX_HOSTS)
        remaining = scheduled - now
        if remaining > 0:
            time.sleep(remaining)


class AsyncHostRateLimiter:
    """异步版 host 限速器。

    **当前未接入生产路径**：抓取统一使用同步的 :class:`HostRateLimiter`（单请求与
    批量路径共享同一实例，见 ``tests/unit/fetching/test_rate_limiter_unified.py``），
    异步上下文里经 ``asyncio.to_thread`` 桥接。本类保留以备将来需要「原生异步、
    无线程跳」的限速实现；在接线前不要假定它已被使用。
    """

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
            _bound_fifo(self._last, _HOST_RATE_MAX_HOSTS)
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
        #: A-P2-7：最近一次 robots 加载失败的分类诊断（类型 / 摘要 / 判定结果），供测试与排障读取。
        self.last_error: dict[str, str | bool] | None = None

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
                        # A-P3-1：缓存与锁表同步封顶；缓存淘汰后回收对应 origin 锁。
                        _bound_fifo(self._cache, _ROBOTS_CACHE_MAX_ORIGINS)
                        for stale in [key for key in self._origin_locks if key not in self._cache]:
                            self._origin_locks.pop(stale, None)
                        _bound_fifo(self._origin_locks, _ROBOTS_CACHE_MAX_ORIGINS)
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
            # 4xx 视为站点未配置 robots（允许）；5xx 按 fail_closed 语义收敛。
            allowed = True if 400 <= exc.code < 500 else not self.fail_closed
            self._record_robots_outcome(origin, "http", f"HTTP {exc.code}", allowed)
            return allowed
        except Exception as exc:
            # A-P2-7：DNS / 超时 / 解析 / 其它分别分类落诊断日志与审计事件；
            # fail_closed 语义保持不变（拒绝或放行只由 self.fail_closed 决定）。
            kind, detail = classify_robots_error(exc)
            allowed = not self.fail_closed
            self._record_robots_outcome(origin, kind, detail, allowed)
            return allowed

    def _record_robots_outcome(self, origin: str, kind: str, detail: str, allowed: bool) -> None:
        self.last_error = {"kind": kind, "detail": detail, "allowed": allowed, "origin": origin}
        LOGGER.warning(
            "robots.txt 加载失败 [%s] %s: %s（fail_closed=%s，判定=%s）",
            kind, origin, detail, self.fail_closed, "允许" if allowed else "拒绝",
        )
        if self._egress is not None:
            try:
                self._egress.record_robots_issue(
                    kind, f"{origin}/robots.txt", detail=detail, allowed=allowed,
                )
            except Exception:  # 审计事件写入失败不得反向阻断 robots 判定
                LOGGER.debug("robots 审计事件写入失败", exc_info=True)

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


def _site_key(host: str) -> str:
    """站点标识：归一化 ``www.`` 前缀，使 apex 与 ``www.`` 视为同一站点。"""
    return host[4:] if host.startswith("www.") else host


#: 内置「写操作语义」URL 拦截表（走查 R1.4，2026-09-18）。
#:
#: 为什么必须有：GET 形态的写操作一旦被爬虫跟进，就是对第三方的**真实副作用**。
#: 实测（0.13.0）：某电商测试站的商品卡片带「加入购物车」链接，自动配置把它当普通链接跟进 ⇒
#: 60 个响应里 25 个是 ``?add-to-cart=NNN``，交付的 448 条里 **400 条**来自这些页面、
#: 同一商品最多重复 16 次 —— 既动了对方的购物车，又把结果集污染成重复数据。
#:
#: 取向：**宁可少抓，不可产生副作用**。确实需要时用 ``crawl.allow_write_patterns``（正则）放行；
#: 显式的 ``crawl.deny_patterns`` 仍然更优先、且始终生效。
#: 注：``?s=`` 这类**纯检索**参数不在表内 —— 它是读操作，拦掉会破坏"搜索后翻页"这类真实需求。
_BUILTIN_WRITE_GUARD_PATTERNS: tuple[str, ...] = (
    r"[?&]add[-_]?to[-_]?cart=",
    r"[?&]add[-_]?to[-_]?(wishlist|compare)=",
    r"(^|[/?&])cart(/|$|[?&])",
    r"(^|[/?&])checkout(/|$|[?&])",
    r"(^|[/?&])orders?(/|$|[?&])",
    r"(^|[/?&])(logout|signout|sign-out)(/|$|[?&])",
    r"(^|[/?&])(delete|destroy|unsubscribe)(/|$|[?&])",
    r"[?&](delete|remove|destroy|unsubscribe)=",
    r"[?&](action|do)=(delete|remove|update|edit|create|publish)",
    r"(^|[/?&])wp-admin(/|$|[?&])",
    r"(^|[/?&])wp-login\.php",
)
_BUILTIN_WRITE_GUARDS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(pattern, re.IGNORECASE) for pattern in _BUILTIN_WRITE_GUARD_PATTERNS
)
#: 内置保护的拒绝原因（会被写进错误/审计，便于用户判断"为什么这个 URL 没被抓"）
WRITE_GUARD_REASON = "内置写操作保护：URL 含状态变更语义（加入购物车/下单/登出/删除等）"


@dataclass(slots=True)
class ScopePolicy:
    config: AppConfig
    _network: NetworkTargetPolicy = field(init=False, repr=False)
    # A-P3-2：用户正则在策略对象初始化时一次性预编译缓存，判定路径不再逐请求编译。
    _deny_patterns: tuple[re.Pattern[str], ...] = field(init=False, repr=False)
    _allow_patterns: tuple[re.Pattern[str], ...] = field(init=False, repr=False)
    _write_guard_patterns: tuple[re.Pattern[str], ...] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._network = NetworkTargetPolicy(self.config)
        crawl = self.config.section("crawl")
        self._deny_patterns = tuple(re.compile(str(item)) for item in crawl.get("deny_patterns", []) or [])
        self._allow_patterns = tuple(re.compile(str(item)) for item in crawl.get("allow_patterns", []) or [])
        self._write_guard_patterns = tuple(
            re.compile(str(item), re.IGNORECASE)
            for item in (crawl.get("allow_write_patterns", []) or [])
        )

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
            # apex ⇄ www 的 301 是最常见的重定向形式（scrapethissite.com →
            # www.scrapethissite.com）。严格比较主机名会把这种重定向判成
            # "超出种子站点"，整轮 run 因"无页面交付"失败——归一化 www. 前缀。
            if _site_key(host) != _site_key(root_host):
                return False, "超出种子站点"
        for pattern in self._deny_patterns:
            if pattern.search(url):
                return False, "命中deny_patterns"
        if self._allow_patterns and not any(pattern.search(url) for pattern in self._allow_patterns):
            return False, "未命中allow_patterns"
        # 内置写操作保护（走查 R1.4）：默认拒绝，显式放行表可覆盖。
        if not self._write_guard_exempt(url):
            for guard in _BUILTIN_WRITE_GUARDS:
                if guard.search(url):
                    return False, WRITE_GUARD_REASON
        return True, ""

    def _write_guard_exempt(self, url: str) -> bool:
        """``crawl.allow_write_patterns`` 里任一正则命中即视为显式放行（预编译缓存）。"""
        return any(pattern.search(url) for pattern in self._write_guard_patterns)
