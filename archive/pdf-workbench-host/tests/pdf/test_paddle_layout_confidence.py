from __future__ import annotations

import math
from pathlib import Path

import pytest

from omnicrawler.pdfx.config import FieldSpec, ProjectConfig
from omnicrawler.pdfx.extraction import _observable_confidence, rule_extract_field
from omnicrawler.pdfx.ocr import _paddle_page_text
from omnicrawler.pdfx.retrieval import CandidatePage
from omnicrawler.pdfx.validation import validate_record


def test_paddle_wrapped_layout_retains_lines_and_scores() -> None:
    lines = ["编号：A-123", "名称：服务合同", "金额：100元"]
    content = "".join(lines)
    payload = {"res": {
        "overall_ocr_res": {"rec_texts": lines, "rec_scores": [.97, .99, .96],
                            "rec_boxes": [[0, 0, 100, 10], [0, 20, 100, 30], [0, 40, 100, 50]]},
        "parsing_res_list": [{"block_label": "text", "block_content": content,
                              "block_bbox": [0, 0, 100, 50]}],
    }}
    text, confidence = _paddle_page_text(payload, {"markdown_texts": content})
    assert text == "\n".join(lines)
    assert confidence == .96
    page = CandidatePage(1, text, 1.0, "ocr", confidence)
    value = rule_extract_field(FieldSpec(name="id", label="编号", patterns=[r"编号：(?P<value>[^\n]+)"]), "x.pdf", [page])
    assert value is not None and value["raw_value"] == "A-123"


def test_paddle_does_not_reorder_columns_or_replace_mismatched_blocks() -> None:
    payload = {"overall_ocr_res": {"rec_texts": ["other", "line"], "rec_scores": [.99, .95],
                                  "rec_boxes": [[200, 0, 220, 10], [0, 0, 100, 10]]},
               "parsing_res_list": [{"block_label": "text", "block_content": "otherline",
                                     "block_bbox": [0, 0, 100, 10]}]}
    text, _ = _paddle_page_text(payload, {"markdown_texts": "otherline"})
    assert text == "otherline"


@pytest.mark.parametrize("score", [None, float("nan"), float("inf"), -1, 1.1])
def test_missing_or_invalid_ocr_score_never_gets_pattern_certainty(score: float | None) -> None:
    page = CandidatePage(1, "编号：A-123", 1.0, "ocr", score)
    value = {"raw_value": "A-123", "page_no": 1, "extraction_method": "content_rule", "matched_by_pattern": True}
    confidence = _observable_confidence(value, {1: page})
    assert math.isfinite(confidence) and confidence < .9


def test_ocr_pattern_confidence_follows_source_quality() -> None:
    page = CandidatePage(1, "编号：A-123", 1.0, "ocr", .4,
                         {"words": [{"text": "编号：A-123", "confidence": .4}]})
    value = {"raw_value": "A-123", "page_no": 1, "extraction_method": "content_rule", "matched_by_pattern": True}
    assert _observable_confidence(value, {1: page}) == pytest.approx(.392)


def test_paddle_tables_and_legacy_regions_are_retained() -> None:
    html = "<table><tr><td>项目</td><td>值</td></tr><tr><td>甲</td><td>2</td></tr></table>"
    for payload in ({"res": {"table_res_list": [{"pred_html": html}]}},
                    {"res": [{"type": "table", "res": {"html": html}}]}):
        text, confidence = _paddle_page_text(payload, {})
        assert "| 甲 | 2 |" in text
        assert confidence is None
    text, _ = _paddle_page_text({"res": {"table_res_list": [{"pred_html": html}]}}, {"markdown_texts": html})
    assert text.count("<table>") == 1 and "| 甲 |" not in text


def _config() -> ProjectConfig:
    return ProjectConfig(path=Path("x.yaml"), project_name="t", input_dir=Path("in"),
                         work_dir=Path("work"), output_dir=Path("out"), database=Path("db"),
                         parser={}, ocr={}, retrieval={}, llm={}, extraction={}, normalization={},
                         validation={"auto_accept_confidence": .9},
                         fields=[FieldSpec(name="id", label="编号"), FieldSpec(name="name", label="名称")])


def test_ocr_field_uncertainty_cannot_be_hidden_by_record_average() -> None:
    values = {"id": {"raw_value": "A-123", "normalized_value": "A-123", "evidence": "编号：A-123",
                     "source_is_ocr": True, "confidence": .5}}
    result = validate_record(_config(), values, .98)
    assert result.review_status == "needs_review"
    assert any("置信度不足" in message for message in result.messages)


def test_concatenated_ocr_field_is_reviewed_even_with_high_scores() -> None:
    raw = "A-123名称：服务合同"
    values = {"id": {"raw_value": raw, "normalized_value": raw, "evidence": raw,
                     "source_is_ocr": True, "extraction_method": "content_rule", "confidence": .98}}
    result = validate_record(_config(), values, .98)
    assert result.review_status == "needs_review"
    assert any("串入" in message for message in result.messages)
