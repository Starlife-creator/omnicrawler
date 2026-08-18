from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from omnicrawler.core.config import load_config
from omnicrawler.pipeline_ops.plan_compiler import compile_task_plan, diff_plans
from omnicrawler.pipeline_ops.task_ir import (
    TaskIR,
    api_candidate_fragment,
    recording_fragment,
    template_fragment,
)
from omnicrawler.services.application_service import ApplicationService


def _config(tmp_path: Path, *, pages: int = 12) -> Path:
    path = tmp_path / f"task-{pages}.yaml"
    path.write_text(
        "config_version: 5\n"
        f"project: {{name: ir, workspace: '{tmp_path / 'work'}', custom_project: keep}}\n"
        "source: {kind: browser, seeds: [https://portal.example.org/start], custom_source: keep}\n"
        f"crawl: {{max_pages: {pages}, max_depth: 2, same_host: true}}\n"
        "browser: {actions: [{type: click, selector: '#next'}]}\n"
        "extract: {fields: {title: {selector: h1, type: text}}}\n"
        "download: {enabled: true, extensions: [.pdf]}\n"
        "processors: {pdf: {enabled: true, skip_ocr: false}}\n"
        "outputs: {jsonl: true, csv: true}\n"
        "x-future: {nested: [one, two]}\n",
        encoding="utf-8",
    )
    return path


def test_yaml_v5_ir_roundtrip_preserves_unknowns_and_all_task_dimensions(tmp_path: Path) -> None:
    config = load_config(_config(tmp_path))
    ir = TaskIR.from_config(config.raw)
    mapping = ir.to_mapping()
    restored = TaskIR.from_mapping(mapping).to_config()
    assert mapping["ir_version"] == 1
    assert restored["x-future"] == {"nested": ["one", "two"]}
    assert restored["project"]["custom_project"] == "keep"
    assert restored["source"]["custom_source"] == "keep"
    assert ir.actions[0]["type"] == "click"
    assert ir.fields["title"]["selector"] == "h1"
    assert set(ir.capabilities) == {"browser", "ocr"}


def test_plan_is_deterministic_secret_independent_explainable_and_diffable(tmp_path: Path) -> None:
    first_ir = TaskIR.from_config(load_config(_config(tmp_path, pages=12)).raw)
    first_ir.authorization = {"provider": "form", "options": {"password": "first"}}
    first = compile_task_plan(first_ir, available_capabilities=["browser", "ocr"])
    repeated_ir = TaskIR.from_mapping(first_ir.to_mapping())
    repeated_ir.authorization["options"]["password"] = "second"
    repeated = compile_task_plan(repeated_ir, available_capabilities=["browser", "ocr"])
    assert first.plan_hash == repeated.plan_hash
    assert first.conflicts == ()
    assert first.permissions["network_domains"] == ["portal.example.org"]
    assert first.permissions["credentials"]["required"] is True
    assert any("最多处理12个页面" in line for line in first.explanation)

    changed = compile_task_plan(TaskIR.from_config(load_config(_config(tmp_path, pages=20)).raw))
    changes = diff_plans(first, changed)
    assert any(item["path"].endswith("max_pages") for item in changes)
    assert first.plan_hash != changed.plan_hash


def test_plan_detects_missing_capabilities_and_application_service_has_public_dtos(tmp_path: Path) -> None:
    path = _config(tmp_path)
    plan = compile_task_plan(TaskIR.from_config(load_config(path).raw), available_capabilities=[])
    assert "缺少运行能力: browser, ocr" in plan.conflicts
    events: list[dict] = []
    service = ApplicationService(path, event_sink=events.append)
    loaded = service.load()
    compiled = service.compile(available_capabilities=["browser", "ocr"])
    assert loaded["config"]["project_name"] == "ir"
    assert isinstance(compiled["plan_hash"], str) and len(compiled["plan_hash"]) == 64
    assert events[0]["category"] == "stage"
    assert all("Pipeline" not in type(value).__name__ for value in compiled.values())


