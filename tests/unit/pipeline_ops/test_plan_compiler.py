"""C-#16：``pipeline_ops.plan_compiler`` 计划编译 / 冲突 / 估计 / 哈希 / 差异直接单测。"""
from __future__ import annotations

import pytest

from omnicrawler.pipeline_ops.plan_compiler import (
    _redact_for_hash,
    compile_task_plan,
    diff_plans,
)
from omnicrawler.pipeline_ops.task_ir import TaskIR


def _ir(**overrides: object) -> TaskIR:
    base: dict[str, object] = {
        "source": {"seeds": ["https://example.com/a"]},
        "scope": {"max_pages": 5},
        "capabilities": [],
    }
    base.update(overrides)
    return TaskIR.from_mapping(base)


def test_compile_plan_baseline_hash_estimates_and_permissions() -> None:
    plan = compile_task_plan(_ir())
    assert plan.plan_version == 1
    assert len(plan.plan_hash) == 64
    assert plan.estimates["maximum_pages"] == 5
    assert plan.permissions["network_domains"] == ["example.com"]
    assert "个入口" in plan.explanation[0]
    assert plan.conflicts == ()


def test_non_string_seed_rejected() -> None:
    with pytest.raises(ValueError, match="种子元素必须是字符串"):
        compile_task_plan(_ir(source={"seeds": [{"url": "https://example.com"}]}))


def test_empty_seed_reports_conflict() -> None:
    plan = compile_task_plan(_ir(source={"seeds": []}))
    assert any("数据源入口" in conflict for conflict in plan.conflicts)
    assert any("冲突" in warning for warning in plan.warnings)


def test_missing_capability_reports_conflict() -> None:
    plan = compile_task_plan(_ir(capabilities=["browser"]), available_capabilities={"rest"})
    assert any("缺少运行能力: browser" in conflict for conflict in plan.conflicts)


def test_egress_domain_conflict_reported() -> None:
    # 种子域名不在 allowed_domains 白名单内应报冲突（passthrough 经 to_config 合并回 config）。
    ir = TaskIR.from_mapping({
        "source": {"seeds": ["https://example.com/a"]},
        "scope": {"max_pages": 3},
        "capabilities": [],
        "extensions": {"config_passthrough": {"egress": {"allowed_domains": ["other.com"]}}},
    })
    plan = compile_task_plan(ir)
    assert any("egress.allowed_domains冲突" in conflict for conflict in plan.conflicts)


def test_diff_plans_records_changed_fields() -> None:
    before = compile_task_plan(_ir())
    after = compile_task_plan(_ir(scope={"max_pages": 12}))
    changes = diff_plans(before, after)
    assert any(change["path"].endswith("estimates.maximum_pages") for change in changes)


def test_redact_for_hash_masks_sensitive_keys_recursively() -> None:
    redacted = _redact_for_hash({
        "auth": {"password": "secret", "token": "x", "plain": "ok"},
        "items": [{"api_key": "k", "name": "n"}],
    })
    assert redacted["auth"]["password"] == "<redacted>"
    assert redacted["auth"]["token"] == "<redacted>"
    assert redacted["auth"]["plain"] == "ok"
    assert redacted["items"][0]["api_key"] == "<redacted>"
    assert redacted["items"][0]["name"] == "n"
