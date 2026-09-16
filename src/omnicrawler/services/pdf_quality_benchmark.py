"""PDF 侧质量基准：模糊抽取 / OCR 的**专项**判据与门槛（与 crawler 侧分开记分）。

## 为什么单开一份，不与 `services/quality_benchmark.py` 混分

crawler 侧的任务是**精确结构化**：选择器 / JSONPath 取到的字符串与真值逐字相等才算命中。
PDF/OCR 侧是**模糊抽取**：识别器必然引入差异（汉字间插空格、全角半角、千分位被读成小数点），
用精确标准要求它等于"拿错尺子"；反过来把它的宽松口径搬进 crawler 基准，
会把"看起来合理却错误"的值放行 —— 实测先例：OCR 把 `12,345.67` 读成 `12.345.67`，
旧正则只匹配出 `12.345`，**一个像对其实错的值**。

⇒ 两个基准**分开**：数据集、口径、门槛各自独立，**不混分**。本模块只回答
「模糊抽取抽到了什么」与「该复核的有没有真进复核」。

## 判据（每条都能被反例推翻）

| 口径 | 含义 |
|---|---|
| **归一后比对** | 用**产品自己的归一**（:func:`omnicrawler.pdfx.normalization.normalize_value`）比较，逐字相等才算命中 |
| **证据率** | 每个字段都要带页码与原文证据 —— 否则人工复核无从下手 |
| **低置信必须进复核** | 置信度低于阈值的字段**必须** `needs_review`（阈值即 `validation.auto_accept_confidence`） |
| **错值不得自动放行** | 值不匹配**且**被 `auto_accepted` 的字段直接判失败；**自报高置信不豁免** |

合起来是一条门槛：**要么对，要么进复核**。这正是"模糊"与"精确"该有的分别 ——
不影响可用性（低置信仍交付，只是标注待复核），但绝不允许"错了还自动放行"。

## 数据集（程序自造，零第三方内容）

* `digital`：reportlab 生成的**原生文字层** PDF（不需要 OCR）；
* `scanned`：PIL 渲染成图再存 PDF（**无文字层**）⇒ 逼出真实 OCR。

图片版需要中文字体与内置 tesseract；缺任一则跳过整条图片版验收（**如实登记，不假装通过**）。
"""

from __future__ import annotations

import contextlib
import csv
import io
import json
import sqlite3
import sys
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..pdfx.normalization import normalize_amount

#: 真值（图片版 OCR 也会被归一到这些值）
CONTRACT_NO = "HT-2026-0001"
CONTRACT_NAME = "示例服务合同"
AMOUNT_RAW = "12,345.67 元"
AMOUNT_NORMALIZED = "12345.67"

#: 与 `pdfx.validation` 的默认值保持一致（`validation.auto_accept_confidence`）
DEFAULT_MIN_CONFIDENCE = 0.90


@dataclass(frozen=True, slots=True)
class FieldObservation:
    """一个字段在一次真实运行里的观测结果（打分只吃这个，不碰 I/O）。"""

    name: str
    normalized: str | None
    page_no: int | None
    evidence: str | None
    confidence: float
    review_status: str
    raw_value: str | None = None

    @property
    def has_evidence(self) -> bool:
        return bool(self.page_no) and bool((self.evidence or "").strip())


