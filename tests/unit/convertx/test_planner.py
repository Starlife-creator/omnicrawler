"""转换边图与最短路测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from omnicrawler.convertx.planner import (
    ConversionEdge,
    ConversionPlan,
    PlanStep,
    build_default_edges,
    describe_plan,
    execute_plan,
    plan_conversion,
    reachable_formats,
)


def test_direct_plan_for_table_pair() -> None:
    plan = plan_conversion(".csv", ".xlsx")
    assert plan is not None
    assert plan.hops == 1
    assert plan.formats() == (".csv", ".xlsx")
    assert plan.total_cost > 0


def test_direct_plan_for_document_pair() -> None:
    plan = plan_conversion(".pdf", ".docx")
    assert plan is not None
    assert plan.hops == 1


def test_image_guard_blocks_cross_family() -> None:
    assert plan_conversion(".png", ".jpg") is not None
    assert plan_conversion(".png", ".csv") is None
    assert plan_conversion(".csv", ".png") is None
    edges = build_default_edges()
    assert not any(edge.source == ".png" and edge.target == ".csv" for edge in edges)


def test_read_only_format_has_no_outgoing_writer_edge() -> None:
    # .pptx 可读但没有 Writer：不能作为目标，也不能自环中转。
    assert plan_conversion(".csv", ".pptx") is None
    assert plan_conversion(".pptx", ".pptx") is None
    assert plan_conversion(".pptx", ".md") is not None


def test_custom_chain_respects_max_steps() -> None:
    edges = [ConversionEdge(".a", ".b", 1), ConversionEdge(".b", ".c", 1)]
    plan = plan_conversion(".a", ".c", edges=edges, max_steps=3)
    assert plan is not None
    assert plan.hops == 2
    assert plan.formats() == (".a", ".b", ".c")
    assert plan_conversion(".a", ".c", edges=edges, max_steps=1) is None


def test_hop_penalty_is_applied() -> None:
    edges = [ConversionEdge(".a", ".b", 1), ConversionEdge(".b", ".c", 1)]
    plan = plan_conversion(".a", ".c", edges=edges)
    assert plan is not None
    assert plan.total_cost == 1 + 5 + 1


def test_describe_plan() -> None:
    text = describe_plan(plan_conversion(".csv", ".jsonl"))
    assert "转换计划" in text
    assert ".csv → .jsonl" in text
    assert "不可达" in describe_plan(None)


def test_reachable_formats() -> None:
    targets = reachable_formats(".csv")
    assert ".xlsx" in targets
    assert ".docx" in targets
    assert ".pdf" in targets
    assert ".png" not in targets


def test_all_format_pair_invariants() -> None:
    from omnicrawler.convertx.catalog import format_status, known_formats
    from omnicrawler.convertx.formats import IMAGE_EXTENSIONS

    formats = known_formats()
    edges = build_default_edges()
    for source in formats:
        for target in formats:
            if source == target:
                continue
            plan = plan_conversion(source, target, edges=edges)
            source_is_image = source in IMAGE_EXTENSIONS
            target_is_image = target in IMAGE_EXTENSIONS
            if source_is_image != target_is_image:
                assert plan is None, f"图片族不得跨族: {source} -> {target}"
            elif format_status(source).readable and format_status(target).writable:
                assert plan is not None, f"应可达却无计划: {source} -> {target}"


def test_execute_plan_runs_each_step(tmp_path: Path) -> None:
    pytest.importorskip("openpyxl")
    src = tmp_path / "in.csv"
    src.write_text("a,b\n1,2\n", encoding="utf-8-sig")
    dst = tmp_path / "out.xlsx"
    plan = ConversionPlan(
        steps=(
            PlanStep(".csv", ".jsonl", 8),
            PlanStep(".jsonl", ".xlsx", 15),
        ),
        total_cost=28,
    )
    result = execute_plan(plan, src, dst)
    assert dst.is_file()
    assert result.output_path == dst
    assert result.extra["plan"][0]["source"] == ".csv"
