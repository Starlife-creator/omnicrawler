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
# ── W3.1：页码真值 + 逐形态门槛 ─────────────────────────────────────────


def test_wrong_page_fails_even_when_the_value_is_right() -> None:
    """**取错页必须判失败**（W3.1）。

    值对、证据也在，但页码指向别处 ⇒ 人工复核会被引到错的地方；
    页码是证据的一部分，不能只当装饰。
    """
    observations = _perfect()
    observations[0] = _obs("contract_no", "HT-2026-0001", page=3)
    score = score_pdf_records(
        "case", "text_layer", _TRUTH, observations, expected_pages={"contract_no": 1, "amount": 1}
    )
    assert score.field_accuracy == 1.0, "值本身都对"
    assert score.page_mismatch_fields == ("contract_no",)
    assert score.ok is False, "页码错了就不该判达标"


def test_matching_pages_pass_and_are_recorded() -> None:
    pages = {"contract_no": 2, "contract_name": 2, "amount": 2}
    observations = [
        _obs("contract_no", "HT-2026-0001", page=2),
        _obs("contract_name", "示例服务合同", page=2),
        _obs("amount", "12345.67", page=2),
    ]
    score = score_pdf_records("case", "text_layer", _TRUTH, observations, expected_pages=pages)
    assert score.page_mismatch_fields == ()
    assert score.ok is True
    assert dict(score.expected_pages) == pages, "期望页码要能回读（便于排障）"
    assert score.to_mapping()["expected_pages"] == {name: 2 for name in pages}


def test_page_truth_is_opt_in_per_case() -> None:
    """不声明期望页码的形态**不校验**页码（只有多页形态才知道字段在第几页）。"""
    single_page_case = next(case for case in PDF_CASES if case.name == "digital-text-layer")
    assert single_page_case.expected_pages == (), "单页形态不该声明期望页码"

    multi_page_case = next(case for case in PDF_CASES if case.name == "multi-page-text-layer")
    assert dict(multi_page_case.expected_pages) == {
        "contract_no": 2, "contract_name": 2, "amount": 2
    }, "多页形态必须声明字段在第 2 页 —— 否则这条判据等于没上"


def test_shapes_cover_three_required_kinds_and_keep_own_thresholds() -> None:
    """三种新形态都在；且**门槛按形态设定、互不平均**。"""
    names = [case.name for case in PDF_CASES]
    for required in ("multi-page-text-layer", "table-text-layer", "low-quality-scan"):
        assert required in names, f"缺少形态 {required}"
    assert len(names) == len(set(names)), "用例名重复"

    low_quality = next(case for case in PDF_CASES if case.name == "low-quality-scan")
    assert low_quality.min_confidence < DEFAULT_MIN_CONFIDENCE, (
        "低质扫描的置信天花板天然更低 ⇒ 门槛应按它自己的现实设定，"
        "而不是套用文字层的门槛（那会让该形态永远判失败）"
    )
    # 别的形态不受它影响（互不平均）
    for case in PDF_CASES:
        if case.name != "low-quality-scan":
            assert case.min_confidence >= low_quality.min_confidence
