"""Tests for scheduling.change_detector — pure logic, zero IO dependencies."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

try:
    from datetime import UTC, datetime  # Python 3.11+
except ImportError:
    from datetime import datetime, timezone
    UTC = timezone.utc

import pytest

from omnicrawl.scheduling.change_detector import (
    ChangeDetector,
    ChangeEvent,
    MonitorRule,
)

# ── MonitorRule ────────────────────────────────────────────────────────

class TestMonitorRule:
    def test_default_construction(self) -> None:
        rule = MonitorRule(url="https://example.com")
        assert rule.url == "https://example.com"
        assert rule.name == ""
        assert rule.rule_id  # auto-generated
        assert len(rule.rule_id) == 12
        assert rule.selector == ""
        assert rule.condition == "changed"
        assert rule.check_interval == 3600
        assert rule.enabled is True
        assert rule.notify_methods == ["desktop"]
        assert rule.last_hash is None
        assert rule.last_content is None
        assert rule.last_checked is None
        assert isinstance(rule.created_at, datetime)

    def test_full_construction(self) -> None:
        now = datetime.now(tz=UTC)
        rule = MonitorRule(
            url="https://example.com/page",
            name="Test",
            rule_id="abc123456789",
            selector=".content",
            condition="contains:hello",
            check_interval=600,
            enabled=False,
            notify_methods=["email"],
            last_hash="abc",
            last_content="old content",
            last_checked=now,
            created_at=now,
        )
        assert rule.rule_id == "abc123456789"
        assert rule.check_interval == 600
        assert rule.enabled is False
        assert rule.last_hash == "abc"

    def test_to_dict(self) -> None:
        now = datetime.now(tz=UTC)
        rule = MonitorRule(url="https://x.com", name="T", last_checked=now, created_at=now)
        d = rule.to_dict()
        assert d["url"] == "https://x.com"
        assert d["name"] == "T"
        assert d["created_at"] == now.isoformat()
        assert d["last_checked"] == now.isoformat()
        # last_hash / last_content were None
        assert d["last_hash"] is None
        assert d["last_content"] is None

    def test_to_dict_none_dates(self) -> None:
        # __post_init__ auto-assigns created_at; last_checked remains None
        rule = MonitorRule(url="https://x.com", last_checked=None)
        d = rule.to_dict()
        assert d["created_at"] is not None  # auto-assigned
        assert d["last_checked"] is None

    def test_from_dict_basic(self) -> None:
        data = {"url": "https://example.com", "name": "FromDict", "rule_id": "fee", "check_interval": 7200}
        rule = MonitorRule.from_dict(data)
        assert rule.url == "https://example.com"
        assert rule.name == "FromDict"
        assert rule.check_interval == 7200

    def test_from_dict_with_iso_dates(self) -> None:
        now = datetime.now(tz=UTC)
        data = {"url": "https://example.com", "created_at": now.isoformat(), "last_checked": now.isoformat()}
        rule = MonitorRule.from_dict(data)
        assert isinstance(rule.created_at, datetime)
        assert isinstance(rule.last_checked, datetime)

    def test_from_dict_invalid_date_falls_back_to_none(self) -> None:
        data = {"url": "https://example.com", "created_at": "not-a-date"}
        rule = MonitorRule.from_dict(data)
        # from_dict converts bad date to None, __post_init__ then auto-assigns
        assert isinstance(rule.created_at, datetime)

    def test_from_dict_ignores_extra_keys(self) -> None:
        data = {"url": "https://example.com", "extra_field": "should be ignored"}
        rule = MonitorRule.from_dict(data)
        assert rule.url == "https://example.com"


# ── ChangeEvent ────────────────────────────────────────────────────────

class TestChangeEvent:
    def test_construction_and_to_dict(self) -> None:
        now = datetime.now(tz=UTC)
        evt = ChangeEvent(
            rule_id="rid",
            rule_name="RuleName",
            url="https://example.com",
            detected_at=now,
            previous_hash="oldhash",
            current_hash="newhash",
            previous_content="old",
            current_content="new",
            diff_summary="变化摘要",
        )
        d = evt.to_dict()
        assert d["rule_id"] == "rid"
        assert d["detected_at"] == now.isoformat()
        assert d["diff_summary"] == "变化摘要"


# ── ChangeDetector — rules management ─────────────────────────────────

class TestChangeDetectorRules:
    def test_add_and_get_rule(self) -> None:
        cd = ChangeDetector()
        rule = MonitorRule(url="https://example.com", name="test")
        rid = cd.add_rule(rule)
        assert rid == rule.rule_id
        assert cd.get_rule(rid) is rule

    def test_list_rules(self) -> None:
        cd = ChangeDetector()
        cd.add_rule(MonitorRule(url="https://a.com"))
        cd.add_rule(MonitorRule(url="https://b.com"))
        assert len(cd.list_rules()) == 2

    def test_remove_existing_rule(self) -> None:
        cd = ChangeDetector()
        rule = MonitorRule(url="https://example.com")
        cd.add_rule(rule)
        assert cd.remove_rule(rule.rule_id) is True
        assert cd.get_rule(rule.rule_id) is None

    def test_remove_nonexistent_rule(self) -> None:
        cd = ChangeDetector()
        assert cd.remove_rule("nonexistent") is False

    def test_pause_resume(self) -> None:
        cd = ChangeDetector()
        cd.pause()
        # access private attr to verify
        assert cd._running is False
        cd.resume()
        assert cd._running is True


# ── ChangeDetector — compute hash ──────────────────────────────────────

class TestComputeHash:
    def test_deterministic_same_input_same_hash(self) -> None:
        h1 = ChangeDetector._compute_hash("hello")
        h2 = ChangeDetector._compute_hash("hello")
        assert h1 == h2

    def test_different_input_different_hash(self) -> None:
        h1 = ChangeDetector._compute_hash("hello")
        h2 = ChangeDetector._compute_hash("world")
        assert h1 != h2

    def test_special_characters(self) -> None:
        h = ChangeDetector._compute_hash("中文 emoji 😀")
        assert isinstance(h, str)
        assert len(h) == 64  # sha256 hex


# ── ChangeDetector — _extract_content ──────────────────────────────────

class TestExtractContent:
    def test_no_selector_returns_full_html(self) -> None:
        html = "<html><body><p>Hello</p></body></html>"
        assert ChangeDetector._extract_content(html, "") == html

    def test_simple_text_extraction_without_parser(self) -> None:
        """When selector is empty, full HTML is returned — no parser needed."""
        html = "<html><body><p>Hello</p></body></html>"
        assert ChangeDetector._extract_content(html, "") == html


# ── ChangeDetector — _check_condition ──────────────────────────────────

class TestCheckCondition:
    def test_changed_always_true(self) -> None:
        assert ChangeDetector._check_condition("anything", "changed") is True

    def test_contains_match(self) -> None:
        assert ChangeDetector._check_condition("hello world", "contains:hello") is True

    def test_contains_no_match(self) -> None:
        assert ChangeDetector._check_condition("hello world", "contains:xyz") is False

    def test_regex_match(self) -> None:
        assert ChangeDetector._check_condition("price: 99.9", "regex:price:\\s*\\d+\\.\\d+") is True

    def test_regex_no_match(self) -> None:
        assert ChangeDetector._check_condition("hello", "regex:^world$") is False

    def test_regex_invalid_pattern(self) -> None:
        assert ChangeDetector._check_condition("hello", "regex:[invalid") is False

    def test_equals_match(self) -> None:
        assert ChangeDetector._check_condition("  hello  ", "equals:hello") is True

    def test_equals_no_match(self) -> None:
        assert ChangeDetector._check_condition("hello", "equals:world") is False

    def test_unknown_condition_falls_back_to_true(self) -> None:
        assert ChangeDetector._check_condition("anything", "bogus") is True


# ── ChangeDetector — _build_diff_summary ──────────────────────────────

class TestBuildDiffSummary:
    def test_first_check_baseline(self) -> None:
        cd = ChangeDetector()
        assert cd._build_diff_summary(None, "content") == "首次检查，建立基线"

    def test_no_change(self) -> None:
        cd = ChangeDetector()
        assert cd._build_diff_summary("same", "same") == "内容未变化"

    def test_added_lines(self) -> None:
        cd = ChangeDetector()
        summary = cd._build_diff_summary("line1\nline2", "line1\nline2\nnewline")
        assert "新增 1 行" in summary

    def test_removed_lines(self) -> None:
        cd = ChangeDetector()
        summary = cd._build_diff_summary("line1\nline2\nold", "line1\nline2")
        assert "移除 1 行" in summary

    def test_length_diff_increase(self) -> None:
        cd = ChangeDetector()
        summary = cd._build_diff_summary("abc", "abcdefghij")
        assert "增加" in summary

    def test_length_diff_decrease(self) -> None:
        cd = ChangeDetector()
        summary = cd._build_diff_summary("abcdefghij", "abc")
        assert "减少" in summary

    def test_identical_length_no_newlines(self) -> None:
        cd = ChangeDetector()
        summary = cd._build_diff_summary("x", "y")
        # Both are one line, different content → 1 added + 1 removed
        assert "新增" in summary or "移除" in summary or "发生变化" in summary


# ── ChangeDetector — check_rule (async) ───────────────────────────────

class TestCheckRule:
    @pytest.mark.asyncio
    async def test_disabled_rule_returns_none(self) -> None:
        cd = ChangeDetector()
        rule = MonitorRule(url="https://example.com", enabled=False)
        cd.add_rule(rule)
        with patch.object(cd, "_fetch_content", new_callable=AsyncMock) as m:
            result = await cd.check_rule(rule.rule_id)
            assert result is None
            m.assert_not_called()

    @pytest.mark.asyncio
    async def test_nonexistent_rule_returns_none(self) -> None:
        cd = ChangeDetector()
        result = await cd.check_rule("nonexistent")
        assert result is None

    @pytest.mark.asyncio
    async def test_interval_not_elapsed_returns_none(self) -> None:
        cd = ChangeDetector()
        now = datetime.now(tz=UTC)
        rule = MonitorRule(url="https://example.com", check_interval=3600, last_checked=now)
        cd.add_rule(rule)
        with patch.object(cd, "_fetch_content", new_callable=AsyncMock) as m:
            result = await cd.check_rule(rule.rule_id)
            assert result is None
            m.assert_not_called()

    @pytest.mark.asyncio
    async def test_fetch_failure_returns_none(self) -> None:
        cd = ChangeDetector()
        rule = MonitorRule(url="https://example.com")
        cd.add_rule(rule)
        with patch.object(cd, "_fetch_content", new_callable=AsyncMock, return_value=None):
            result = await cd.check_rule(rule.rule_id)
            assert result is None

    @pytest.mark.asyncio
    async def test_first_check_establishes_baseline_no_event(self) -> None:
        cd = ChangeDetector()
        rule = MonitorRule(url="https://example.com")
        cd.add_rule(rule)
        with patch.object(cd, "_fetch_content", new_callable=AsyncMock, return_value="<html><p>hello</p></html>"):
            result = await cd.check_rule(rule.rule_id)
            assert result is None  # baseline, no event
            assert rule.last_hash is not None
            assert rule.last_content is not None
            assert rule.last_checked is not None

    @pytest.mark.asyncio
    async def test_no_change_updates_timestamp(self) -> None:
        cd = ChangeDetector()
        rule = MonitorRule(url="https://example.com")
        h = ChangeDetector._compute_hash("same content")
        rule.last_hash = h
        rule.last_content = "same content"
        rule.last_checked = datetime(2020, 1, 1, tzinfo=UTC)
        cd.add_rule(rule)
        with patch.object(cd, "_fetch_content", new_callable=AsyncMock, return_value="same content"):
            result = await cd.check_rule(rule.rule_id)
            assert result is None
            assert rule.last_checked > datetime(2020, 1, 1, tzinfo=UTC)

    @pytest.mark.asyncio
    async def test_change_detected_returns_event(self) -> None:
        cd = ChangeDetector()
        rule = MonitorRule(url="https://example.com", name="TestRule")
        rule.last_hash = "oldhash"
        rule.last_content = "old content"
        rule.last_checked = datetime(2020, 1, 1, tzinfo=UTC)
        cd.add_rule(rule)
        with patch.object(cd, "_fetch_content", new_callable=AsyncMock, return_value="new content here"):
            result = await cd.check_rule(rule.rule_id)
            assert result is not None
            assert isinstance(result, ChangeEvent)
            assert result.rule_id == rule.rule_id
            assert result.rule_name == "TestRule"
            assert result.previous_hash == "oldhash"
            assert result.current_hash != "oldhash"
            assert result.previous_content == "old content"
            assert result.current_content == "new content here"

    @pytest.mark.asyncio
    async def test_condition_check_fails_no_event(self) -> None:
        cd = ChangeDetector()
        rule = MonitorRule(url="https://example.com", condition="contains:SPECIAL")
        rule.last_hash = "oldhash"
        rule.last_content = "old"
        rule.last_checked = datetime(2020, 1, 1, tzinfo=UTC)
        cd.add_rule(rule)
        with patch.object(cd, "_fetch_content", new_callable=AsyncMock, return_value="new plain content"):
            result = await cd.check_rule(rule.rule_id)
            assert result is None  # content changed but condition not met

    @pytest.mark.asyncio
    async def test_notify_callback_called_on_change(self) -> None:
        events: list = []
        cd = ChangeDetector(on_notify=events.append)
        rule = MonitorRule(url="https://example.com")
        rule.last_hash = "oldhash"
        rule.last_content = "old"
        rule.last_checked = datetime(2020, 1, 1, tzinfo=UTC)
        cd.add_rule(rule)
        with patch.object(cd, "_fetch_content", new_callable=AsyncMock, return_value="new content"):
            result = await cd.check_rule(rule.rule_id)
            assert len(events) == 1
            assert events[0] is result


# ── ChangeDetector — check_all ─────────────────────────────────────────

class TestCheckAll:
    @pytest.mark.asyncio
    async def test_no_rules_returns_empty(self) -> None:
        cd = ChangeDetector()
        events = await cd.check_all()
        assert events == []

    @pytest.mark.asyncio
    async def test_multiple_rules_collects_events(self) -> None:
        cd = ChangeDetector()
        for url in ("https://a.com", "https://b.com"):
            rule = MonitorRule(url=url)
            rule.last_hash = "old"
            rule.last_content = "old"
            rule.last_checked = datetime(2020, 1, 1, tzinfo=UTC)
            cd.add_rule(rule)
        with patch.object(cd, "_fetch_content", new_callable=AsyncMock, return_value="new content"):
            events = await cd.check_all()
            assert len(events) == 2

    @pytest.mark.asyncio
    async def test_stopped_early(self) -> None:
        cd = ChangeDetector()
        cd.add_rule(MonitorRule(url="https://a.com"))
        cd.add_rule(MonitorRule(url="https://b.com"))
        cd.pause()
        events = await cd.check_all()
        assert events == []  # stopped before any check


# ── ChangeDetector — history ──────────────────────────────────────────

class TestHistory:
    def test_empty_history(self) -> None:
        cd = ChangeDetector()
        assert cd.get_history("nonexistent") == []

    @pytest.mark.asyncio
    async def test_event_recorded_in_history(self) -> None:
        cd = ChangeDetector()
        rule = MonitorRule(url="https://example.com")
        rule.last_hash = "old"
        rule.last_content = "old"
        rule.last_checked = datetime(2020, 1, 1, tzinfo=UTC)
        cd.add_rule(rule)
        with patch.object(cd, "_fetch_content", new_callable=AsyncMock, return_value="new"):
            event = await cd.check_rule(rule.rule_id)
        assert len(cd.get_history(rule.rule_id)) == 1
        assert cd.get_history(rule.rule_id)[0] is event


# ── ChangeDetector — save / load rules ────────────────────────────────

class TestSaveLoad:
    def test_save_and_load_roundtrip(self, tmp_path: Path) -> None:
        cd = ChangeDetector(data_dir=tmp_path)
        cd.add_rule(MonitorRule(url="https://a.com", name="A", rule_id="rid1"))
        cd.add_rule(MonitorRule(url="https://b.com", name="B", rule_id="rid2"))
        cd.save_rules()

        cd2 = ChangeDetector(data_dir=tmp_path)
        count = cd2.load_rules()
        assert count == 2
        assert len(cd2.list_rules()) == 2
        names = {r.name for r in cd2.list_rules()}
        assert names == {"A", "B"}

    def test_load_nonexistent_file(self, tmp_path: Path) -> None:
        cd = ChangeDetector(data_dir=tmp_path)
        count = cd.load_rules()
        assert count == 0

    def test_load_invalid_json(self, tmp_path: Path) -> None:
        cd = ChangeDetector(data_dir=tmp_path)
        rules_file = tmp_path / "monitor_rules.json"
        rules_file.write_text("not valid json")
        count = cd.load_rules()
        assert count == 0

    def test_save_custom_path(self, tmp_path: Path) -> None:
        cd = ChangeDetector()
        cd.add_rule(MonitorRule(url="https://example.com"))
        custom = tmp_path / "custom.json"
        cd.save_rules(custom)
        assert custom.exists()
        data = json.loads(custom.read_text(encoding="utf-8"))
        assert len(data) == 1
        assert data[0]["url"] == "https://example.com"
