"""Phase 2a H1 安全指标收集器契约测试。"""

from __future__ import annotations

import pytest

from omnicrawler.plugins.security_metrics import METRIC_KEYS, SecurityMetrics

pytestmark = pytest.mark.plugin_contract


def test_all_metrics_initialized_zero() -> None:
    m = SecurityMetrics()
    for key in METRIC_KEYS:
        assert m.value(key) == 0


def test_increment_and_value() -> None:
    m = SecurityMetrics()
    m.increment("isolation_subprocess_total")
    m.increment("isolation_subprocess_total", amount=4)
    m.increment("e_permission_total")
    assert m.value("isolation_subprocess_total") == 5
    assert m.value("e_permission_total") == 1


def test_unknown_metric_rejected() -> None:
    """防指标拼写漂移（H7 语义）：未知键拒绝。"""
    m = SecurityMetrics()
    with pytest.raises(KeyError):
        m.increment("not_a_metric")
    with pytest.raises(KeyError):
        m.value("not_a_metric")


def test_isolation_ratio() -> None:
    m = SecurityMetrics()
    assert m.isolation_ratio() is None  # 无样本
    m.increment("isolation_subprocess_total", 8)
    m.increment("isolation_in_process_total", 2)
    assert m.isolation_ratio() == pytest.approx(0.8)


def test_audit_lag_average() -> None:
    """第 65 轮审计健康增补。"""
    m = SecurityMetrics()
    assert m.audit_lag_ms_avg() is None
    m.increment("audit_lag_ms_total", 100)
    m.increment("audit_lag_count", 2)
    m.increment("audit_lag_ms_total", 50)
    m.increment("audit_lag_count", 2)
    assert m.audit_lag_ms_avg() == pytest.approx(37.5)


def test_snapshot_includes_all_keys_and_uptime() -> None:
    m = SecurityMetrics()
    snap = m.snapshot()
    for key in METRIC_KEYS:
        assert key in snap
    assert "uptime_seconds" in snap
    assert snap["uptime_seconds"] >= 0


def test_thread_safe_increment() -> None:
    """并发累加无丢失。"""
    import threading

    m = SecurityMetrics()

    def worker() -> None:
        for _ in range(1000):
            m.increment("e_resource_total")

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert m.value("e_resource_total") == 4000