@dataclass(frozen=True, slots=True)
class PdfQualityScore:
    """一次 PDF 任务的质量得分。``ok`` 的门槛＝**要么对，要么进复核**。"""

    case: str
    kind: str
    expected_fields: int
    observed_fields: int
    matched_fields: int
    field_accuracy: float
    evidence_ratio: float
    #: 置信度低于阈值却没进复核的字段数（违反「低置信必须进复核」）
    review_violations: int
    #: 值不匹配**且**被自动放行的字段数（违反「错值不得自动放行」）
    unreviewed_errors: int
    missing_fields: tuple[str, ...]
    unreviewed_error_fields: tuple[str, ...] = ()
    #: W3.1：取到**非真值页**的字段（页码是证据的一部分 —— 取错页等于抽错位置）
    page_mismatch_fields: tuple[str, ...] = ()
    #: 该形态的期望页码（字段 → 页号）；为空表示该形态不校验页码
    expected_pages: tuple[tuple[str, int], ...] = ()
    min_confidence: float = DEFAULT_MIN_CONFIDENCE
    environment: tuple[tuple[str, str], ...] = ()

    @property
    def ok(self) -> bool:
        return (
            not self.missing_fields
            and self.evidence_ratio >= 1.0
            and self.review_violations == 0
            and self.unreviewed_errors == 0
            and not self.page_mismatch_fields
        )

    def to_mapping(self) -> dict[str, Any]:
        return {
            "case": self.case,
            "kind": self.kind,
            "expected_fields": self.expected_fields,
            "observed_fields": self.observed_fields,
            "matched_fields": self.matched_fields,
            "field_accuracy": round(self.field_accuracy, 4),
            "evidence_ratio": round(self.evidence_ratio, 4),
            "review_violations": self.review_violations,
            "unreviewed_errors": self.unreviewed_errors,
            "missing_fields": list(self.missing_fields),
            "unreviewed_error_fields": list(self.unreviewed_error_fields),
            "page_mismatch_fields": list(self.page_mismatch_fields),
            "expected_pages": {str(name): int(page) for name, page in self.expected_pages},
            "min_confidence": self.min_confidence,
            "ok": self.ok,
            "environment": dict(self.environment),
        }


def _is_reviewed(status: str) -> bool:
    return str(status or "").strip().lower() in {"needs_review", "human_accepted"}


def score_pdf_records(
    case: str,
    kind: str,
    truth: Mapping[str, str],
    observations: Iterable[FieldObservation],
    *,
    min_confidence: float = DEFAULT_MIN_CONFIDENCE,
    expected_pages: Mapping[str, int] | None = None,
) -> PdfQualityScore:
    """把一次运行的观测结果与真值比对（纯函数）。

    "对"的定义是**归一后逐字相等**：真值与观测都已过产品归一，因此
    「千分位被 OCR 读成小数点」这类可判定形态会被恢复后判对，而**不可判定**的
    误读**不会**因为"看起来差不多"被判对。

    ``expected_pages``（W3.1）给定时，每个字段还必须取自**真值页** ——
    取错页与取错值同级：证据页码是人工复核的入口，指错页等于让人去错地方核对。
    """
    by_name = {item.name: item for item in observations}
    missing = tuple(name for name in truth if name not in by_name)

    matched = 0
    evidence_ok = 0
    review_violations = 0
    unreviewed_errors: list[str] = []

    pages = dict(expected_pages or {})
    page_mismatches: list[str] = []

    for name, expected in truth.items():
        item = by_name.get(name)
        if item is None:
            continue
        if item.has_evidence:
            evidence_ok += 1
        if name in pages and item.page_no != pages[name]:
            page_mismatches.append(name)
        if item.confidence < min_confidence and not _is_reviewed(item.review_status):
            review_violations += 1
        if item.normalized == expected:
            matched += 1
        elif not _is_reviewed(item.review_status):
            # 错了却自动放行 —— 这是本判据要抓的核心失败
            unreviewed_errors.append(name)

    expected_count = len(truth) or 1
    return PdfQualityScore(
        case=case,
        kind=kind,
        expected_fields=len(truth),
        observed_fields=len(by_name),
        matched_fields=matched,
        field_accuracy=matched / expected_count,
        evidence_ratio=evidence_ok / expected_count,
        review_violations=review_violations,
        unreviewed_errors=len(unreviewed_errors),
        missing_fields=missing,
        unreviewed_error_fields=tuple(unreviewed_errors),
        page_mismatch_fields=tuple(page_mismatches),
        expected_pages=tuple(sorted(pages.items())),
        min_confidence=min_confidence,
    )


# ── 归一：与产品同一把尺子 ───────────────────────────────────────────────


def normalize_amount_truth(truth: Mapping[str, str]) -> dict[str, str]:
    """把真值里的金额按产品归一口径转换（避免基准自造第二套数值规则）。"""
    normalized, _unit = normalize_amount(truth["amount"])
    assert normalized is not None, f"真值金额无法归一：{truth['amount']!r}"
    return {"contract_no": truth["contract_no"], "contract_name": truth["contract_name"], "amount": normalized}


# ── 样本：程序自造，零第三方内容 ─────────────────────────────────────────

