"""robots.txt 404 不是故障（2026-11-27）。

## 为什么

实测 `books.toscrape.com` / `quotes.toscrape.com` 的 `robots.txt` 都返回 404。
旧实现把它记成 WARNING：

    robots.txt 加载失败 [http] https://books.toscrape.com: HTTP 404（fail_closed=True，判定=允许）

一句话里同时说了"失败"和"允许" —— 用户只能怀疑是不是哪里坏了。**站点没配 robots
是正常状态**，绝大多数小站都这样。

## 判据要窄

只有「kind == http 且状态码 404/410 且判定为允许」才降级为 INFO。
5xx / DNS / 超时 / 解析失败都是真问题，必须继续占 WARNING ——
把它们一起降级等于把诊断信号关掉。
"""

from __future__ import annotations

import logging

from omnicrawler.security.policy import RobotsPolicy


def _classify(kind: str, detail: str, allowed: bool) -> bool:
    return RobotsPolicy._is_simply_absent(kind, detail, allowed)


def test_404_robots_is_treated_as_absent_not_broken() -> None:
    assert _classify("http", "HTTP 404", True) is True
    assert _classify("http", "HTTP 410", True) is True


def test_server_error_is_still_a_problem() -> None:
    """★ 5xx 是真故障 —— 降级它等于把诊断信号关掉。"""
    assert _classify("http", "HTTP 503", True) is False
    assert _classify("http", "HTTP 500", False) is False


def test_network_and_parse_failures_are_still_problems() -> None:
    assert _classify("dns", "nodename nor servname provided", True) is False
    assert _classify("timeout", "timed out", True) is False
    assert _classify("parse", "parse fail", True) is False
    assert _classify("policy", "未接入 EgressBroker", False) is False


def test_404_but_denied_is_not_treated_as_absent() -> None:
    """反方向：404 却判成拒绝（fail_closed 下其它路径导致）时不能当"正常"。"""
    assert _classify("http", "HTTP 404", False) is False


def test_log_level_actually_changes(caplog) -> None:
    """★ 判据必须作用于**日志级别**，不只是返回布尔 —— 否则优化没落地。"""
    policy = RobotsPolicy.__new__(RobotsPolicy)
    policy.last_error = None
    policy.fail_closed = True
    policy._egress = None
    with caplog.at_level(logging.INFO, logger="omnicrawler.security.policy"):
        policy._record_robots_outcome("https://a.example", "http", "HTTP 404", True)
    assert caplog.records, "没有产生任何日志"
    assert caplog.records[-1].levelno == logging.INFO, caplog.records[-1].levelname

    caplog.clear()
    with caplog.at_level(logging.INFO, logger="omnicrawler.security.policy"):
        policy._record_robots_outcome("https://b.example", "http", "HTTP 503", False)
    assert caplog.records[-1].levelno == logging.WARNING, caplog.records[-1].levelname


def test_last_error_is_still_recorded_for_a_404(caplog) -> None:
    """降级日志级别**不等于**丢掉证据：last_error 仍要写，审计仍要能查到。"""
    policy = RobotsPolicy.__new__(RobotsPolicy)
    policy.last_error = None
    policy.fail_closed = True
    policy._egress = None
    with caplog.at_level(logging.INFO, logger="omnicrawler.security.policy"):
        policy._record_robots_outcome("https://c.example", "http", "HTTP 404", True)
    assert policy.last_error == {
        "kind": "http", "detail": "HTTP 404", "allowed": True, "origin": "https://c.example",
    }
