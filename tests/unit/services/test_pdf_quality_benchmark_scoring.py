"""PDF 侧专项判据：每一类缺陷都必须让分数掉下来（**门槛＝要么对，要么进复核**）。

`score_pdf_records` 是纯函数（真值 + 观测 → 分数），因此不生成 PDF、不跑管线就能把
反例逐条测出来。这里同时钉住**判据的两个方向**：

* **要抓**：错了还自动放行、低置信不标复核、缺证据、缺字段；
* **不该误伤**：值错但**已进复核**（模糊抽取的正常终态）不算失败 ——
  门槛不是"必须 100% 正确"，而是"错不可怕，可怕的是错了还自动放行"。
"""

from __future__ import annotations

import pytest

from omnicrawler.services.pdf_quality_benchmark import (
    DEFAULT_MIN_CONFIDENCE,
    PDF_CASES,
    FieldObservation,
    score_pdf_records,
)

_TRUTH = {"contract_no": "HT-2026-0001", "contract_name": "示例服务合同", "amount": "12345.67"}


def _obs(
    name: str,
    value: str | None,
    *,
    page: int | None = 1,
    evidence: str = "原文片段",
    confidence: float = 0.99,
    review: str = "auto_accepted",
) -> FieldObservation:
    return FieldObservation(
        name=name,
        normalized=value,
        page_no=page,
        evidence=evidence,
        confidence=confidence,
        review_status=review,
    )


def _perfect() -> list[FieldObservation]:
    return [_obs(name, value) for name, value in _TRUTH.items()]


def test_perfect_run_scores_full_marks() -> None:
    score = score_pdf_records("case", "text_layer", _TRUTH, _perfect())
    assert score.field_accuracy == 1.0
    assert score.evidence_ratio == 1.0
    assert score.review_violations == 0
    assert score.unreviewed_errors == 0
    assert score.ok is True


def test_value_error_that_is_auto_accepted_fails_the_gate() -> None:
    """**自报高置信也不豁免**：值错 + 自动放行 ⇒ 直接判失败。"""
    observations = _perfect()
    observations[2] = _obs("amount", "1234567", confidence=1.0)  # 小数点被 OCR 吃掉
    score = score_pdf_records("case", "scanned", _TRUTH, observations)
    assert score.field_accuracy == pytest.approx(2 / 3)
    assert score.unreviewed_errors == 1
    assert score.unreviewed_error_fields == ("amount",)
    assert score.ok is False


def test_value_error_routed_to_review_is_accepted() -> None:
    """模糊抽取的正常出口：错了但**进了复核** ⇒ 门槛通过（不误伤）。"""
    observations = _perfect()
    observations[2] = _obs("amount", "1234567", confidence=0.55, review="needs_review")
    score = score_pdf_records("case", "scanned", _TRUTH, observations)
    assert score.field_accuracy == pytest.approx(2 / 3), "准确率如实反映（不粉饰）"
    assert score.unreviewed_errors == 0
    assert score.review_violations == 0
    assert score.ok is True, "错值进了复核就不该判失败——这正是门槛的语义"


def test_low_confidence_without_review_is_a_violation() -> None:
    """「低置信必须进复核」：0.4 < 0.9 却 auto_accepted ⇒ 违反。"""
    observations = _perfect()
    observations[0] = _obs("contract_no", "HT-2026-0001", confidence=0.4)
    score = score_pdf_records("case", "scanned", _TRUTH, observations)
    assert score.review_violations == 1
    assert score.ok is False


def test_min_confidence_threshold_is_configurable() -> None:
    """阈值可配：把门槛降到 0.3 后，0.4 的字段不再算"低置信"。"""
    observations = _perfect()
    observations[0] = _obs("contract_no", "HT-2026-0001", confidence=0.4)
    score = score_pdf_records("case", "scanned", _TRUTH, observations, min_confidence=0.3)
    assert score.review_violations == 0
    assert score.ok is True
    assert score.min_confidence == 0.3


def test_missing_evidence_fails_even_when_values_are_right() -> None:
    """值对但没有页码/原文证据 ⇒ 无从复核，判失败。"""
    observations = _perfect()
    observations[1] = _obs("contract_name", "示例服务合同", page=None, evidence="")
    score = score_pdf_records("case", "text_layer", _TRUTH, observations)
    assert score.field_accuracy == 1.0
    assert score.evidence_ratio == pytest.approx(2 / 3)
    assert score.ok is False


def test_missing_field_fails_even_when_others_are_perfect() -> None:
    observations = [_obs("contract_no", "HT-2026-0001"), _obs("contract_name", "示例服务合同")]
    score = score_pdf_records("case", "text_layer", _TRUTH, observations)
    assert score.missing_fields == ("amount",)
    assert score.ok is False


def test_empty_observations_score_zero() -> None:
    score = score_pdf_records("case", "text_layer", _TRUTH, [])
    assert score.field_accuracy == 0.0
    assert score.evidence_ratio == 0.0
    assert set(score.missing_fields) == set(_TRUTH)
    assert score.ok is False


def test_normalization_is_not_a_cover_up() -> None:
    """归一**不得**把不可判定的误读"抹平"：`12345.67` vs `1234567` 必须判错。

    这条是"别用归一掩盖误读"的机器化表达 —— 可判定形态（`12.345.67` 这类结构上唯一的
    千分位误读）由 `normalize_amount` 恢复；恢复不了的就不许算对。
    """
    from omnicrawler.pdfx.normalization import normalize_amount

    recovered, _ = normalize_amount("12.345.67 元")
    assert recovered == "12345.67", "可判定的千分位误读应被恢复（否则基准会误判为错）"

    lost, _ = normalize_amount("1234567 元")
    assert lost is not None and lost != "12345.67", "丢掉小数点的值不得被归一成真值"

    score = score_pdf_records("case", "scanned", _TRUTH, _perfect()[:2] + [_obs("amount", lost)])
    assert score.unreviewed_errors == 1
    assert score.ok is False


def test_score_mapping_rounds_floats_and_reports_the_gate() -> None:
    mapping = score_pdf_records("case", "text_layer", _TRUTH, _perfect()).to_mapping()
    assert mapping["ok"] is True
    assert mapping["field_accuracy"] == 1.0
    assert mapping["min_confidence"] == DEFAULT_MIN_CONFIDENCE


def test_builtin_cases_cover_both_sample_shapes() -> None:
    """覆盖面本身要被机器检查：数字版（文字层）与图片版（OCR）各至少一例。"""
    kinds = {case.kind for case in PDF_CASES}
    assert kinds == {"text_layer", "scanned"}
    assert any(case.needs_ocr for case in PDF_CASES), "缺 OCR 形态的样本"
    assert any(not case.needs_ocr for case in PDF_CASES), "缺数字版样本"
    for case in PDF_CASES:
        names = {name for name, _label, _kind, _pattern in case.field_specs}
        assert names == set(case.truth()), f"{case.name} 字段与真值不一致"
        assert case.filename, f"{case.name} 缺文件名"
