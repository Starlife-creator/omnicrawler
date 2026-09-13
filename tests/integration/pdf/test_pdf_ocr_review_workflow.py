"""附件 → PDF/OCR → 人工复核：端到端验收（**自动部分**）。

对应账本「附件→PDF/OCR→人工复核」的自动部分。**人工部分**（视觉、理解成本、
真人工复核的判断）仍由人工走查承担（`docs/MANUAL_WALKTHROUGH.md`），两者互补。

**为什么单开一个文件**：`pdfx` 是独立子系统（`ingest → parse → ocr → extract →
export`，外加 `apply-review`），此前只有组件级测试，缺"固定附件 → 字段来源位置与
不确定性 → 复核修改进入最终输出"的端到端真值任务。

**样本全部程序自造**（零第三方内容、无版权风险）：
- 数字版：reportlab 生成（中英文 + 表格）；
- 图片版：PIL 渲染成**无文字层**的 PDF，用来**逼出真实 OCR**（用项目内置 tesseract）。

真值即下面三个常量；OCR 会在汉字之间插空格，故比对前先做空白归一。
"""

from __future__ import annotations

import contextlib
import csv
import io
import json
import sqlite3
import sys
from pathlib import Path

import pytest

#: 本文件位于 tests/integration/pdf/ 下 ⇒ 仓库根是 parents[3]
REPO_ROOT = Path(__file__).resolve().parents[3]
TESSERACT = REPO_ROOT / ".runtime" / "tesseract" / "tesseract.exe"
_CJK_FONTS = (
    Path("C:/Windows/Fonts/msyh.ttc"),
    Path("C:/Windows/Fonts/simsun.ttc"),
    Path("C:/Windows/Fonts/simhei.ttf"),
)

CONTRACT_NO = "HT-2026-0001"
CONTRACT_NAME = "示例服务合同"
AMOUNT_RAW = "12,345.67 元"
AMOUNT_NORMALIZED = "12345.67"

_CONFIG_TEMPLATE = """template_version: 1
project_name: pdf-e2e
input_dir: {root}/in
work_dir: {root}/work
output_dir: {root}/out
database: {root}/work/pipeline.sqlite3

parser:
  workers: 1
  min_native_chars: 20
  max_garbled_ratio: 0.03

ocr:
  backend: {backend}{command_line}
  lang: chi_sim+eng
  dpi: 200

retrieval:
  top_pages: 6
  neighbor_pages: 1
  min_score: 1
  fallback_pages: [1]

fields:
  - name: contract_no
    label: 合同编号
    type: text
    source: content
    required: true{no_extra}
    patterns:
      - '{no_pattern}\\s*[：:]\\s*(?P<value>[^\\n]+)'
  - name: contract_name
    label: 合同名称
    type: text
    source: content
    required: true{name_extra}
    patterns:
      - '{name_pattern}\\s*[：:]\\s*(?P<value>[^\\n]+)'
  - name: amount
    label: 金额
    type: amount
    source: content
    target_unit: 元
    patterns:
      - '金额\\s*[：:]?\\s*(?P<value>[\\d,，.]+\\s*元)'
"""


def _run_pdfx(config: Path, *args: str) -> dict:
    """进程内调用 PDF 子系统（与顶层 `omnicrawler pdf` 内部同一条路径）。"""
    from omnicrawler.pdfx.cli import main as pdfx_main

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
    assert code == 0, f"pdfx {' '.join(args)} 退出码 {code}：{out[-900:]}"
    start, end = out.find("{"), out.rfind("}")
    return json.loads(out[start : end + 1]) if start >= 0 and end > start else {}


def _make_digital_pdf(path: Path) -> None:
    pdfmetrics = pytest.importorskip("reportlab.pdfbase.pdfmetrics")
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

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
            Spacer(1, 6 * mm),
            Paragraph("Details / 明细", style),
            Table(
                [
                    ["Item / 项目", "Qty / 数量", "Price / 单价"],
                    ["Consulting / 咨询", "2", "5,000.00"],
                    ["Support / 支持", "1", "2,345.67"],
                ],
                style=TableStyle(
                    [
                        ("FONTNAME", (0, 0), (-1, -1), "STSong-Light"),
                        ("GRID", (0, 0), (-1, -1), 0.4, colors.grey),
                    ]
                ),
            ),
        ]
    )


