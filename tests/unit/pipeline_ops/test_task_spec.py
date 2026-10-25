"""C-#16：``pipeline_ops.task_spec`` 规格校验、种子协议约束与执行计划编译直接单测。"""
from __future__ import annotations

import pytest

from omnicrawler.pipeline_ops.task_spec import TaskSpec, compile_execution_plan


def _valid(**overrides: object) -> TaskSpec:
    base: dict[str, object] = {
        "name": "示例任务",
        "seeds": ["https://example.com/list"],
        "max_pages": 10,
        "max_depth": 2,
    }
    base.update(overrides)
    return TaskSpec.from_mapping(base)


def test_validate_collects_all_violations() -> None:
    task = TaskSpec(
        name="   ", seeds=["ftp://example.com", "javascript:alert(1)"],
        max_pages=0, max_depth=-1,
        ai=TaskSpec().ai.__class__(mode="nope"),
    )
    errors = task.validate()
    assert any("任务名称不能为空" in item for item in errors)
    assert any("入口网址" in item for item in errors)
    assert any("最大页面数" in item for item in errors)
    assert any("最大层级" in item for item in errors)
    assert any("AI 模式" in item for item in errors)


def test_valid_task_has_no_errors() -> None:
    assert _valid().validate() == []


def test_compile_defaults_to_crawl_kind() -> None:
    plan = compile_execution_plan(_valid())
    assert plan.config["source"]["kind"] == "crawl"
    assert plan.config["crawl"]["max_pages"] == 10
    assert plan.config["crawl"]["same_host"] is True
    assert any("同站范围" in decision for decision in plan.decisions)


def test_compile_browser_mode_and_api_intent() -> None:
    browser = compile_execution_plan(_valid(execution_mode="browser"))
    assert browser.config["source"]["kind"] == "browser"

    api = compile_execution_plan(_valid(intent="api"))
    assert api.config["source"]["kind"] == "rest"


def test_compile_incremental_and_focused_modes() -> None:
    incremental = compile_execution_plan(_valid(updates={"enabled": True}))
    assert incremental.config["source"]["kind"] == "incremental"

    focused = compile_execution_plan(_valid(files={"enabled": True}))
    assert focused.config["source"]["kind"] == "focused"
    assert focused.config["download"]["enabled"] is True


def test_compile_rejects_invalid_task_before_planning() -> None:
    with pytest.raises(ValueError, match="入口网址"):
        compile_execution_plan(TaskSpec(name="x", seeds=[]))


def test_from_mapping_ignores_unknown_nested_keys() -> None:
    task = TaskSpec.from_mapping({
        "name": "嵌套",
        "seeds": ["https://example.com"],
        "topic": {"include_any": ["价格"], "bogus": 1},
        "ai": {"mode": "local", "provider": "demo", "unknown": True},
    })
    assert task.topic.include_any == ["价格"]
    assert task.ai.mode == "local" and task.ai.provider == "demo"


def test_slug_falls_back_to_host_for_default_name() -> None:
    plan = compile_execution_plan(TaskSpec(seeds=["https://shop.example.com/products"]))
    assert plan.config["project"]["workspace"] == "work/shop_example_com"
