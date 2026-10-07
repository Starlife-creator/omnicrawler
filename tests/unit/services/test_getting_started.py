import json

import pytest

from omnicrawler.core.config import load_config
from omnicrawler.core.models import CrawlRequest
from omnicrawler.pipeline import Pipeline
from omnicrawler.pipeline_ops.preflight import run_sample
from omnicrawler.services.getting_started import create_starter, progress
from omnicrawler.sources.local_files import fetch, seed


def test_fresh_starters_preserve_previous_edits_and_need_no_network(tmp_path):
    first = create_starter(tmp_path)
    first.write_bytes(first.read_bytes() + b"# user edit\n")
    before = first.read_bytes()
    second = create_starter(tmp_path)
    assert first != second and first.read_bytes() == before
    config = load_config(second)
    assert config.source_kind == "file" and config.section("ai")["mode"] == "disabled"
    with Pipeline(config) as pipeline:
        summary = pipeline.run()
    assert summary["status"] == "succeeded" and summary["records"] == 3
    records = [json.loads(line)["data"] for line in (config.workspace / "output/records.jsonl").read_text(encoding="utf8").splitlines()]
    zero = next(row for row in records if row["name"] == "苹果")
    assert type(zero["price"]) is int and zero["price"] == 0 and zero["in_stock"] is False


def test_progress_is_bound_to_current_trial_and_run_and_keeps_review_pending(tmp_path):
    path = create_starter(tmp_path)
    config = load_config(path)
    assert progress(config)["next_step"]["step"] == "trial"
    result = run_sample(config, pages=1)
    assert result["sample"]["status"] == "succeeded"
    assert progress(config)["next_step"]["step"] == "run"
    with Pipeline(config) as pipeline:
        assert pipeline.run()["status"] == "succeeded"
    current = progress(config)
    assert current["next_step"]["step"] == "delivery"
    assert current["next_step"]["state"] == "awaiting_user_review"
    config.raw["extract"]["fields"]["price"]["path"] = "different_price"
    changed = progress(config)
    assert changed["next_step"]["step"] == "trial" and changed["next_step"]["state"] == "stale"


def test_local_import_does_not_allow_undeclared_or_escaping_files(tmp_path):
    config = load_config(create_starter(tmp_path))
    unrelated = config.path.parent / "private.json"
    unrelated.write_text('{"private":true}', encoding="utf8")
    with pytest.raises(PermissionError, match="明确列出"):
        fetch(config, CrawlRequest(unrelated.as_uri()))
    config.raw["source"]["local_files"] = ["../outside.json"]
    with pytest.raises(PermissionError, match="不能越过"):
        seed(config)


def test_local_import_blocks_links_and_oversized_inputs(tmp_path):
    config = load_config(create_starter(tmp_path))
    data = config.path.parent / "items.json"
    with data.open("wb") as stream:
        stream.truncate(16 * 1024 * 1024 + 1)
    with pytest.raises(ValueError, match="16 MiB"):
        fetch(config, CrawlRequest(data.as_uri()))


def test_network_policy_still_rejects_file_urls(tmp_path):
    from omnicrawler.core.errors import PolicyBlockedError
    from omnicrawler.security.policy import NetworkTargetPolicy
    config = load_config(create_starter(tmp_path))
    with pytest.raises(PolicyBlockedError):
        NetworkTargetPolicy(config).require(config.section("source")["seeds"][0])
