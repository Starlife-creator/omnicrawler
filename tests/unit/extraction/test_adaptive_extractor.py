"""Tests for extraction.adaptive_extractor — L3 失效检测→LLM→验证闭环."""

from __future__ import annotations

import json
from typing import Any

import pytest

pytest.importorskip("lxml")

from omnicrawl.core.models import ExtractedRecord  # noqa: E402
from omnicrawl.extraction.adaptive_extractor import (  # noqa: E402
    MAX_RULE_LENGTH,
    AdaptiveExtractor,
    RepairProposal,
)

_HTML = """<html><body>
<div class="card">
  <h2 class="name">商品A</h2>
  <span class="price">99.9</span>
</div>
<div class="card">
  <h2 class="name">商品B</h2>
  <span class="price">199.0</span>
</div>
<div class="card">
  <h2 class="name">商品C</h2>
  <span class="price">299.0</span>
</div>
</body></html>"""

_FIELDS: dict[str, Any] = {
    "title": {"selector": ".old-title"},
    "price": {"selector": ".old-price"},
}


def _record(data: dict[str, Any]) -> ExtractedRecord:
    return ExtractedRecord("https://example.com/item", "item", data)


def _failing_records() -> list[ExtractedRecord]:
    # 旧选择器全部失效 → 字段值全空
    return [_record({"title": "", "price": None}), _record({"title": "", "price": ""})]


def _mock_llm(selector: str = ".name", rule_type: str = "css") -> Any:
    def generate(prompt: str) -> str:
        return json.dumps({"rule_type": rule_type, "selector": selector})

    return generate


class TestDetectFailingFields:
    def test_all_failing(self) -> None:
        extractor = AdaptiveExtractor()
        assert extractor.detect_failing_fields(_failing_records(), _FIELDS) == ["title", "price"]

    def test_partial_failure_threshold(self) -> None:
        records = [_record({"title": "ok", "price": ""}), _record({"title": "", "price": ""})]
        extractor = AdaptiveExtractor(success_threshold=0.7)
        failing = extractor.detect_failing_fields(records, _FIELDS)
        assert "title" in failing  # 1/2 = 0.5 < 0.7 判失效
        assert "price" in failing

    def test_empty_records_all_failing(self) -> None:
        # 无记录 = 无成功证据，按 fail-closed 判全部失效
        extractor = AdaptiveExtractor()
        assert extractor.detect_failing_fields([], _FIELDS) == ["title", "price"]


class TestVerifyRule:
    def test_css_hits(self) -> None:
        extractor = AdaptiveExtractor()
        matches, samples = extractor.verify_rule(_HTML, ".name")
        assert matches == 3
        assert samples and "商品A" in samples[0]

    def test_xpath_hits(self) -> None:
        extractor = AdaptiveExtractor()
        matches, _ = extractor.verify_rule(_HTML, "//span[contains(@class,'price')]", "xpath")
        assert matches == 3

    def test_no_match(self) -> None:
        extractor = AdaptiveExtractor()
        matches, samples = extractor.verify_rule(_HTML, ".missing-class")
        assert matches == 0
        assert samples == []

    def test_bad_rule_returns_zero(self) -> None:
        extractor = AdaptiveExtractor()
        assert extractor.verify_rule(_HTML, "div[unclosed") == (0, [])
        assert extractor.verify_rule(_HTML, "") == (0, [])


class TestProposeRepairs:
    def test_no_failing_fields_returns_empty(self) -> None:
        records = [_record({"title": "A", "price": "1.0"}), _record({"title": "B", "price": "2.0"})]
        extractor = AdaptiveExtractor(llm_generate=_mock_llm())
        assert extractor.propose_repairs(_HTML, records, _FIELDS) == []

    def test_full_cycle_produces_verified_proposals(self) -> None:
        extractor = AdaptiveExtractor(llm_generate=_mock_llm(".name"))
        proposals = extractor.propose_repairs(_HTML, _failing_records(), _FIELDS)
        assert len(proposals) == 2
        assert all(isinstance(p, RepairProposal) for p in proposals)
        assert all(p.verified for p in proposals)
        assert all(p.matches >= 1 for p in proposals)
        names = {p.field for p in proposals}
        assert names == {"title", "price"}

    def test_unverified_proposal_rejected(self) -> None:
        # LLM 给出无命中的选择器 → 不产出建议（fail-closed）
        extractor = AdaptiveExtractor(llm_generate=_mock_llm(".does-not-exist"))
        proposals = extractor.propose_repairs(_HTML, _failing_records(), _FIELDS)
        assert proposals == []

    def test_llm_invalid_json_returns_empty(self) -> None:
        extractor = AdaptiveExtractor(llm_generate=lambda _prompt: "not json")
        assert extractor.propose_repairs(_HTML, _failing_records(), _FIELDS) == []

    def test_llm_unknown_keys_rejected(self) -> None:
        def generate(_prompt: str) -> str:
            return json.dumps({"rule_type": "css", "selector": ".name", "evil": "x"})

        extractor = AdaptiveExtractor(llm_generate=generate)
        assert extractor.propose_repairs(_HTML, _failing_records(), _FIELDS) == []

    def test_oversized_rule_rejected(self) -> None:
        extractor = AdaptiveExtractor(
            llm_generate=_mock_llm(".name" + "x" * MAX_RULE_LENGTH)
        )
        assert extractor.propose_repairs(_HTML, _failing_records(), _FIELDS) == []

    def test_no_ai_provider_returns_empty(self) -> None:
        extractor = AdaptiveExtractor()
        assert extractor.propose_repairs(_HTML, _failing_records(), _FIELDS) == []

    def test_rule_type_from_config(self) -> None:
        fields = {"title": {"xpath": "//h2[contains(@class,'name')]"}}
        records = [_record({"title": ""}), _record({"title": ""})]

        def generate(prompt: str) -> str:
            return json.dumps({"rule_type": "xpath", "selector": "//h2[contains(@class,'name')]"})

        extractor = AdaptiveExtractor(llm_generate=generate)
        proposals = extractor.propose_repairs(_HTML, records, fields)
        assert len(proposals) == 1
        assert proposals[0].rule_type == "xpath"