def _cjk_font() -> Path | None:
    return next((item for item in _CJK_FONTS if item.is_file()), None)


def _make_image_only_pdf(path: Path, font_path: Path) -> None:
    """渲染成图再存为单页 PDF（**无文字层**），用来逼出 OCR。"""
    from PIL import Image, ImageDraw, ImageFont

    image = Image.new("RGB", (1240, 360), "white")
    draw = ImageDraw.Draw(image)
    font = ImageFont.truetype(str(font_path), 40)
    draw.text((40, 40), f"合同编号：{CONTRACT_NO}", fill="black", font=font)
    draw.text((40, 130), f"合同名称：{CONTRACT_NAME}", fill="black", font=font)
    draw.text((40, 220), f"金额：{AMOUNT_RAW}", fill="black", font=font)
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path, "PDF", resolution=200)


def _write_config(
    path: Path,
    project: Path,
    *,
    ocr_backend: str = "none",
    ocr_command: str = "",
    strict_patterns: bool = False,
    collapse_whitespace: bool = False,
) -> None:
    """写 pdfx 项目配置。

    ``strict_patterns=True`` 使用**不宽容 OCR 空格**的模式。OCR 常在汉字间插空格，
    严格模式因此匹配不到 ⇒ 记录被判 `invalid` / `needs_review` 并进入复核队列 ——
    用它复现"抽取失败 → 人工复核 → 修改进入最终输出"的真实回路。
    """
    path.write_text(
        _CONFIG_TEMPLATE.format(
            root=project.as_posix(),
            backend=ocr_backend,
            command_line=f"\n  command: {ocr_command}" if ocr_command else "",
            # 严格模式 = 逐字匹配（不宽容 OCR 插入的空白）；默认模式 = 容忍空白
            no_pattern="合同编号" if strict_patterns else r"合同\s*编号",
            name_pattern="合同名称" if strict_patterns else r"合同\s*名\s*称",
            # 逐字段声明：默认关；只有显式打开才归一 OCR 空白（既有产出不变）
            no_extra="\n    collapse_whitespace: true" if collapse_whitespace else "",
            name_extra="\n    collapse_whitespace: true" if collapse_whitespace else "",
        ),
        encoding="utf-8",
    )


def _rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def _text_of(out_dir: Path) -> str:
    texts = sorted((out_dir / "text").glob("*.txt"))
    assert texts, f"应产出文本：{sorted(p.name for p in out_dir.glob('*'))}"
    return texts[0].read_text(encoding="utf-8")


def _squash(value: str) -> str:
    """比较 OCR 文本时抹掉空白，并把全角标点归一为半角。

    OCR 会把全角冒号输出成半角（实测 `合同编号：` → `合同编号:`），
    这属识别器的正常差异，不该让"内容是否取到"的判断失败。
    """
    return "".join(value.translate(str.maketrans("：，。（）", ":,.()")).split())