#: 找不到中文字体时的候选（图片版必需；缺则跳过该形态）
CJK_FONT_CANDIDATES = (
    Path("C:/Windows/Fonts/msyh.ttc"),
    Path("C:/Windows/Fonts/simsun.ttc"),
    Path("C:/Windows/Fonts/simhei.ttf"),
)


def cjk_font() -> Path | None:
    """可用于渲染中文样本的字体（找不到返回 None，由调用方决定降级或跳过）。"""
    return next((item for item in CJK_FONT_CANDIDATES if item.is_file()), None)


def make_digital_pdf(path: Path) -> None:
    """数字版样本：reportlab 生成，**带原生文字层**。"""
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

    pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
    path.parent.mkdir(parents=True, exist_ok=True)
    style = ParagraphStyle("cn", fontName="STSong-Light", fontSize=12, leading=18)
    SimpleDocTemplate(str(path), pagesize=A4).build(
        [
            Paragraph("Sample Service Contract / 示例服务合同", style),
            Spacer(1, 6 * mm),
            Paragraph(f"合同编号：{CONTRACT_NO}", style),
            Paragraph(f"合同名称：{CONTRACT_NAME}", style),
            Paragraph(f"金额：{AMOUNT_RAW}", style),
        ]
    )


def make_multi_page_pdf(path: Path, *, pages: int = 3, fields_on_page: int = 2) -> None:
    """多页样本：**关键字段在后续页**，页码真值因此才有意义（W3.1）。

    单页样本里"页码对不对"根本测不出来 —— 所有字段都在第 1 页，取错页也无从发生。
    """
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont
    from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer

    pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
    path.parent.mkdir(parents=True, exist_ok=True)
    style = ParagraphStyle("cn", fontName="STSong-Light", fontSize=12, leading=18)
    story: list[Any] = [
        Paragraph("Sample Service Contract / 示例服务合同", style),
        Spacer(1, 6 * mm),
        Paragraph("本页为封面与目录，不含待抽取字段。", style),
    ]
    for page in range(2, pages + 1):
        story.append(PageBreak())
        if page == fields_on_page:
            story += [
                Paragraph(f"合同编号：{CONTRACT_NO}", style),
                Paragraph(f"合同名称：{CONTRACT_NAME}", style),
                Paragraph(f"金额：{AMOUNT_RAW}", style),
            ]
        else:
            story.append(Paragraph(f"第 {page} 页：附件条款说明。", style))
    SimpleDocTemplate(str(path), pagesize=A4).build(story)


def make_table_pdf(path: Path) -> None:
    """表格样式的数字版样本（字段在**表格单元格**里）。

    真实合同常把要素放进表格；表格文本层的行序/空白与段落不同，
    抽取规则必须同样成立（W3.1 的形态之一）。
    """
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
    path.parent.mkdir(parents=True, exist_ok=True)
    style = ParagraphStyle("cn", fontName="STSong-Light", fontSize=12, leading=18)
    # ★ 实测发现（2026-09-16）：把"标签"和"值"分放在两列时，文本层里两者是**分开的**
    # （没有冒号连接），抽取规则匹配不上 ⇒ 表格形态要用"整段说明在单元格里"的真实排布。
    # ★ 实测发现（2026-09-16）：分列 + 占位符会被**跨列读进值里**（实测得到
    # `HT-2026—-0001`、`示例服务—合同`）⇒ 表格形态用"说明列 + 空列"的真实排布，
    # 不塞任何占位字符。
    rows = [
        ["要素说明", "备注"],
        [f"合同编号：{CONTRACT_NO}", ""],
        [f"合同名称：{CONTRACT_NAME}", ""],
        [f"金额：{AMOUNT_RAW}", ""],
    ]
    table = Table(rows, colWidths=[30 * mm, 110 * mm])
    table.setStyle(TableStyle([("FONTNAME", (0, 0), (-1, -1), "STSong-Light"), ("GRID", (0, 0), (-1, -1), 0.4, "grey")]))
    SimpleDocTemplate(str(path), pagesize=A4).build(
        [Paragraph("Sample Service Contract / 示例服务合同", style), Spacer(1, 6 * mm), table]
    )


