import pytest

from omnicrawler.scheduling.monitor_store import MonitorStore


@pytest.mark.parametrize("target", ["", "webhook:test"])
def test_expired_owner_cannot_acknowledge_or_fail_new_owner(tmp_path, target):
    first, second = MonitorStore(tmp_path), MonitorStore(tmp_path)
    first.save("r", {}, {"event_id": "e"}, targets=[target] if target else [])
    old = first.claim("e", target_id=target)
    table = "target_deliveries" if target else "deliveries"
    with first.connection() as conn:
        conn.execute(f"UPDATE {table} SET lease_until=0 WHERE event_id='e'")
    new = second.claim("e", target_id=target)
    assert old and new and old != new
    first.acknowledge("e", target_id=target, lease_token=old)
    first.fail("e", target_id=target, lease_token=old)
    assert next(row for row in second.report() if row["target_id"] == (target or "desktop"))["status"] == "sending"
    second.acknowledge("e", target_id=target, lease_token=new)
    first.fail("e", target_id=target, lease_token=old)
    assert next(row for row in second.report() if row["target_id"] == (target or "desktop"))["status"] == "submitted"


def test_unrelated_backlog_does_not_hide_active_rule_pending(tmp_path):
    store = MonitorStore(tmp_path)
    with store.connection() as conn:
        conn.executemany("INSERT INTO deliveries(event_id,rule_id,body_json) VALUES(?, 'inactive', '{}')",
                         [(str(index),) for index in range(1001)])
    store.save("active", {}, {"event_id": "active-event"})
    assert [row["event_id"] for row in store.pending({"active"})] == ["active-event"]
