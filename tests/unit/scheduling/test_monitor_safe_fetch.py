import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from omnicrawler.scheduling.change_detector import ChangeDetector, MonitorRule


def test_standalone_monitor_uses_guarded_http_path(tmp_path, monkeypatch):
    from omnicrawler.fetching import page_probe

    calls = []

    def safe_fetch(*args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(body=b"safe page", headers={"content-type": "text/html"}, status=200)

    monkeypatch.setattr(page_probe, "fetch_static_page", safe_fetch, raising=False)
    detector = ChangeDetector(data_dir=tmp_path)
    with patch("urllib.request.urlopen", side_effect=AssertionError("direct transport")):
        assert asyncio.run(detector._fetch_content("https://example.com")) == "safe page"
    assert calls


def test_failed_fetch_is_reported_as_failed_check(tmp_path):
    detector = ChangeDetector(data_dir=tmp_path)
    rule = MonitorRule(url="https://example.com", check_interval=0)
    detector.add_rule(rule)
    with patch.object(detector, "_fetch_content", new=AsyncMock(return_value=None)):
        assert asyncio.run(detector.check_rule(rule.rule_id)) is None
    assert detector.check_report()[rule.rule_id]["status"] == "failed"
    assert rule.last_hash is None


def test_retry_only_builds_broker_with_explicit_notification_scope(tmp_path):
    detector = ChangeDetector(data_dir=tmp_path)
    detector.add_rule(MonitorRule(url="https://example.com", webhook_url="https://hooks.example.org/inbox"))
    broker = detector.network_broker()
    assert broker.credential_domains == ("hooks.example.org",)
    assert broker.credential_purposes == ("notification",)
    assert detector.network_broker() is broker


def test_injected_task_broker_is_not_widened(tmp_path):
    broker = object()
    detector = ChangeDetector(data_dir=tmp_path, egress=broker)
    assert detector.network_broker() is broker