def _db(project: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(project / "work" / "pipeline.sqlite3")
    conn.row_factory = sqlite3.Row
    return conn


def _require_ocr_env() -> Path:
    font = _cjk_font()
    if font is None:
        pytest.skip("缺少中文字体，无法生成图片版样本（该环境不可达）")
    if not TESSERACT.is_file():
        pytest.skip(f"未找到内置 tesseract：{TESSERACT}（该环境不可达）")
    return font


def test_digital_pdf_extracts_fields_with_source_evidence(tmp_path: Path) -> None:
    """数字版 PDF：中英文与表格都取到，字段带**页码 + 原文证据**，校验通过。"""
    project = tmp_path / "proj"
    _make_digital_pdf(project / "in" / "sample.pdf")
    config = tmp_path / "pdf.yaml"
    _write_config(config, project)

    for stage in ("ingest", "parse", "extract", "export", "export-text"):
        _run_pdfx(config, stage)

    text = _text_of(project / "out")
    assert f"合同编号：{CONTRACT_NO}" in text, "数字版应直接取到原生文字"
    assert "Sample Service Contract" in text, "英文内容也应在文本层里"
    assert "| Item / 项目 |" in text, "表格应被还原为结构化文本"

    rows = _rows(project / "out" / "results.csv")
    assert len(rows) == 1, f"应恰好一条记录：{rows}"
    row = rows[0]
    assert row["合同编号"] == CONTRACT_NO
    assert row["合同名称"] == CONTRACT_NAME
    assert row["金额"] == AMOUNT_NORMALIZED, "金额应标准化为「元」的数值"
    assert row["金额_原始值"] == AMOUNT_RAW, "原始值要保留"
    assert row["金额_单位"] == "元"
    assert row["金额_页码"] == "1", "来源位置（页码）要可见"
    assert row["金额_原文证据"], "必须带原文证据"
    assert row["校验状态"] == "valid", row.get("校验信息")
    assert len(_rows(project / "out" / "field_values_long.csv")) == 3, "长表每字段一行"


def test_image_only_pdf_runs_real_ocr_and_extracts(tmp_path: Path) -> None:
    """图片版 PDF（无文字层）：真的走 OCR，且模式宽容 OCR 空格时能正常抽取。"""
    font = _require_ocr_env()

    project = tmp_path / "proj"
    _make_image_only_pdf(project / "in" / "scan.pdf", font)
    config = tmp_path / "pdf.yaml"
    _write_config(config, project, ocr_backend="tesseract", ocr_command=TESSERACT.as_posix())

    for stage in ("ingest", "parse", "ocr", "extract", "export"):
        _run_pdfx(config, stage)

    with _db(project) as db:
        page = dict(
            db.execute(
                "SELECT parse_method, ocr_status, ocr_confidence, final_text FROM pages"
            ).fetchone()
        )
        record = dict(
            db.execute("SELECT validation_status, review_status FROM records").fetchone()
        )

    assert page["parse_method"] == "ocr", f"该页无文字层，必须靠 OCR：{page}"
    assert page["ocr_status"] == "done", page
    assert (page["ocr_confidence"] or 0) > 0.5, f"OCR 置信度应可查：{page}"

    recovered = _squash(page["final_text"] or "")
    assert f"合同编号:{CONTRACT_NO}" in recovered, f"OCR 应还原出编号：{recovered}"
    assert f"合同名称:{CONTRACT_NAME}" in recovered, f"OCR 应还原出名称：{recovered}"

    # 为 OCR 写模式要容忍 OCR 插入的空白，否则明明识别出来了也匹配不到
    assert record["validation_status"] == "valid", record
    assert record["review_status"] == "auto_accepted", record

    rows = _rows(project / "out" / "results.csv")
    assert len(rows) == 1, rows
    # 注意：OCR 文本里的汉字间空格会**原样进入 text 字段值**（实测 "示例  服务  合同"），
    # 因为取值模式把整行余下内容都收进来了。所以这里按空白归一再比 —— 内容确实取到了；
    # 要去掉多余空格属配置侧收紧模式（或走复核），不在本用例的断言范围。
    assert _squash(rows[0]["合同编号"]) == CONTRACT_NO, rows[0]
    assert _squash(rows[0]["合同名称"]) == CONTRACT_NAME, rows[0]
    # OCR 可能把千分位逗号读成小数点（实测 12,345.67 → 12.345.67），
    # 因此这里只断言"金额被识别出来了"，不与真值做逐字符比较 —— 这类偏差正是复核环节存在的原因。
    assert rows[0]["金额"], f"金额应被识别出来：{rows[0]}"
    assert rows[0]["金额_页码"] == "1" and rows[0]["金额_原文证据"], "OCR 结果同样要带来源证据"


def test_human_review_correction_reaches_final_export(tmp_path: Path) -> None:
    """不确定性可见 + 人工复核的修改进入最终输出。

    本条故意用**严格模式**（不宽容 OCR 空格）制造"抽取失败"，以走通真实回路：
    判 invalid / needs_review → 进复核队列 → 人工补齐并确认 → 进入最终导出。
    """
    font = _require_ocr_env()

    project = tmp_path / "proj"
    _make_image_only_pdf(project / "in" / "scan.pdf", font)
    config = tmp_path / "pdf.yaml"
    _write_config(
        config,
        project,
        ocr_backend="tesseract",
        ocr_command=TESSERACT.as_posix(),
        strict_patterns=True,
    )

    for stage in ("ingest", "parse", "ocr", "extract", "export"):
        _run_pdfx(config, stage)

    # ① 不确定性必须**可见**：缺必填字段要进复核队列，而不是被当成正常结果
    with _db(project) as db:
        record0 = dict(
            db.execute(
                "SELECT validation_status, review_status, validation_messages FROM records"
            ).fetchone()
        )
    assert record0["review_status"] == "needs_review", record0
    assert record0["validation_status"] == "invalid", record0
    assert "缺少必填字段" in record0["validation_messages"], record0

    queue = project / "out" / "review_queue.csv"
    rows = _rows(queue)
    assert len(rows) == 1, f"复核队列应有待复核记录：{rows}"

    # ② 模拟人工复核：补齐字段并确认
    rows[0]["合同编号"] = CONTRACT_NO
    rows[0]["合同名称"] = CONTRACT_NAME
    rows[0]["复核决定"] = "确认"
    rows[0]["复核备注"] = "人工核对原文后补齐"
    reviewed = tmp_path / "reviewed.csv"
    with reviewed.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    applied = _run_pdfx(config, "apply-review", "--file", str(reviewed))
    assert applied["result"]["accepted"] == 1, applied

    with _db(project) as db:
        record = dict(
            db.execute("SELECT record_id, review_status, validation_status FROM records").fetchone()
        )
        values = {
            row["field_name"]: dict(row)
            for row in db.execute(
                "SELECT * FROM field_values WHERE record_id=?", (record["record_id"],)
            )
        }

    assert record["review_status"] == "human_accepted", record
    assert record["validation_status"] == "human_reviewed", record
    assert values["contract_no"]["extraction_method"] == "human_review", values.get("contract_no")
    assert float(values["contract_no"]["confidence"]) == 1.0
    assert values["contract_no"]["normalized_value"] == CONTRACT_NO

    # ③ 复核结果进入最终导出
    _run_pdfx(config, "export")
    exported = _rows(project / "out" / "results.csv")
    assert len(exported) == 1, exported
    assert exported[0]["合同编号"] == CONTRACT_NO, "人工补齐的编号必须出现在最终导出里"
    assert exported[0]["合同名称"] == CONTRACT_NAME
    assert exported[0]["复核状态"] == "human_accepted"


def test_ocr_whitespace_collapse_is_opt_in(tmp_path: Path) -> None:
    """OCR 空白归一是**显式选项**：开启后取值与真值逐字符相等。

    默认关（既有产出不变）由 `tests/unit/pdf/test_collapse_whitespace_option.py` 锁住；
    这里证明"开启后问题真的解决"：同一份**图片版**样本（OCR 必然在汉字间插空格），
    两个文本字段声明 `collapse_whitespace` 后，取值应等于真值、不再需要空白归一比较。
    """
    font = _require_ocr_env()

    project = tmp_path / "proj"
    _make_image_only_pdf(project / "in" / "scan.pdf", font)
    config = tmp_path / "pdf.yaml"
    _write_config(
        config,
        project,
        ocr_backend="tesseract",
        ocr_command=TESSERACT.as_posix(),
        collapse_whitespace=True,
    )

    for stage in ("ingest", "parse", "ocr", "extract", "export"):
        _run_pdfx(config, stage)

    rows = _rows(project / "out" / "results.csv")
    assert len(rows) == 1, rows
    # 关键差异：逐字符相等，不再需要 _squash（空白已按字段声明归一）
    assert rows[0]["合同编号"] == CONTRACT_NO, rows[0]
    assert rows[0]["合同名称"] == CONTRACT_NAME, rows[0]
    # 归一不改证据：原始值仍在
    assert rows[0]["合同名称_原始值"], "原始值必须仍保留（归一不动证据）"
    assert rows[0]["合同名称_页码"] == "1"
