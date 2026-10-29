"""A-P3-1：无界内存表 FIFO / TTL 上限与淘汰契约回归测试。

覆盖：限速器主机表、robots 缓存与 origin 锁表、DNS 解析缓存、熔断器主机表；
容量常量在测试里调小，锁定「超出即淘汰最旧条目」的行为，不绑定真实数值。
"""
from __future__ import annotations

import socket
from pathlib import Path
from unittest.mock import patch

import pytest

from omnicrawler.core.config import DEFAULTS, AppConfig
from omnicrawler.security import policy as policy_module
from omnicrawler.security.egress import EgressBroker
from omnicrawler.security.policy import (
    HostRateLimiter,
    NetworkTargetPolicy,
    RobotsPolicy,
)


class _Policy:
    def require(self, url: str) -> None:
        return None

    def approved_addresses(self, host: str, port: int) -> tuple[str, ...]:
        return (f"198.51.100.{host}",)


def _config(tmp_path: Path, http: dict | None = None) -> AppConfig:
    raw = {
        "project": {"name": "bounds", "workspace": "work"},
        "source": {"kind": "static_html", "seeds": ["https://example.com/start"]},
        "crawl": dict(DEFAULTS["crawl"]),
        "http": {**DEFAULTS["http"], **(http or {}), "user_agent": "test@example.com"},
        "egress": dict(DEFAULTS["egress"]),
    }
    return AppConfig(tmp_path / "task.yaml", tmp_path, raw, tmp_path / "work")


# ── HostRateLimiter 主机表 ──────────────────────────────────────────────

def test_host_rate_limiter_evicts_oldest_host(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(policy_module, "_HOST_RATE_MAX_HOSTS", 5)
    limiter = HostRateLimiter(delay_seconds=0)
    for index in range(8):
        limiter.wait(f"https://host-{index}.example.com/")
    assert len(limiter._last) == 5
    # FIFO：前 3 个最旧主机已被淘汰，新主机保留。
    assert "host-0.example.com" not in limiter._last
    assert "host-7.example.com" in limiter._last


# ── NetworkTargetPolicy DNS 缓存 ───────────────────────────────────────

def test_dns_cache_evicts_oldest_host(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(policy_module, "_DNS_CACHE_MAX_HOSTS", 5)
    target = NetworkTargetPolicy(_config(tmp_path))
    fake_addrinfo = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("203.0.113.9", 0))]
    with patch("omnicrawler.security.policy.socket.getaddrinfo", return_value=fake_addrinfo):
        for index in range(8):
            target.addresses(f"dns-{index}.example.com", 443)
    assert len(target._cache) == 5
    assert ("dns-0.example.com", 443) not in target._cache
    assert ("dns-7.example.com", 443) in target._cache


# ── RobotsPolicy 缓存与 origin 锁表 ─────────────────────────────────────

class _OkResponse:
    def read(self, _limit: int = -1) -> bytes:
        return b"User-agent: *\nDisallow: /private/\n"

    def __enter__(self) -> _OkResponse:
        return self

    def __exit__(self, *_exc: object) -> None:
        return None


class _OkOpener:
    def open(self, _request: object, timeout: float | None = None) -> _OkResponse:
        return _OkResponse()


def test_robots_cache_and_locks_evict_oldest_origin(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(policy_module, "_ROBOTS_CACHE_MAX_ORIGINS", 5)
    robots = RobotsPolicy(_config(tmp_path), opener=_OkOpener())
    for index in range(8):
        robots.allowed(f"https://robots-{index}.example.com/page")
    assert len(robots._cache) == 5
    assert len(robots._origin_locks) == 5
    assert "https://robots-0.example.com" not in robots._cache
    assert "https://robots-7.example.com" in robots._cache
    # 缓存淘汰后对应 origin 锁一并回收，不留孤儿锁。
    assert set(robots._origin_locks) == set(robots._cache)


# ── EgressBroker 熔断器主机表 ─────────────────────────────────────────

def test_circuit_table_evicts_oldest_host(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import omnicrawler.security.egress as egress_module

    monkeypatch.setattr(egress_module, "_CIRCUIT_MAX_HOSTS", 5)
    broker = EgressBroker(_config(tmp_path), policy=_Policy())
    for index in range(8):
        broker.record_failure(f"https://circuit-{index}.example.com/", retryable=True)
    assert len(broker._circuits) == 5
    assert "circuit-0.example.com" not in broker._circuits
    assert "circuit-7.example.com" in broker._circuits
