"""A-P2-8：代理池选择 / 粘性绑定 / 失败摘除 / 健康回退纯逻辑回归测试。"""
from __future__ import annotations

import time

from omnicrawler.fetching.proxy_pool import ProxyPool


def test_from_urls_dedupes_and_strips() -> None:
    pool = ProxyPool.from_urls(["http://p1:8080", "  http://p1:8080  ", "", "http://p2:8080"])
    assert [entry.url for entry in pool.proxies] == ["http://p1:8080", "http://p2:8080"]


def test_select_round_robins_across_proxies() -> None:
    pool = ProxyPool.from_urls(["http://p1:8080", "http://p2:8080"])
    first = pool.select("a.example.com")
    second = pool.select("b.example.com")
    assert {first, second} == {"http://p1:8080", "http://p2:8080"}


def test_sticky_binding_reuses_same_proxy_for_domain() -> None:
    pool = ProxyPool.from_urls(["http://p1:8080", "http://p2:8080"])
    first = pool.select("a.example.com")
    assert pool.select("a.example.com") == first
    assert pool.select("a.example.com") == first


def test_failure_removes_proxy_and_unbinds_sticky() -> None:
    pool = ProxyPool.from_urls(["http://p1:8080", "http://p2:8080"], max_failures=2)
    bound = pool.select("a.example.com")  # 粘性绑定到 p1
    other = "http://p2:8080" if bound == "http://p1:8080" else "http://p1:8080"

    pool.report_failure(bound)
    pool.report_failure(bound)  # 达到阈值摘除

    status = {entry["url"]: entry for entry in pool.status()}
    assert status[bound]["healthy"] is False
    # 粘性解除：再次选择落到健康代理
    assert pool.select("a.example.com") == other
    assert pool.select("b.example.com") in {bound, other}


def test_success_recovers_proxy_health() -> None:
    pool = ProxyPool.from_urls(["http://p1:8080"], max_failures=1)
    pool.report_failure("http://p1:8080")
    assert pool.status()[0]["healthy"] is False
    pool.report_success("http://p1:8080")
    status = pool.status()[0]
    assert status["healthy"] is True
    assert status["consecutive_failures"] == 0


def test_all_proxies_removed_returns_none() -> None:
    pool = ProxyPool.from_urls(["http://p1:8080"], max_failures=1, cooldown_seconds=30.0)
    pool.report_failure("http://p1:8080")
    assert pool.select("a.example.com") is None


def test_cooldown_expired_proxy_becomes_eligible_again() -> None:
    pool = ProxyPool.from_urls(["http://p1:8080"], max_failures=1, cooldown_seconds=0.0)
    pool.report_failure("http://p1:8080")
    entry = pool.proxies[0]
    entry.cooldown_until = time.monotonic() - 1.0  # 冷却已过
    assert pool.select("a.example.com") == "http://p1:8080"
