import json

from omnicrawler.core.models import ExtractedRecord
from omnicrawler.quality.notification_rules import validate_policy
from omnicrawler.state import StateStore


def observe(state, data, policy, task="deadline"):
    run = state.start_run("business", "task.yaml", task_id=task)
    state.track_semantic_changes(run, [ExtractedRecord("https://example.org/item", "item", {"id": 1, **data})],
        identity_fields=("id",), notification={"rule_id": "rule", "target_id": "webhook:test", "config_sha256": "a" * 64, "policy": policy})
    return run


def events(state, status="pending"):
    return [json.loads(row["body_json"]) for row in state.rows("SELECT body_json FROM target_deliveries WHERE status=? ORDER BY rowid", (status,))]


def test_deadline_direction_enters_actual_notification_and_filters(tmp_path):
    policy = {"semantic_fields": {"closing_time": {"kind": "deadline"}}, "event_types": ["advanced"]}
    with StateStore(tmp_path / "state.sqlite3") as state:
        observe(state, {"closing_time": "2026-10-10"}, policy)
        observe(state, {"closing_time": "2026-10-12"}, policy)
        assert not events(state)
        assert events(state, "suppressed")[0]["suppression_reason"] == "business_event_not_matched"
        observe(state, {"closing_time": "2026-10-09"}, policy)
        business = events(state)[0]["details"]["business_events"][0]
        assert business["event_type"] == "advanced" and business["field"] == "closing_time"
        assert business["before"] == "2026-10-10" and business["confidence"] is None
        assert len(state.rows("SELECT * FROM entity_observations")) == 3


def test_timezones_and_explicit_money_units_do_not_create_false_events(tmp_path):
    policy = {"semantic_fields": {"due": {"kind": "deadline"}, "total": {"kind": "amount", "unit_field": "unit", "currency_field": "currency"}},
              "event_types": ["advanced", "postponed", "amount_changed"]}
    with StateStore(tmp_path / "state.sqlite3") as state:
        observe(state, {"due": "2026-10-10T08:00:00+08:00", "total": 100, "unit": "万元", "currency": "CNY"}, policy)
        observe(state, {"due": "2026-10-10T00:00:00Z", "total": 1000000, "unit": "元", "currency": "CNY"}, policy)
        assert not events(state)
        assert {event["event_type"] for event in events(state, "suppressed")[0]["details"]["business_events"]} == {"unchanged"}


def test_missing_field_is_distinct_from_null_and_does_not_claim_withdrawal(tmp_path):
    policy = {"semantic_fields": {"life": {"kind": "status"}}, "event_types": ["withdrawn", "reappeared"]}
    with StateStore(tmp_path / "state.sqlite3") as state:
        observe(state, {"life": "withdrawn"}, policy)
        observe(state, {"life": None}, policy)
        observe(state, {}, policy)
        assert not events(state)
        suppressed = events(state, "suppressed")
        assert suppressed[0]["details"]["business_events"][0]["event_type"] == "field_changed"
        assert suppressed[1]["details"]["business_events"][0]["event_type"] == "field_removed"
        observe(state, {"life": "active"}, policy)
        assert events(state)[0]["details"]["business_events"][0]["event_type"] == "reappeared"


def test_semantics_preserve_confirmation_and_task_isolation(tmp_path):
    policy = {"semantic_fields": {"due": {"kind": "deadline"}}, "event_types": ["postponed"], "confirmations": 2}
    path = tmp_path / "state.sqlite3"
    with StateStore(path) as state:
        observe(state, {"due": "2026-10-10"}, policy)
        observe(state, {"due": "2026-10-12"}, policy)
        assert not events(state)
        observe(state, {"due": "2026-10-12"}, policy, task="other")
        assert not events(state)
    with StateStore(path) as state:
        observe(state, {"due": "2026-10-12"}, policy)
        assert len(events(state)) == 1


def test_invalid_semantic_policies_fail_closed():
    for policy in [{"event_types": ["advanced"]}, {"semantic_fields": {"due": {"kind": []}}},
                   {"semantic_fields": {"due": {"kind": "deadline", "unit_field": "unit"}}},
                   {"semantic_fields": {"due": {"kind": "deadline"}}, "event_types": ["unchanged"]}]:
        assert validate_policy(policy)


def test_unknown_status_does_not_prove_reappearance(tmp_path):
    policy = {"semantic_fields": {"life": {"kind": "status"}}, "event_types": ["reappeared"]}
    with StateStore(tmp_path / "state.sqlite3") as state:
        observe(state, {"life": "withdrawn"}, policy)
        observe(state, {"life": "unknown"}, policy)
        assert not events(state)
        assert events(state, "suppressed")[0]["details"]["business_events"][0]["event_type"] == "status_changed"
