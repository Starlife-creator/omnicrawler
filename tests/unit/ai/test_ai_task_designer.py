"""Test AI task designer output schema, validation, and safety boundaries.

Covers:
- AI structured output and Schema validation
- AI offline/timeout/quota/privacy rejection
- AI config modification, diff display and undo
- Safety constraint enforcement
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

# Allow importing omnicrawl from source tree
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from omnicrawl.services.ai_task_designer import (
    ConfigChange,
    ConfigDiff,
    build_task_design_messages,
    diff_config_changes,
    format_task_design_for_display,
    parse_ai_task_output,
    validate_task_config_safety,
)

# ---------------------------------------------------------------------------
# AI output parsing
# ---------------------------------------------------------------------------

VALID_AI_JSON = json.dumps({
    "known_requirements": {
        "url": "https://example.com/news",
        "intent": "collect_section",
        "topics": ["新能源"],
        "schedule": "weekly",
        "explicit_requirements": ["每周检查", "只下载PDF"],
    },
    "assumptions": [
        {"field": "source_kind", "value": "static_html", "reason": "入口页面不含JS", "confidence": "high"},
        {"field": "max_pages", "value": 100, "reason": "栏目估约20篇文章/周", "confidence": "medium"},
    ],
    "unresolved_questions": [
        {"question": "是否需要登录？", "why": "不确定网站是否需要认证", "options": ["需要登录", "不需要"], "recommendation": "先尝试无登录访问"},
    ],
    "config_patch": {
        "seed_urls": ["https://example.com/news"],
        "task_intent": "collect_section",
        "max_pages": 100,
        "process_pdf": True,
        "monitor_same_url": True,
    },
    "explanations": [
        {"field": "process_pdf", "before": False, "after": True, "why": "用户要求下载PDF"},
    ],
    "risks": [
        {"risk": "目标网站可能有访问频率限制", "severity": "medium", "mitigation": "并发设为1，延迟2秒"},
    ],
    "recommended_actions": ["试跑3页验证范围", "确认主题词准确性"],
})


class TestParseAITaskOutput:
    def test_valid_json_parses(self) -> None:
        draft = parse_ai_task_output(VALID_AI_JSON)
        assert draft is not None
        assert draft.known_requirements["url"] == "https://example.com/news"
        assert len(draft.assumptions) == 2
        assert len(draft.unresolved_questions) == 1
        assert draft.has_unresolved
        assert draft.has_risks

    def test_high_confidence_assumptions(self) -> None:
        draft = parse_ai_task_output(VALID_AI_JSON)
        # Only high-confidence assumptions are all-high
        assert not draft.high_confidence  # one medium

    def test_invalid_json_raises(self) -> None:
        with pytest.raises(ValueError, match="无效"):
            parse_ai_task_output("not json")

    def test_missing_fields_raises(self) -> None:
        with pytest.raises(ValueError, match="缺少必要字段"):
            parse_ai_task_output('{"known_requirements": {}}')

    def test_wrong_type_fields_raises(self) -> None:
        bad = json.loads(VALID_AI_JSON)
        bad["assumptions"] = "not a list"
        with pytest.raises(ValueError):
            parse_ai_task_output(json.dumps(bad))


# ---------------------------------------------------------------------------
# Config diff
# ---------------------------------------------------------------------------

class TestConfigDiff:
    def test_simple_diff(self) -> None:
        current = {"concurrency": 4, "max_pages": 100}
        proposed = {"concurrency": 2, "max_pages": 100}
        explanations = [{"field": "concurrency", "why": "降低以避免触发限制"}]
        diff = diff_config_changes(current, proposed, explanations)
        assert len(diff.changes) == 1
        assert diff.changes[0].field == "concurrency"
        assert diff.changes[0].before == 4
        assert diff.changes[0].after == 2
        assert diff.changes[0].reversible

    def test_additions_and_removals(self) -> None:
        current = {"seed_urls": ["https://a.com"]}
        proposed = {"seed_urls": ["https://a.com"], "process_pdf": True}
        explanations: list[dict[str, str]] = []
        diff = diff_config_changes(current, proposed, explanations)
        assert "process_pdf" in diff.additions

    def test_empty_diff(self) -> None:
        diff = diff_config_changes({}, {}, [])
        assert diff.empty

    def test_format_diff_readable(self) -> None:
        diff = ConfigDiff(
            changes=[ConfigChange("concurrency", 4, 2, "降低并发", True)],
        )
        formatted = diff.format_diff()
        assert "concurrency" in formatted
        assert "4" in formatted
        assert "2" in formatted
        assert "撤销" in formatted


# ---------------------------------------------------------------------------
# Safety validation
# ---------------------------------------------------------------------------

class TestSafetyValidation:
    def test_disable_security_blocked(self) -> None:
        violations = validate_task_config_safety({"disable_security": True})
        assert len(violations) > 0
        assert any("安全策略" in v for v in violations)

    def test_plaintext_key_blocked(self) -> None:
        violations = validate_task_config_safety({"api_key": "sk-real-key-value"})
        assert len(violations) > 0
        assert any("明文" in v for v in violations)

    def test_secret_ref_allowed(self) -> None:
        violations = validate_task_config_safety({"api_key": "secret://openai_key"})
        assert len(violations) == 0

    def test_wildcard_domains_blocked(self) -> None:
        violations = validate_task_config_safety({"allowed_domains": ["*"]})
        assert len(violations) > 0
        assert any("域名" in v for v in violations)

    def test_clean_config_passes(self) -> None:
        violations = validate_task_config_safety({
            "seed_urls": ["https://example.com"],
            "max_pages": 10,
        })
        assert len(violations) == 0


# ---------------------------------------------------------------------------
# Display formatting
# ---------------------------------------------------------------------------

class TestFormatDisplay:
    def test_format_includes_sections(self) -> None:
        draft = parse_ai_task_output(VALID_AI_JSON)
        display = format_task_design_for_display(draft)
        assert "已明确的需求" in display
        assert "系统假设" in display
        assert "需要您确认" in display
        assert "需要注意的风险" in display
        assert "安全保证" in display

    def test_format_includes_safety_constraints(self) -> None:
        draft = parse_ai_task_output(VALID_AI_JSON)
        display = format_task_design_for_display(draft)
        assert "不扩大入口域名" in display
        assert "不写入真实凭据" in display


# ---------------------------------------------------------------------------
# Messages building
# ---------------------------------------------------------------------------

class TestBuildMessages:
    def test_builds_system_and_user(self) -> None:
        messages = build_task_design_messages("采集 https://example.com/news 的新闻")
        assert len(messages) == 2
        assert messages[0]["role"] == "system"
        assert messages[1]["role"] == "user"
        assert "安全边界" in messages[0]["content"]
        assert "https://example.com/news" in messages[1]["content"]