def make_low_quality_scan_pdf(path: Path, font: Path) -> None:
    """**低质**扫描样本：低分辨率渲染 + 噪声 + 轻度模糊（W3.1 的第三形态）。

    与 `make_image_only_pdf` 的区别是"真的难"：整段文字被降采样、加噪、糊化，
    OCR 必然产生低置信结果 —— 用来验证「低置信必须进复核」不是纸面条款。
    """
    import random

    from PIL import Image, ImageDraw, ImageFilter, ImageFont

    hi = Image.new("RGB", (900, 300), "white")
    draw = ImageDraw.Draw(hi)
    drawer = ImageFont.truetype(str(font), 34)
    draw.text((30, 30), f"合同编号：{CONTRACT_NO}", fill="black", font=drawer)
    draw.text((30, 110), f"合同名称：{CONTRACT_NAME}", fill="black", font=drawer)
    draw.text((30, 190), f"金额：{AMOUNT_RAW}", fill="black", font=drawer)

    # ★ 降质档位是量出来的（2026-09-16），两种失败模式都记录在案：
    #   * 本档（0.45 缩放 + 0.8 模糊 + 6% 噪声）：字段**全部读对**、置信 0.98 ⇒
    #     样本"更难但读得出"，用来守住"不许把错值自动放行"；
    #   * 更狠的档（0.30 缩放 + 1.4 模糊 + 旋转 + 12% 噪声）：**一个字段都抽不出**
    #     （`missing_fields` 全中 ⇒ 判据判失败）。那是"读不出"，与"读错却高置信"是两种不同
    #     失败模式 —— 合成样本能造出前者，造不出后者（tesseract 对合成字形宁可不读也不降置信）。
    small = hi.resize((int(hi.width * 0.45), int(hi.height * 0.45)), Image.Resampling.BILINEAR)
    small = small.filter(ImageFilter.GaussianBlur(0.8))
    pixels = small.load()
    assert pixels is not None
    rng = random.Random(20260916)  # 固定种子：样本可复现
    for y in range(small.height):
        for x in range(small.width):
            if rng.random() >= 0.06:
                continue
            # PIL 的 `pixels[...]` 在类型存根里是 `float | tuple` ⇒ 显式收窄后再解包
            pixel = pixels[x, y]
            if not isinstance(pixel, tuple):
                continue
            red, green, blue = pixel
            noise = rng.randint(-60, 60)
            pixels[x, y] = (
                max(0, min(255, red + noise)),
                max(0, min(255, green + noise)),
                max(0, min(255, blue + noise)),
            )

    path.parent.mkdir(parents=True, exist_ok=True)
    small.save(path, "PDF", resolution=200)


def make_image_only_pdf(path: Path, font: Path) -> None:
    """图片版样本：渲染成图再存 PDF（**无文字层**），用来逼出真实 OCR。"""
    from PIL import Image, ImageDraw, ImageFont

    image = Image.new("RGB", (1240, 360), "white")
    draw = ImageDraw.Draw(image)
    drawer = ImageFont.truetype(str(font), 40)
    draw.text((40, 40), f"合同编号：{CONTRACT_NO}", fill="black", font=drawer)
    draw.text((40, 130), f"合同名称：{CONTRACT_NAME}", fill="black", font=drawer)
    draw.text((40, 220), f"金额：{AMOUNT_RAW}", fill="black", font=drawer)
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path, "PDF", resolution=200)


