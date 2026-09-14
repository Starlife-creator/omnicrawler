"""PDF 侧专项基准：数据集与门槛（**自动部分**）。

对应《优化方案》§5.2 #5 的下一项验收：在 **pdfx 侧**单独定「模糊抽取 / OCR」的数据集与门槛，
**不与精确结构化任务混分**。判据与打分函数在 `services/pdf_quality_benchmark.py`，
这里跑**真实管线**证明门槛可被达到、且**判据抓得住默认配置的后果**。

门槛（`PdfQualityScore.ok`）＝ **要么对，要么进复核**：
归一后字段准确、每个字段带页码与原文证据、置信度低于阈值的必须 `needs_review`、
不得有"错值被自动放行"。

环境不可达时**跳过并如实登记**：图片版需要中文字体与内置 tesseract。
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from omnicrawler.services.pdf_quality_benchmark import (
    PDF_CASES,
    PdfBenchmarkCase,
    cjk_font,
    results_rows,
    run_case,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
TESSERACT = REPO_ROOT / ".runtime" / "tesseract" / "tesseract.exe"

_DIGITAL = next(case for case in PDF_CASES if not case.needs_ocr)
_SCANNED = next(case for case in PDF_CASES if case.needs_ocr)


def _require_ocr_env() -> None:
    if cjk_font() is None:
        pytest.skip("缺少中文字体，无法生成图片版样本（该环境不可达）")
    if not TESSERACT.is_file():
        pytest.skip(f"未找到内置 tesseract：{TESSERACT}（该环境不可达）")


def _run(case: PdfBenchmarkCase, tmp_path: Path):
    return run_case(case, workdir=tmp_path, tesseract=TESSERACT)


def test_digital_pdf_meets_the_gate(tmp_path: Path) -> None:
    """数字版：原生文字层，三个字段都应与真值归一后逐字相等，且都在门槛内。"""
    score = _run(_DIGITAL, tmp_path)

    assert score.missing_fields == (), score.to_mapping()
    assert score.field_accuracy == 1.0, score.to_mapping()
    assert score.evidence_ratio == 1.0, "每个字段都要带页码与原文证据"
    assert score.review_violations == 0, "高置信不应无谓进复核"
    assert score.unreviewed_errors == 0
    assert score.ok is True, score.to_mapping()


def test_scanned_pdf_meets_the_gate_with_real_ocr(tmp_path: Path) -> None:
    """图片版（无文字层）：真走 OCR，且**在声明的抽取规则下**达到同一门槛。"""
    _require_ocr_env()
    score = _run(_SCANNED, tmp_path)

    assert score.kind == "scanned"
    assert score.missing_fields == (), score.to_mapping()
    # 逐字相等（不是"大致像"）：金额的千分位误读由结构判定恢复，汉字间空格由
    # 用例显式声明的 `collapse_whitespace` 归一 —— 两者都在真值侧可追溯。
    assert score.field_accuracy == 1.0, score.to_mapping()
    assert score.evidence_ratio == 1.0, score.to_mapping()
    assert score.review_violations == 0, score.to_mapping()
    assert score.unreviewed_errors == 0, score.to_mapping()
    assert score.ok is True, score.to_mapping()

    # 交付内容也要对得上（分数与产物一致，避免"分数好看、表里不对"）
    rows = results_rows(tmp_path, _SCANNED)
    assert len(rows) == 1, rows
    assert rows[0]["合同名称"] == "示例服务合同", rows[0]
    assert rows[0]["金额"] == "12345.67", rows[0]


def test_ocr_text_whitespace_default_is_flagged_by_the_gate(tmp_path: Path) -> None:
    """**特征化用例**：默认配置（不开 `collapse_whitespace`）下，OCR 汉字间空格会作为
    "错值自动放行"被本判据判失败 —— 记录当前行为，并写清升级路径。

    实测（2026-09-14）：`示例服务合同` 被 OCR 读成 `示例  服务  合同`，**置信度 0.98**、
    `auto_accepted`，于是原样进入交付值：值错 + 自动放行 ⇒ `ok=False`。
    产品**已有**开关（逐字段 `collapse_whitespace`，默认关，见
    `tests/unit/pdf/test_collapse_whitespace_option.py`）。

    **若将来把"OCR 源文本的空白归一"改成默认开，本用例会红 —— 那是好消息**：
    请同时更新开关默认、`pdf_quality_benchmark` 的用例声明与相关文档，而不是把断言改回绿。
    """
    _require_ocr_env()
    default_rules = dataclasses.replace(_SCANNED, name="scanned-default-rules", collapse_ocr_whitespace=False)
    score = _run(default_rules, tmp_path)

    assert score.ok is False, "默认配置下该伪影应被本判据抓住（若已改为默认归一，请按 docstring 同步）"
    assert score.unreviewed_errors == 1, score.to_mapping()
    assert score.unreviewed_error_fields == ("contract_name",), score.to_mapping()
    # 判据没有误伤其他字段：金额与编号是对的，且都在门槛内
    assert score.matched_fields == 2, score.to_mapping()
    assert score.review_violations == 0, "该伪影是高置信错值——所以不会「低置信必须进复核」那条兜住"
