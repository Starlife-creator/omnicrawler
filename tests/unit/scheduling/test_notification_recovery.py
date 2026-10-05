import asyncio

from omnicrawler.scheduling.change_detector import ChangeDetector, MonitorRule


def test_failed_notification_is_recoverable_after_restart(tmp_path, monkeypatch):
    attempts = []
    def failure(event):
        attempts.append(event.to_dict())
        raise RuntimeError("receiver unavailable")
    detector = ChangeDetector(data_dir=tmp_path, on_notify=failure)
    detector.add_rule(MonitorRule(url="https://example.com", rule_id="r", check_interval=0))
    body = {"value": "old"}
    async def fetch(url):
        return body["value"]
    monkeypatch.setattr(detector, "_fetch_content", fetch)
    asyncio.run(detector.check_rule("r"))
    body["value"] = "new"
    event = asyncio.run(detector.check_rule("r"))
    assert event is not None
    delivered = []
    restarted = ChangeDetector(data_dir=tmp_path, on_notify=delivered.append)
    restarted.add_rule(MonitorRule(url="https://example.com", rule_id="r", check_interval=0))
    restarted.retry_notifications(force=True)
    assert len(delivered) == 1
    assert delivered[0].current_content == "new"
    restarted.retry_notifications(force=True)
    assert len(delivered) == 1


def test_cancelled_detector_leaves_notifications_pending(tmp_path, monkeypatch):
    received = []
    detector = ChangeDetector(data_dir=tmp_path, durable_delivery=True)
    detector.add_rule(MonitorRule(url="https://example.com", rule_id="r", check_interval=0))
    body = {"value": "old"}
    async def fetch(url):
        return body["value"]
    monkeypatch.setattr(detector, "_fetch_content", fetch)
    asyncio.run(detector.check_rule("r"))
    body["value"] = "new"
    event = asyncio.run(detector.check_rule("r"))
    detector.cancel()
    detector._on_notify = received.append
    detector.retry_notifications(force=True)
    assert received == []
    recovered = ChangeDetector(data_dir=tmp_path, on_notify=received.append)
    recovered.add_rule(MonitorRule(url="https://example.com", rule_id="r", check_interval=0))
    recovered.retry_notifications(force=True)
    assert received[0].event_id == event.event_id


def test_storage_failure_keeps_previous_baseline_and_can_retry(tmp_path, monkeypatch):
    detector = ChangeDetector(data_dir=tmp_path, durable_delivery=True)
    rule = MonitorRule(url="https://example.com", rule_id="r", check_interval=0)
    detector.add_rule(rule)
    body = {"value": "old"}
    async def fetch(url):
        return body["value"]
    monkeypatch.setattr(detector, "_fetch_content", fetch)
    asyncio.run(detector.check_rule("r"))
    previous = rule.last_hash
    body["value"] = "new"
    original = detector._store.save
    def fail(*args, **kwargs):
        raise OSError("disk full")
    monkeypatch.setattr(detector._store, "save", fail)
    import pytest
    with pytest.raises(OSError, match="disk full"):
        asyncio.run(detector.check_rule("r"))
    assert rule.last_hash == previous
    monkeypatch.setattr(detector._store, "save", original)
    event = asyncio.run(detector.check_rule("r"))
    assert event.previous_hash == previous
    assert detector.delivery_report()[0]["status"] == "pending"


def test_delivery_lease_prevents_concurrent_claim_and_ack_stops_retry(tmp_path):
    from omnicrawler.scheduling.monitor_store import MonitorStore
    first = MonitorStore(tmp_path)
    second = MonitorStore(tmp_path)
    first.save("r", {"last_hash": "new"}, {"event_id": "e", "current_hash": "new"})
    assert first.claim("e")
    assert not second.claim("e")
    first.acknowledge("e")
    assert second.pending({"r"}, force=True) == []
