"""AI 任务设计器修复测试（Phase 1d：C26/C28/C29/C30/C31/C32）。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from omnicrawl.services.ai_task_designer import (
    _append_ai_audit,
    ai_task_design_audit,
    parse_ai_task_output,
    validate_task_config_safety,
)


def _draft_json(request: str = "") -> str:
    value = {
        "known_requirements": {"url": "https://example.com/list", "intent": "collect_section", "topics": []},
        "assumptions": [{"field": "url", "value": "https://example.com", "reason": "r", "confidence": "high"}],
        "unresolved_questions": [],
        "config_patch": {},
        "explanations": [],
        "risks": [],
        "recommended_actions": ["试跑"],
    }
    return json.dumps(value, ensure_ascii=False)


def test_c26_markdown_fence_json_is_parsed() -> None:
    draft = parse_ai_task_output("```json\n" + _draft_json() + "\n```")
    assert draft.known_requirements["intent"] == "collect_section"


def test_c28_non_object_element_rejected() -> None:
    value = json.loads(_draft_json())
    value["unresolved_questions"] = ["需要确认什么"]
    with pytest.raises(ValueError, match="必须是对象"):
        parse_ai_task_output(json.dumps(value, ensure_ascii=False))


def test_c28_missing_subkey_rejected() -> None:
    value = json.loads(_draft_json())
    value["assumptions"] = [{"field": "url"}]  # 缺 value/confidence
    with pytest.raises(ValueError, match="缺少必填子键"):
        parse_ai_task_output(json.dumps(value, ensure_ascii=False))


def test_c29_request_is_backfilled() -> None:
    draft = parse_ai_task_output(_draft_json(), request="原始需求文本")
    assert draft.request == "原始需求文本"
    # 不传时不再伪造 user_request 字段
    assert parse_ai_task_output(_draft_json()).request == ""


def test_c30_domain_expansion_blocked() -> None:
    patch = {"seed_urls": ["https://evil.example.org/steal"], "allowed_domains": []}
    violations = validate_task_config_safety(patch, allowed_domains=["https://example.com"])
    assert any("扩大" in v for v in violations)


def test_c30_subdomain_is_allowed() -> None:
    patch = {"seed_urls": ["https://news.example.com/a"], "allowed_domains": ["example.com"]}
    assert validate_task_config_safety(patch, allowed_domains=["https://example.com"]) == []


def test_c30_nested_plain_secret_blocked() -> None:
    patch = {"extract": {"auth": {"api_key": "sk-plain"}}}
    violations = validate_task_config_safety(patch)
    assert any("明文敏感值" in v for v in violations)
    # secret:// 引用放行
    patch2 = {"extract": {"auth": {"api_key": "secret://MYKEY"}}}
    assert validate_task_config_safety(patch2) == []


def test_c31_unknown_cost_note_when_no_usage() -> None:
    result = type("R", (), {"provider": "p", "model": "m", "usage": {}, "text": ""})()
    draft = parse_ai_task_output(_draft_json(), request="r")
    record = ai_task_design_audit(result, draft)
    assert record["cost"] == 0.0
    assert "未知费用" in record["cost_note"]


def test_c32_audit_appended_to_jsonl(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        "omnicrawl.core.runtime_paths.portable_data_root",
        lambda: tmp_path,
    )
    _append_ai_audit({"provider": "p", "status": "ok"})
    path = tmp_path / ".omnicrawl" / "ai-logs" / "ai-audit.jsonl"
    assert path.is_file()
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["status"] == "ok"
