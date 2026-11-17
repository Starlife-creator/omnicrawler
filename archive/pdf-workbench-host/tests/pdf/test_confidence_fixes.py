"""Phase 3d 置信度真实化修复测试（D17/D22/D23/D31/D34/D46）。"""

from __future__ import annotations

from pathlib import Path

from omnicrawler.pdfx.config import FieldSpec, ProjectConfig
from omnicrawler.pdfx.extraction import (
    _collapse_ws,
    _merge_rules,
    _observable_confidence,
    rule_extract_field,
)
from omnicrawler.pdfx.llm import build_user_content
from omnicrawler.pdfx.retrieval import CandidatePage


def _config(fields: list[FieldSpec], extraction=None, llm=None) -> ProjectConfig:
    return ProjectConfig(
        path=Path("x.yaml"), project_name="t", input_dir=Path("in"), work_dir=Path("work"),
        output_dir=Path("out"), database=Path("db"), parser={}, ocr={}, retrieval={},
        llm=llm or {}, extraction=extraction or {}, normalization={},
        validation={"auto_accept_confidence": 0.9},
        fields=fields,
    )


def _page(text: str, page_no: int = 1) -> CandidatePage:
    return CandidatePage(page_no=page_no, text=text, score=1.0, parse_method="native", ocr_confidence=None)


def test_d22_pattern_high_confidence() -> None:
    spec = FieldSpec(name="code", label="代码", type="code", patterns=[r"(?P<value>\d{6})"])
    value = rule_extract_field(spec, "600519.pdf", [_page("股票代码为 600519 的公司")])
    assert value is not None
    assert value["matched_by_pattern"] is True
    assert _observable_confidence(value, {1: _page("股票代码为 600519 的公司")}) == 0.98


def test_d22_alias_low_confidence_without_evidence() -> None:
    spec = FieldSpec(name="amount", label="担保金额", type="amount", aliases=["担保金额"])
    # alias 兜底命中（带冒号）→ 一律低置信 0.55（进复核）
    value = rule_extract_field(spec, "a.pdf", [_page("担保金额：5000万元")])
    assert value is not None
    assert value["matched_by_pattern"] is False
    assert _observable_confidence(value, {1: _page("担保金额：5000万元")}) == 0.55


def test_d23_alias_requires_separator() -> None:
    spec = FieldSpec(name="amount", label="担保金额", type="amount", aliases=["担保金额"])
    # 无冒号/冒号分隔 → 不抓取（“担保金额 1000”无分隔符）
    assert rule_extract_field(spec, "a.pdf", [_page("担保金额 1000 万元")]) is None
    # 有冒号 → 抓取，且数值字段要求值含数字
    value = rule_extract_field(spec, "a.pdf", [_page("担保金额：1000万元")])
    assert value is not None
    assert value["raw_value"].startswith("1000")
    # 数值字段拒绝不含数字的兜底值
    assert rule_extract_field(spec, "a.pdf", [_page("担保金额：尚需股东大会审议")]) is None


def test_d31_multi_record_rules_not_shared() -> None:
    rules = {"amount": {"raw_value": "100", "extraction_method": "content_rule"}}
    records = [
        {"party": {"raw_value": "A"}},
        {"party": {"raw_value": "B"}},
    ]
    merged = _merge_rules(records, rules)
    # 多记录：content 规则值不回填（避免共享失真）
    assert "amount" not in merged[0]
    assert "amount" not in merged[1]
    # 单记录：回填并标记
    single = _merge_rules([{"party": {"raw_value": "A"}}], rules)
    assert single[0]["amount"]["shared_from_rules"] is True


def test_d34_whitespace_normalized_evidence() -> None:
    page = _page("担保金额：1 000 万元整")
    assert _collapse_ws("1 000 万元") in _collapse_ws(page.text)
    # 带多空格抄写也能匹配
    assert _collapse_ws("1  000 万元") in _collapse_ws(page.text)


def test_d46_truncation_marker() -> None:
    config = _config([], extraction={"max_chars_per_page": 10})
    prompt = build_user_content(config, "a.pdf", [_page("一二三四五六七八九十一二三四五")])
    assert "内容已截断" in str(prompt)
    short_prompt = build_user_content(config, "a.pdf", [_page("短文本")])
    assert "内容已截断" not in str(short_prompt)