def test_ir_requires_plugin_capability_only_when_task_declares_plugins(tmp_path: Path) -> None:
    """plugin 能力仅当任务显式声明插件时要求，而非因默认搜索目录普遍要求。"""
    base = _config(tmp_path)
    # 默认配置：仅默认插件搜索目录，不要求 plugin 能力
    default_ir = TaskIR.from_config(load_config(base).raw)
    assert "plugin" not in default_ir.capabilities

    # 显式声明插件文件：要求 plugin 能力
    custom = tmp_path / "task-with-plugin.yaml"
    custom.write_text(
        base.read_text(encoding="utf-8").rstrip()
        + "\nplugins: {paths: [examples/plugins/example_site.py]}\n",
        encoding="utf-8",
    )
    plugin_ir = TaskIR.from_config(load_config(custom).raw)
    assert "plugin" in plugin_ir.capabilities

    # 能力受限环境（不含 plugin）应报缺 plugin 能力
    plan = compile_task_plan(plugin_ir, available_capabilities=["browser", "ocr"])
    assert "缺少运行能力: plugin" in plan.conflicts


def test_recording_api_and_template_inputs_merge_through_one_ir_contract(tmp_path: Path) -> None:
    base = TaskIR.from_config(load_config(_config(tmp_path)).raw)
    recorded = base.merge_fragment(recording_fragment({
        "start_url": "https://portal.example.org/list",
        "actions": [{"type": "fill", "selector": "#q", "value": "report"}],
        "api_responses": [{"url": "https://portal.example.org/api"}],
    }))
    assert recorded.actions[0]["type"] == "fill"
    assert recorded.extensions["api_candidates"][0]["url"].endswith("/api")

    api = recorded.merge_fragment(api_candidate_fragment({
        "url": "https://portal.example.org/api/items", "method": "post",
        "body": {"page": 1}, "pagination": {"type": "page", "parameter": "page"},
    }))
    assert api.source["kind"] == "rest" and api.source["method"] == "POST"
    assert api.pagination["parameter"] == "page"

    fragment = template_fragment({
        "project": {"name": "template"}, "source": {"kind": "crawl", "seeds": ["https://docs.example.org/"]},
        "extract": {"fields": {"date": {"selector": "time"}}},
    })
    merged = api.merge_fragment(fragment)
    assert merged.fields["date"]["selector"] == "time"
    assert merged.extensions["api_candidate_evidence"]["method"] == "post"


def test_application_service_run_does_not_mutate_cached_config(tmp_path: Path) -> None:
    """S1.1.1: run(max_pages) 不得就地改写缓存配置，污染后续任务的默认值。"""
    path = _config(tmp_path, pages=12)
    service = ApplicationService(path)
    before = compile_task_plan(TaskIR.from_config(load_config(path).raw)).to_mapping()

    with patch("omnicrawler.services.application_service.Pipeline") as mock_pipeline:
        mock_pipeline.return_value.__enter__.return_value.run.return_value = {"status": "succeeded"}
        service.run(max_pages=50)

    after = compile_task_plan(TaskIR.from_config(load_config(path).raw)).to_mapping()
    assert after == before
    assert after["config"]["crawl"]["max_pages"] == 12


def test_application_service_plan_equivalence_and_sample_binding(tmp_path: Path) -> None:
    path = _config(tmp_path)
    service = ApplicationService(path)
    direct = compile_task_plan(TaskIR.from_config(load_config(path).raw)).to_mapping()
    assert service.compile() == direct

    with patch("omnicrawler.application_service.run_sample", return_value={"status": "sampled"}):
        sampled = service.sample(pages=3)
    assert sampled["plan_hash"] == direct["plan_hash"]

    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    raw["crawl"]["max_pages"] = 99
    path.write_text(yaml.safe_dump(raw, allow_unicode=True), encoding="utf-8")
    with pytest.raises(ValueError, match="试跑计划不一致"):
        service.run(require_sample_match=True)