# ── 任务定义 ─────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class PdfBenchmarkCase:
    """一个 PDF 基准任务：真值 + 抽取规则 + 样本形态。"""

    name: str
    kind: str  # "text_layer" | "scanned"
    filename: str
    needs_ocr: bool
    #: 抽取字段：字段名 → (标签, 类型, 取值模式)。模式容忍 OCR 在汉字间插空格。
    field_specs: tuple[tuple[str, str, str, str], ...] = (
        ("contract_no", "合同编号", "text", r"合同\s*编\s*号\s*[：:]\s*(?P<value>[^\n]+)"),
        ("contract_name", "合同名称", "text", r"合同\s*名\s*称\s*[：:]\s*(?P<value>[^\n]+)"),
        ("amount", "金额", "amount", r"金额\s*[：:]?\s*(?P<value>[\d,，.．]+\s*元)"),
    )
    min_confidence: float = DEFAULT_MIN_CONFIDENCE
    #: 该形态的**字段 → 真值页**（W3.1）。空表示不做页码校验。
    #: ★ 门槛按**形态**设定、互不平均：低质扫描的置信天花板本来就低，
    #: 拿它的数字去平均别的形态（或反之）等于把两种现实混成一个指标。
    expected_pages: tuple[tuple[str, int], ...] = ()
    #: 文本字段是否**显式**声明"归一 OCR 插入的空白"（产品的逐字段开关 `collapse_whitespace`）。
    #:
    #: **2026-09-15 起这里默认不声明**（§5.8 #24 已拍板：按来源默认生效）——OCR 页的文本值
    #: 本来就该归一，基准要验的正是"**不声明任何开关也达标**"。该字段保留为显式覆盖的入口
    #: （也用于反向用例：显式关掉时会不会被本判据抓住）。
    collapse_ocr_whitespace: bool = False

    def truth(self) -> dict[str, str]:
        return normalize_amount_truth(
            {"contract_no": CONTRACT_NO, "contract_name": CONTRACT_NAME, "amount": AMOUNT_RAW}
        )


#: 内置用例。新增形态即扩展覆盖面；两个基准（crawler / pdf）**互不混分**。
PDF_CASES: tuple[PdfBenchmarkCase, ...] = (
    PdfBenchmarkCase(
        name="digital-text-layer",
        kind="text_layer",
        filename="digital.pdf",
        needs_ocr=False,
    ),
    PdfBenchmarkCase(
        name="scanned-image-only",
        kind="scanned",
        filename="scan.pdf",
        needs_ocr=True,
        # 图片版**不声明**任何开关：按来源默认生效后，OCR 页的文本值本就该归一
        # （§5.8 #24）—— 本用例的验收正是「不声明任何开关也达标」。
    ),
    # ── W3.1 扩形态：多页 / 表格 / 低质扫描 ──────────────────────────────
    PdfBenchmarkCase(
        name="multi-page-text-layer",
        kind="text_layer",
        filename="multi.pdf",
        needs_ocr=False,
        # 字段在第 2 页 ⇒ **取错页会被判失败**（单页样本测不出这一点）
        expected_pages=(("contract_no", 2), ("contract_name", 2), ("amount", 2)),
    ),
    PdfBenchmarkCase(
        name="table-text-layer",
        kind="text_layer",
        filename="table.pdf",
        needs_ocr=False,
    ),
    PdfBenchmarkCase(
        name="low-quality-scan",
        kind="scanned",
        filename="lowq.pdf",
        needs_ocr=True,
        # ★ 逐形态门槛：低质扫描的置信度天然偏低，门槛按它自己的现实设定（不与其它形态平均）
        min_confidence=0.60,
    ),
)


def _config_yaml(case: PdfBenchmarkCase, root: Path, *, ocr_command: str) -> str:
    def field_block(name: str, label: str, kind: str, pattern: str) -> str:
        extra = "\n    target_unit: 元" if kind == "amount" else ""
        if kind == "text" and case.collapse_ocr_whitespace:
            extra += "\n    collapse_whitespace: true"
        return (
            f"  - name: {name}\n"
            f"    label: {label}\n"
            f"    type: {kind}\n"
            f"    source: content\n"
            f"    required: true{extra}\n"
            f"    patterns:\n      - '{pattern}'"
        )

    fields = "\n".join(field_block(*spec) for spec in case.field_specs)
    ocr = (
        f"ocr:\n  backend: tesseract\n  command: {ocr_command}\n  lang: chi_sim+eng\n  dpi: 200\n"
        if case.needs_ocr
        else "ocr:\n  backend: none\n"
    )
    return (
        "template_version: 1\n"
        f"project_name: pdf-benchmark-{case.name}\n"
        f"input_dir: {root.as_posix()}/in\n"
        f"work_dir: {root.as_posix()}/work\n"
        f"output_dir: {root.as_posix()}/out\n"
        f"database: {root.as_posix()}/work/pipeline.sqlite3\n\n"
        "parser:\n  workers: 1\n  min_native_chars: 20\n  max_garbled_ratio: 0.03\n\n"
        f"{ocr}\n"
        "retrieval:\n  top_pages: 6\n  neighbor_pages: 1\n  min_score: 1\n  fallback_pages: [1]\n\n"
        f"fields:\n{fields}\n"
    )


