"""编排层不得再出现格式特例（AGENTS.md 原则 3 的机器化判据）。

原先是 `pipeline/_fetch.py` 直接写 `application/pdf` / `%PDF-` / `.pdf` 三个字面量、
`pipeline/_run.py` 直接调 PDF 集成。格式与站点差异属于配置/能力层，不属于编排层，
所以这里钉死：编排层源码里再出现这些特例就红。

反向断言的做法是先用**构造出来的**违规源码证明检测器真的会命中——否则
「扫不到」和「扫描器坏了」看起来完全一样。
"""

from __future__ import annotations

import re
from pathlib import Path

PIPELINE_DIR = Path(__file__).resolve().parents[3] / "src" / "omnicrawler" / "pipeline"

#: 编排层里不该出现的格式特例。
FORBIDDEN_IN_PIPELINE: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("PDF content type literal", re.compile(r"application/pdf")),
    ("PDF magic literal", re.compile(r"%PDF-")),
    ("PDF stage integration import", re.compile(r"pdf_integration|run_pdf_pipeline")),
)


def _scan(source: str) -> list[str]:
    """返回命中说明；空列表表示这段源码没有格式特例。"""
    return [label for label, pattern in FORBIDDEN_IN_PIPELINE if pattern.search(source)]


def _pipeline_sources() -> dict[str, str]:
    return {
        path.name: path.read_text(encoding="utf-8", errors="replace")
        for path in sorted(PIPELINE_DIR.glob("*.py"))
    }


def test_scanner_flags_a_violation() -> None:
    """反向断言：把特例装回源码，检测器必须命中（否则下面全是假绿）。"""
    offending = (
        "if result.content_type == 'application/pdf' or result.body.startswith(b'%PDF-'):\n"
        "    from ..pipeline_ops.pdf_integration import run_pdf_pipeline\n"
    )
    hits = _scan(offending)
    assert len(hits) == 3


def test_pipeline_layer_has_no_format_special_cases() -> None:
    sources = _pipeline_sources()
    assert sources, "empty selection is not a pass: 没有扫到任何编排层源码"
    offenders = {
        name: hits for name, hits in ((n, _scan(s)) for n, s in sources.items()) if hits
    }
    assert offenders == {}, f"编排层出现格式特例（应移到 core/pipeline_ops）：{offenders}"


def test_pdf_stage_is_defined_outside_the_orchestration_layer() -> None:
    """阶段定义必须在 pipeline_ops，编排层只调用它。"""
    runner = (PIPELINE_DIR / "_run.py").read_text(encoding="utf-8")
    assert "from ..pipeline_ops.pdf_stage import run_pdf_stage" in runner
    stage_module = PIPELINE_DIR.parent / "pipeline_ops" / "pdf_stage.py"
    assert stage_module.is_file()
