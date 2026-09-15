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


def test_ocr_text_whitespace_is_normalized_by_source_by_default(tmp_path: Path) -> None:
    """**新契约（§5.8 #24，2026-09-15）**：不声明任何开关，OCR 页的文本值也按来源归一 ⇒ 达标。

    **历史**（同一条用例此前记录的是坏行为）：该伪影曾**原样进入交付值**并被 `auto_accepted`
    （实测置信度 0.98）⇒ 在本判据下就是「错值自动放行」。当时的处置是"图片版用例显式声明
    `collapse_whitespace`"，把修复责任推给配置；现在改为**按来源默认生效**（OCR 页才归一，
    原生文字层不动，显式 `false` 仍可关掉）。

    因此本条现在守的是：**默认就对**。若有人把"按来源默认归一"改回"默认关"，它会立刻红。
    """
    _require_ocr_env()
    score = _run(_SCANNED, tmp_path)  # 用例本身**不声明**任何开关

    assert score.field_accuracy == 1.0, score.to_mapping()
    assert score.unreviewed_errors == 0, score.to_mapping()
    assert score.review_violations == 0, score.to_mapping()
    assert score.ok is True, score.to_mapping()

    # 交付值也要对得上：汉字之间的 OCR 空格应已被归一
    rows = results_rows(tmp_path, _SCANNED)
    assert len(rows) == 1, rows
    assert rows[0]["合同名称"] == "示例服务合同", rows[0]
    # 归一不动证据：原始值仍保留
    assert rows[0]["合同名称_原始值"], "原始值必须保留（归一不改证据）"
