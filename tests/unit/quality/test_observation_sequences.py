from omnicrawler.core.models import CrawlRequest, ExtractedRecord
from omnicrawler.state import StateStore

URL = "https://example.com/item"


def cycle(state, project, price):
    run = state.start_run(project, "config.yaml")
    records = [ExtractedRecord(URL, "product", {"sku": "A", "price": price})]
    changes = state.track_semantic_changes(run, records, identity_fields=("sku",))
    state.save_records(run, CrawlRequest(URL), records)
    state.finish_run(run, "succeeded", {})
    return changes


def test_restored_old_value_becomes_current_baseline(tmp_path):
    with StateStore(tmp_path / "state.sqlite3") as state:
        changes = [cycle(state, "monitor", price) for price in (100, 80, 100, 100, 80, 80)]
    assert [len(items) for items in changes] == [1, 1, 1, 0, 1, 0]
    assert changes[4][0]["before"]["price"] == 100


def test_same_source_in_another_project_has_its_own_first_baseline(tmp_path):
    with StateStore(tmp_path / "state.sqlite3") as state:
        cycle(state, "task-A", 100)
        changes = cycle(state, "task-B", 80)
    assert changes[0]["change_type"] == "added"
    assert changes[0]["baseline"] is True


def test_explicit_task_identity_survives_rename_and_isolates_same_names(tmp_path):
    with StateStore(tmp_path / "state.sqlite3") as state:
        first = state.start_run("name", "one.yaml", task_id="stable-A")
        state.track_semantic_changes(first, [ExtractedRecord(URL, "product", {"sku": "A", "price": 100})], identity_fields=("sku",))
        second = state.start_run("renamed", "two.yaml", task_id="stable-A")
        changes = state.track_semantic_changes(second, [ExtractedRecord(URL, "product", {"sku": "A", "price": 80})], identity_fields=("sku",))
        assert changes[0]["change_type"] == "modified"
        assert changes[0]["before"]["price"] == 100
        other = state.start_run("renamed", "three.yaml", task_id="stable-B")
        changes = state.track_semantic_changes(other, [ExtractedRecord(URL, "product", {"sku": "A", "price": 70})], identity_fields=("sku",))
        assert changes[0]["change_type"] == "added"
        assert changes[0]["baseline"]


def test_business_identity_survives_pagination_movement_and_replay(tmp_path):
    with StateStore(tmp_path / "state.sqlite3") as state:
        first = state.start_run("name", "one.yaml", task_id="stable")
        state.track_semantic_changes(first, [ExtractedRecord(URL, "product", {"sku": "A", "price": 100})], identity_fields=("sku",))
        second = state.start_run("name", "two.yaml", task_id="stable")
        moved = [ExtractedRecord(URL + "?page=2", "product", {"sku": "A", "price": 80})]
        changes = state.track_semantic_changes(second, moved, identity_fields=("sku",))
        assert changes[0]["change_type"] == "modified"
        assert state.track_semantic_changes(second, moved, identity_fields=("sku",)) == []
