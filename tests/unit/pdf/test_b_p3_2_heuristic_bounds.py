"""B-P3-2：启发式边界加固回归测试。

覆盖 validation.py：年份下限、6 位代码、勾稽容差；
normalization.py：日期上下限、比率/百分数上下文区分、英文别名词边界、加长否定词窗口。
"""

from __future__ import annotations

from pathlib import Path

from omnicrawler.pdfx.config import FieldSpec, ProjectConfig
from omnicrawler.pdfx.normalization import normalize_date, normalize_percent, normalize_value
from omnicrawler.pdfx.validation import validate_record


def _config(fields: list[FieldSpec], *, validation: dict | None = None) -> ProjectConfig:
    return ProjectConfig(
        path=Path("x.yaml"), project_name="t", input_dir=Path("in"), work_dir=Path("work"),
        output_dir=Path("out"), database=Path("db"), parser={}, ocr={}, retrieval={},
        llm={}, extraction={}, normalization={},
        validation=validation or {"auto_accept_confidence": 0.9},
        fields=fields,
    )


def _values(normalized: str, raw: str | None = None) -> dict[str, dict[str, object]]:
    return {"f": {"raw_value": raw or normalized, "normalized_value": normalized, "evidence": "原文"}}


def test_year_below_lower_bound_rejected() -> None:
    spec = FieldSpec(name="f", label="年份", type="year")
    result = validate_record(_config([spec]), _values("1899"), 0.5)
    assert result.status == "invalid"
    assert any("超出合理范围" in message for message in result.messages)


def test_year_in_allowed_range_passes() -> None:
    spec = FieldSpec(name="f", label="年份", type="year")
    result = validate_record(_config([spec]), _values("1990"), 0.5)
    assert result.status == "valid"


def test_code_must_be_six_digits() -> None:
    spec = FieldSpec(name="f", label="代码", type="code")
    for bad in ("12345", "1234567", "abc123"):
        result = validate_record(_config([spec]), _values(bad), 0.5)
        assert result.status == "invalid", bad
    good = validate_record(_config([spec]), _values("123456"), 0.5)
    assert good.status == "valid"


def test_cross_check_tolerance_boundary() -> None:
    spec = FieldSpec(name="f", label="字段", type="text")
    config = _config(
        [spec],
        validation={"cross_checks": [
            {"type": "sum_equal", "fields": ["a", "b", "c"], "message": "合计勾稽"},
        ]},
    )
    # 30.002 + 30.002 = 60.004，差 0.004 在 0.005 容差内 → 不报错
    within = validate_record(config, {
        "a": {"normalized_value": "30.002", "evidence": "x"},
        "b": {"normalized_value": "30.002", "evidence": "x"},
        "c": {"normalized_value": "60.004", "evidence": "x"},
    }, 0.5)
    assert within.status != "invalid"
    # 60.02 差 0.016 超容差 → invalid
    beyond = validate_record(config, {
        "a": {"normalized_value": "30.002", "evidence": "x"},
        "b": {"normalized_value": "30.002", "evidence": "x"},
        "c": {"normalized_value": "60.02", "evidence": "x"},
    }, 0.5)
    assert beyond.status == "invalid"
    assert any("勾稽失败" in message for message in beyond.messages)


def test_date_rejects_old_and_future() -> None:
    assert normalize_date("1899年5月1日") == (None, None)
    assert normalize_date("2099年5月1日") == (None, None)
    assert normalize_date("2024年5月20日") == ("2024-05-20", None)


def test_percent_ratio_context_not_multiplied() -> None:
    # 比率上下文：0–1 视为比率，不再 ×100
    assert normalize_percent("比例0.5") == ("0.5", "%")
    assert normalize_percent("占比 0.5") == ("0.5", "%")
    # 无上下文的 0.5 按百分数 ×100；带 % 原值保留
    assert normalize_percent("0.5") == ("50", "%")
    assert normalize_percent("50%") == ("50", "%")


def test_english_alias_word_boundary() -> None:
    spec = FieldSpec(name="r", label="R", type="enum",
                     value_aliases={"subsidiary": ["subsidiary"]})
    assert normalize_value("subsidiary", spec)[0] == "subsidiary"
    # 嵌入更长 token 不命中
    assert normalize_value("subsidiaryx", spec)[0] == "subsidiaryx"


def test_long_negation_window_blocks_relation() -> None:
    spec = FieldSpec(
        name="r", label="R", type="enum",
        value_aliases={"全资子公司": ["全资子公司"], "控股子公司": ["控股子公司"]},
    )
    for text in ("非全资子公司", "并非控股子公司", "没有控股子公司", "不是全资子公司"):
        assert normalize_value(text, spec)[0] == text
    assert normalize_value("控股子公司", spec)[0] == "控股子公司"