def _run_pdfx(config: Path, *args: str) -> dict[str, Any]:
    """进程内调用 PDF 子系统（与顶层 `omnicrawler pdf` 同一条路径）。"""
    from ..pdfx.cli import main as pdfx_main

    buf, old_argv, code = io.StringIO(), sys.argv, 0
    try:
        sys.argv = ["pdfx", "--config", str(config), *args]
        with contextlib.redirect_stdout(buf):
            pdfx_main()
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else 0
    finally:
        sys.argv = old_argv

    out = buf.getvalue()
    if code != 0:
        raise RuntimeError(f"pdfx {' '.join(args)} 退出码 {code}：{out[-600:]}")
    start, end = out.find("{"), out.rfind("}")
    return json.loads(out[start : end + 1]) if 0 <= start < end else {}


def _observations(project: Path, case: PdfBenchmarkCase) -> list[FieldObservation]:
    """从运行产物里读回字段级事实（归一值 / 证据 / 置信度 / 复核状态）。"""
    conn = sqlite3.connect(project / "work" / "pipeline.sqlite3")
    conn.row_factory = sqlite3.Row
    with conn:
        rows = conn.execute(
            "SELECT fv.field_name, fv.raw_value, fv.normalized_value, fv.page_no, fv.evidence, "
            "       fv.confidence, r.review_status "
            "FROM field_values fv JOIN records r ON r.record_id = fv.record_id"
        ).fetchall()
    return [
        FieldObservation(
            name=str(row["field_name"]),
            normalized=row["normalized_value"],
            raw_value=row["raw_value"],
            page_no=row["page_no"],
            evidence=row["evidence"],
            confidence=float(row["confidence"] or 0.0),
            review_status=str(row["review_status"] or ""),
        )
        for row in rows
    ]


def run_case(
    case: PdfBenchmarkCase,
    *,
    workdir: Path,
    tesseract: Path | None = None,
) -> PdfQualityScore:
    """跑一个用例：造样本 → 真实管线 → 读回产物 → 打分。

    图片版需要中文字体与 tesseract；缺则抛 :class:`RuntimeError`
    ——由调用方决定跳过（**不在这里假装通过**）。
    """
    project = workdir / case.name
    sample = project / "in" / case.filename
    if case.needs_ocr:
        font = cjk_font()
        if font is None:
            raise RuntimeError("缺少中文字体，无法生成图片版样本")
        if tesseract is None or not Path(tesseract).is_file():
            raise RuntimeError(f"未找到 tesseract：{tesseract}")
        if case.name == "low-quality-scan":
            make_low_quality_scan_pdf(sample, font)
        else:
            make_image_only_pdf(sample, font)
    elif case.name == "multi-page-text-layer":
        make_multi_page_pdf(sample)
    elif case.name == "table-text-layer":
        make_table_pdf(sample)
    else:
        make_digital_pdf(sample)

    config = workdir / f"{case.name}.yaml"
    config.write_text(
        _config_yaml(case, project, ocr_command=Path(tesseract).as_posix() if tesseract else ""),
        encoding="utf-8",
    )

    stages = ["ingest", "parse", *(("ocr",) if case.needs_ocr else ()), "extract", "export"]
    for stage in stages:
        _run_pdfx(config, stage)

    score = score_pdf_records(
        case.name,
        case.kind,
        case.truth(),
        _observations(project, case),
        min_confidence=case.min_confidence,
        expected_pages=dict(case.expected_pages),
    )
    return score


def observations(workdir: Path, case: PdfBenchmarkCase) -> list[FieldObservation]:
    """读回某用例的**逐字段观测**（置信度 / 页码 / 复核状态）——供验收与排障使用。

    打分只需要汇总值，但"低质样本是否真的触发了低置信复核"这类验收要看**逐字段**事实，
    因此把读取入口公开出来（复用同一实现，避免测试里再造一套读库逻辑）。
    """
    return _observations(workdir / case.name, case)


def results_rows(workdir: Path, case: PdfBenchmarkCase) -> list[dict[str, str]]:
    """读回导出表（供需要检查"交付内容"的调用方使用）。"""
    path = workdir / case.name / "out" / "results.csv"
    with path.open(encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))
