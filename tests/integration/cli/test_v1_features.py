from __future__ import annotations

import tempfile
from pathlib import Path

import yaml

from omnicrawler.core.models import CrawlRequest, FetchResult
from omnicrawler.extraction.api_discovery import write_discovery_bundle
from omnicrawler.extraction.topic_filter import evaluate_topic, filter_records
from omnicrawler.pipeline_ops.task_spec import (
    AISpec,
    FileSpec,
    TaskSpec,
    TopicSpec,
    UpdateSpec,
    compile_execution_plan,
)
from omnicrawler.services.ai_providers import DisabledProvider, build_provider
from omnicrawler.services.help_registry import HELP_ENTRIES
from omnicrawler.state import StateStore


def test_task_spec_compiles_dynamic_topic_pdf_monitor() -> None:
    task = TaskSpec(
        name="政策人工智能PDF",
        intent="documents",
        seeds=["https://example.org/policy"],
        topic=TopicSpec(include_any=["人工智能", "大模型"], exclude=["失效"]),
        files=FileSpec(enabled=True, extensions=["pdf"], process_pdf=True),
        updates=UpdateSpec(enabled=True),
        ai=AISpec(mode="disabled"),
    )
    plan = compile_execution_plan(task)
    assert plan.config["source"]["kind"] == "incremental"
    assert plan.config["download"]["extensions"] == [".pdf"]
    assert plan.config["processors"]["pdf"]["enabled"] is True
    assert plan.config["selection"]["topic"]["include_any"] == ["人工智能", "大模型"]
    assert plan.config["updates"]["revisit_completed"] is True


def test_topic_filter_records_match_evidence() -> None:
    config = {
        "enabled": True,
        "include_any": ["人工智能", "大模型"],
        "include_all": [],
        "exclude": ["失效"],
        "match_on": ["title", "text"],
        "keep_uncertain": True,
    }
    decision = evaluate_topic({"title": "人工智能政策", "text": "有效"}, config)
    assert decision.matched
    records = filter_records([
        {"title": "大模型白皮书"},
        {"title": "人工智能办法（失效）"},
        {"title": "农业政策"},
    ], config)
    assert [item["title"] for item in records] == ["大模型白皮书"]
    assert records[0]["_topic_match"]["matched"] is True


def test_api_discovery_preserves_post_and_generates_executable_json_config() -> None:
    responses = [{
        "url": "https://api.example.org/search?page=1",
        "method": "POST",
        "status": 200,
        "content_type": "application/json",
        "request_headers": {"Content-Type": "application/json", "Cookie": "must-not-leak"},
        "request_payload": {"topic": "AI"},
        "json": {"items": [{"title": "A", "url": "/a.pdf"}]},
    }]
    with tempfile.TemporaryDirectory() as temp:
        bundle = write_discovery_bundle(responses, Path(temp))
        config = yaml.safe_load(Path(bundle["templates"][0]).read_text(encoding="utf-8"))
    assert config["source"]["method"] == "POST"
    assert config["source"]["payload"] == {"topic": "AI"}
    assert "Cookie" not in config["source"]["headers"]
    assert config["extract"]["item_path"] == "items"
    assert config["extract"]["fields"]["title"]["path"] == "title"


def test_help_registry_covers_new_simple_mode_options() -> None:
    for key in (
        "source.seed", "source.pagination", "selection.topic", "download.files",
        "processors.pdf", "updates.same_url", "ai.mode", "outputs.formats",
    ):
        assert HELP_ENTRIES[key].details


def test_ai_provider_is_opt_in_and_openai_compatible() -> None:
    assert isinstance(build_provider({"mode": "disabled"}), DisabledProvider)
    provider = build_provider({
        "mode": "custom",
        "default_provider": "demo",
        "providers": {
            "demo": {
                "type": "openai_compatible",
                "base_url": "http://127.0.0.1:11434/v1",
                "model": "local-model",
            }
        },
    })
    assert provider.model == "local-model"


def test_conditional_response_reuses_previous_version() -> None:
    with tempfile.TemporaryDirectory() as temp:
        state = StateStore(Path(temp) / "state.sqlite3")
        request = CrawlRequest("https://example.org/same-url")
        run1 = state.start_run("demo", "one.yaml")
        first = FetchResult(
            request, request.url, 200,
            {"content-type": "text/html", "etag": '"v1"', "last-modified": "Mon, 20 Jul 2026 00:00:00 GMT"},
            b"version one", 0.1,
        )
        assert state.save_response(run1, first, None) is True
        assert state.conditional_headers(request.url) == {
            "If-None-Match": '"v1"',
            "If-Modified-Since": "Mon, 20 Jul 2026 00:00:00 GMT",
        }
        run2 = state.start_run("demo", "two.yaml")
        unchanged = FetchResult(
            request, request.url, 304, {"etag": '"v1"'}, b"", 0.05,
            meta={"not_modified": True},
        )
        assert state.save_response(run2, unchanged, None) is False
        versions = state.conn.execute("SELECT COUNT(*) AS count FROM content_versions").fetchone()
        assert versions["count"] == 1
        state.close()
