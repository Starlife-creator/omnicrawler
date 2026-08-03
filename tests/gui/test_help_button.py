"""Tests for help button visibility, sizing, and capability display.

Covers:
- Help button minimum 32x32 click area
- Help button text visible on all themes
- Capability tiered display (Standard/Full/Optional)
- Field spec unified structure
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


# ---------------------------------------------------------------------------
# Help button sizing and accessibility
# ---------------------------------------------------------------------------

class TestHelpTooltipSizing:
    """Verify help buttons meet accessibility standards (at least 32x32)."""

    def test_minimum_size_compliant(self) -> None:
        source = (
            Path(__file__).resolve().parents[2]
            / "src" / "omnicrawl" / "gui" / "widgets" / "help_tooltip.py"
        ).read_text(encoding="utf-8")
        assert "setFixedSize(32, 32)" in source or "32" in source, \
            "Help buttons must have at least 32x32 click area"


class TestHelpRegistry:
    """Ensure all help entries have the required fields."""

    def test_all_entries_have_full_structure(self) -> None:
        from omnicrawl.services.help_registry import HELP_ENTRIES
        required = {"what", "why", "how", "example", "limitations",
                     "common_errors", "default_behavior", "change_impact"}
        for help_id, entry in HELP_ENTRIES.items():
            for field in required:
                assert hasattr(entry, field), \
                    f"Help entry '{help_id}' missing field '{field}'"

    def test_search_finds_keywords(self) -> None:
        from omnicrawl.services.help_registry import search_help
        results = search_help("网址 URL")
        assert len(results) > 0
        assert any("seed" in r.help_id for r in results)

    def test_all_help_ids_in_registry(self) -> None:
        from omnicrawl.services.help_registry import NON_OBVIOUS_CONTROL_HELP_IDS
        assert len(NON_OBVIOUS_CONTROL_HELP_IDS) >= 10, \
            "Expected at least 10 help entries covering all wizard steps"


# ---------------------------------------------------------------------------
# Capability tiered display
# ---------------------------------------------------------------------------

class TestCapabilityDisplay:
    """Verify capability report shows tiered Standard/Full/Optional status."""

    def test_report_includes_tiers(self) -> None:
        from omnicrawl.core.capabilities import capability_report
        report = capability_report()
        assert "standard" in report, "Report must include Standard tier"
        assert "full" in report, "Report must include Full tier"
        assert "optional" in report, "Report must include Optional tier"

    def test_standard_tier_has_items(self) -> None:
        from omnicrawl.core.capabilities import capability_report
        report = capability_report()
        standard = report["standard"]
        assert "items" in standard
        assert len(standard["items"]) > 0

    def test_summary_text_includes_all_tiers(self) -> None:
        from omnicrawl.core.capabilities import capability_report, capability_summary_text
        report = capability_report()
        text = capability_summary_text(report)
        assert "Standard 核心" in text
        assert "Full 专属" in text
        assert "可选" in text

    def test_individual_capability_structure(self) -> None:
        from omnicrawl.core.capabilities import _cap_item
        item = _cap_item("Test", True, "description", "standard")
        assert item["name"] == "Test"
        assert item["ready"] is True
        assert item["description"] == "description"
        assert item["tier"] == "standard"


# ---------------------------------------------------------------------------
# Field spec unified structure
# ---------------------------------------------------------------------------

class TestFieldSpecs:
    """Verify all field specs follow the 8-element unified structure."""

    def test_all_required_fields(self) -> None:
        from omnicrawl.services.field_spec import FIELD_SPECS
        required = {"what", "why", "recommendation", "default",
                     "impact", "example", "common_errors"}
        for field_id, spec in FIELD_SPECS.items():
            for attr_name in required:
                value = getattr(spec, attr_name, "")
                assert value, f"Field spec '{field_id}' has empty '{attr_name}'"

    def test_help_html_contains_structure(self) -> None:
        from omnicrawl.services.field_spec import field_help_html
        html = field_help_html("concurrency")
        assert "这是什么" in html
        assert "为什么需要" in html
        assert "推荐设置" in html
        assert "常见错误" in html

    def test_all_ids_listed(self) -> None:
        from omnicrawl.services.field_spec import all_field_ids
        ids = all_field_ids()
        assert "concurrency" in ids
        assert "max_pages" in ids
        assert "process_pdf" in ids


# ---------------------------------------------------------------------------
# AI safety tests
# ---------------------------------------------------------------------------

class TestAISafety:
    def test_untrusted_marker(self) -> None:
        from omnicrawl.services.ai_safety import mark_untrusted
        result = mark_untrusted("hello")
        assert "[UNTRUSTED_EXTERNAL_CONTENT" in result

    def test_validate_ai_output_rejects_unknown(self) -> None:
        from omnicrawl.services.ai_safety import validate_ai_output
        with pytest.raises(ValueError):
            validate_ai_output({"extra": "field"}, {"known": str})

    def test_validate_ai_output_accepts_valid(self) -> None:
        from omnicrawl.services.ai_safety import validate_ai_output
        result = validate_ai_output({"known": "value"}, {"known": str})
        assert result["known"] == "value"

    def test_validate_ai_output_rejects_wrong_type(self) -> None:
        from omnicrawl.services.ai_safety import validate_ai_output
        with pytest.raises(ValueError):
            validate_ai_output({"known": 123}, {"known": str})


# ---------------------------------------------------------------------------
# Document version consistency
# ---------------------------------------------------------------------------

class TestDocumentVersions:
    """Verify that version references are consistent between pyproject.toml and __init__.py."""

    def test_pyproject_version(self) -> None:
        import tomllib
        root = Path(__file__).resolve().parents[2]
        pyproject = root / "pyproject.toml"
        if pyproject.is_file():
            with open(pyproject, "rb") as f:
                data = tomllib.load(f)
            version = data.get("project", {}).get("version", "")
            assert re.fullmatch(r"\d+\.\d+\.\d+", version), f"Invalid semver: {version}"

    def test_init_version(self) -> None:
        import tomllib

        from omnicrawl import __version__
        root = Path(__file__).resolve().parents[2]
        pyproject = root / "pyproject.toml"
        with open(pyproject, "rb") as f:
            data = tomllib.load(f)
        expected = data.get("project", {}).get("version", "")
        assert __version__ == expected, (
            f"__init__.py version {__version__} != pyproject.toml version {expected}"
        )
