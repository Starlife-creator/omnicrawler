"""Phase 3a 配置/模板校验修复测试（D25/D26/D27/D28/D29/D30）。"""

from __future__ import annotations

from pathlib import Path

import pytest

from omnicrawl.pdfx.config import FieldSpec, ProjectConfig, load_config
from omnicrawl.pdfx.templates import builtin_pdf_resource
from omnicrawl.pdfx.validation import validate_record


def _config(fields: list[FieldSpec]) -> ProjectConfig:
    return ProjectConfig(
        path=Path("x.yaml"), project_name="t", input_dir=Path("in"), work_dir=Path("work"),
        output_dir=Path("out"), database=Path("db"), parser={}, ocr={}, retrieval={},
        llm={}, extraction={}, normalization={},
        validation={"auto_accept_confidence": 0.9},
        fields=fields,
    )


def _validate(spec: FieldSpec, normalized: str, raw: str | None = None) -> object:
    raw = raw or normalized
    config = _config([spec])
    return validate_record(
        config,
        {spec.name: {"raw_value": raw, "normalized_value": normalized, "evidence": "证据原文"}},
        0.5,
    )


def test_d25_minimum_string_is_converted_to_float() -> None:
    spec = FieldSpec.from_dict({
        "name": "amount", "label": "金额", "type": "amount", "minimum": "0", "maximum": "100"
    })
    assert spec.minimum == 0.0
    assert spec.maximum == 100.0


def test_d25_invalid_minimum_rejected() -> None:
    with pytest.raises(ValueError, match="minimum"):
        FieldSpec.from_dict({"name": "a", "label": "A", "type": "amount", "minimum": "abc"})


def test_d26_target_unit_requires_numeric_type() -> None:
    with pytest.raises(ValueError, match="target_unit"):
        FieldSpec.from_dict({"name": "a", "label": "A", "type": "text", "target_unit": "万元"})


def test_d27_allowed_values_requires_enum_type() -> None:
    with pytest.raises(ValueError, match="allowed_values"):
        FieldSpec.from_dict({"name": "a", "label": "A", "type": "text", "allowed_values": ["x"]})


def test_d27_enum_type_accepted() -> None:
    spec = FieldSpec.from_dict({"name": "r", "label": "R", "type": "enum", "allowed_values": ["x"]})
    assert spec.type == "enum"


def test_d29_year_range_validation() -> None:
    spec = FieldSpec(name="y", label="年份", type="year")
    result = _validate(spec, "2099")
    # 2099 超过今年 → invalid
    assert result.status == "invalid"


def test_d28_code_value_pattern_rejects_bad_code() -> None:
    spec = FieldSpec(
        name="code", label="代码", type="code",
        value_pattern=r"^(00[013]\d{3}|30[01]\d{3}|60[0135]\d{3}|688\d{3}|8\d{5}|4\d{5})$",
    )
    result = _validate(spec, "202412")
    assert result.status == "invalid"
    result_ok = _validate(spec, "600519")
    assert result_ok.status != "invalid" or "白名单" not in "".join(result_ok.messages)


def test_d30_allowed_values_violation_is_invalid() -> None:
    spec = FieldSpec(name="rel", label="关系", type="enum", allowed_values=["全资子公司", "控股子公司"])
    result = _validate(spec, "非全资子公司")
    assert result.status == "invalid"
    assert any("不在允许值" in m for m in result.messages)


def test_d32_cross_check_sum_equal() -> None:
    spec_a = FieldSpec(name="a", label="期初")
    spec_b = FieldSpec(name="b", label="变动")
    spec_c = FieldSpec(name="c", label="期末")
    config = _config([spec_a, spec_b, spec_c])
    config.validation = {"auto_accept_confidence": 0.9, "cross_checks": [
        {"type": "sum_equal", "fields": ["a", "b", "c"], "message": "期初+变动应等于期末"},
    ]}
    values = {
        "a": {"raw_value": "100", "normalized_value": "100", "evidence": "e"},
        "b": {"raw_value": "20", "normalized_value": "20", "evidence": "e"},
        "c": {"raw_value": "121", "normalized_value": "121", "evidence": "e"},
    }
    result = validate_record(config, values, 0.5)
    assert result.status == "invalid"
    assert any("勾稽" in m for m in result.messages)
    # 勾稽一致则无此错误
    values["c"]["normalized_value"] = "120"
    assert validate_record(config, values, 0.5).status != "invalid"


def test_d32_cross_check_less_equal() -> None:
    spec_a = FieldSpec(name="amount", label="单笔")
    spec_b = FieldSpec(name="limit", label="额度")
    config = _config([spec_a, spec_b])
    config.validation = {"auto_accept_confidence": 0.9, "cross_checks": [
        {"type": "less_equal", "field": "amount", "max_field": "limit", "message": "单笔不超过额度"},
    ]}
    values = {
        "amount": {"raw_value": "500", "normalized_value": "500", "evidence": "e"},
        "limit": {"raw_value": "300", "normalized_value": "300", "evidence": "e"},
    }
    assert validate_record(config, values, 0.5).status == "invalid"


def test_announcement_template_loads_with_new_types() -> None:
    """D26/D27/D28 模板补 type 后必须能通过 FieldSpec 校验并成功加载。"""
    config = load_config(builtin_pdf_resource("announcement_fields.yaml"))
    fields = config.field_map()
    assert fields["guarantee_amount"].type == "amount"
    assert fields["relationship"].type == "enum"
    assert fields["publisher_stock_code"].type == "code"
    assert fields["publisher_stock_code"].value_pattern is not None


def test_announcement_template_still_loads() -> None:
    config = load_config(builtin_pdf_resource("announcement_fields.yaml"))
    assert config.validation.get("auto_accept_confidence") is not None


def test_d19_reviewed_document_skipped_in_extract(monkeypatch) -> None:
    """Blocking 回归：含 human_accepted 记录的文档不自动重抽（避免 record_id 冲突）。"""
    from omnicrawl.pdfx.extraction import extract_document

    config = _config([])
    config.validation = {"auto_accept_confidence": 0.9}
    db = type("DB", (), {
        "fetchone": lambda self, sql, params=(): (
            {"n": 1} if "review_status='human_accepted'" in sql else None
        ),
    })()
    row = {"doc_id": "doc1", "filename": "a.pdf", "primary_path": "a.pdf"}

    def boom(*args, **kwargs):
        raise AssertionError("不应走到候选页选择（复核文档应提前跳过）")

    monkeypatch.setattr("omnicrawl.pdfx.extraction.select_candidates", boom)
    assert extract_document(config, db, row, None, None) == 0
