"""PDF 质量基准入口：在程序自造样本上核对「要么对，要么进复核」。

与 `tools/benchmark_quality.py` 的关系：后者测 **crawler** 侧的结构化抽取
（数据对不对、全不全、有没有来源证据），本工具测 **pdfx** 侧的模糊抽取 / OCR。
两个基准**数据集、口径、门槛各自独立，不混分**（见
``omnicrawler.services.pdf_quality_benchmark`` 模块说明）。

```bash
python tools/benchmark_pdf_quality.py                        # 跑全部内置用例
python tools/benchmark_pdf_quality.py --case digital-text-layer --json report.json
python tools/benchmark_pdf_quality.py --tesseract /usr/bin/tesseract
```

样本全部由程序自造（reportlab / PIL 渲染），**零第三方内容、不依赖外部网络**，
因此「同一版本 → 同一用例 → 可比结果」。

环境不可达（如缺 tesseract）时**跳过并如实登记**——既不当成通过，也不静默：
跳过的用例在表格与 JSON 里都带原因，退出码与"全部跳过"单独区分。

退出码
------
===  ==========================================================================
0    全部选中用例都跑完且满足门槛
1    有用例跑完但未达门槛
2    ``--case`` 没匹配到任何用例（选中 0 个必须判红）
3    选中用例**一个都没跑起来**（环境全部不可达 ⇒ 空集合，同样判红）
===  ==========================================================================
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from omnicrawler.services.pdf_quality_benchmark import (  # noqa: E402
    PDF_CASES,
    PdfBenchmarkCase,
    PdfQualityScore,
    cjk_font,
    run_case,
)

REPO_ROOT = Path(__file__).resolve().parents[1]

#: 仓库自带的 tesseract（Windows 便携构建产物，见 .github/workflows）
BUNDLED_TESSERACT = REPO_ROOT / ".runtime" / "tesseract" / "tesseract.exe"

_COLUMNS = (
    ("case", 24),
    ("kind", 12),
    ("fields", 9),
    ("matched", 10),
    ("accuracy", 10),
    ("evidence", 10),
    ("review_viol", 12),
    ("unreviewed", 12),
    ("ok", 6),
)


def _resolve_tesseract(explicit: str | None) -> Path | None:
    """定位 tesseract：显式参数 → 仓库自带 → PATH。

    与集成测试 ``tests/integration/pdf/test_pdf_quality_benchmark.py`` 同一口径
    （优先用仓库自带的 ``.runtime``），额外允许系统安装以便在 Linux/macOS 上跑。
    """
    if explicit:
        return Path(explicit)
    if BUNDLED_TESSERACT.is_file():
        return BUNDLED_TESSERACT
    found = shutil.which("tesseract")
    return Path(found) if found else None


def _missing_env_reason(case: PdfBenchmarkCase, tesseract: Path | None) -> str:
    """用例的前置环境缺口（空串＝环境齐备）。"""
    if not case.needs_ocr:
        return ""
    if cjk_font() is None:
        return "缺少中文字体，无法生成图片版样本"
    if tesseract is None or not Path(tesseract).is_file():
        return f"未找到 tesseract：{tesseract or '（仓库自带与 PATH 均无）'}"
    return ""


def _row(score: PdfQualityScore) -> str:
    mapping = score.to_mapping()
    return (
        mapping["case"].ljust(24)
        + mapping["kind"].ljust(12)
        + f"{mapping['matched_fields']}/{mapping['expected_fields']}".ljust(9)
        + str(mapping["observed_fields"]).ljust(10)
        + f"{mapping['field_accuracy']:.2f}".ljust(10)
        + f"{mapping['evidence_ratio']:.2f}".ljust(10)
        + str(mapping["review_violations"]).ljust(12)
        + str(mapping["unreviewed_errors"]).ljust(12)
        + ("是" if mapping["ok"] else "否").ljust(6)
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="在程序自造的 PDF 样本上核对「要么对，要么进复核」"
    )
    parser.add_argument("--case", action="append", help="只跑指定用例（可重复；默认全部）")
    parser.add_argument("--json", dest="json_path", help="把报告写入该 JSON 文件")
    parser.add_argument("--workdir", help="中间产物目录（默认系统临时目录）")
    parser.add_argument(
        "--tesseract", help="tesseract 可执行文件路径（缺省：仓库自带 → PATH）"
    )
    args = parser.parse_args(argv)

    selected = [case for case in PDF_CASES if not args.case or case.name in args.case]
    if not selected:
        print(f"没有匹配的用例；可用：{[case.name for case in PDF_CASES]}", file=sys.stderr)
        return 2

    tesseract = _resolve_tesseract(args.tesseract)

    header = "".join(name.ljust(width) for name, width in _COLUMNS)
    print(header)
    print("-" * len(header))

    scores: list[PdfQualityScore] = []
    skipped: list[dict[str, str]] = []
    with tempfile.TemporaryDirectory() as temp:
        base = Path(args.workdir) if args.workdir else Path(temp)
        for case in selected:
            reason = _missing_env_reason(case, tesseract)
            if reason:
                skipped.append({"case": case.name, "reason": reason})
                print(case.name.ljust(24) + "跳过".ljust(12) + reason)
                continue
            score = run_case(case, workdir=base, tesseract=tesseract)
            scores.append(score)
            print(_row(score))

    failed = [score.case for score in scores if not score.ok]
    print()
    print(f"结论：{len(scores) - len(failed)}/{len(scores)} 个用例满足「要么对，要么进复核」")
    print("说明：要求归一后字段准确、每字段带页码与原文证据、低置信必须进复核、错值不得自动放行。")
    if skipped:
        print(f"跳过：{len(skipped)}/{len(selected)} 个用例环境不可达（不计入通过）")
        for item in skipped:
            print(f"  - {item['case']}：{item['reason']}")

    if args.json_path:
        target = Path(args.json_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(
                {
                    "cases": [score.to_mapping() for score in scores],
                    "skipped": skipped,
                    "failed": failed,
                    "selected": [case.name for case in selected],
                    "tesseract": str(tesseract) if tesseract else "",
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        print(f"报告：{target}")

    if not scores:
        print("选中用例一个都没跑起来：环境全部不可达，空集合不得判绿。", file=sys.stderr)
        return 3
    if failed:
        print(f"未达门槛用例：{failed}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
