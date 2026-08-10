from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

Matcher = Callable[[str], bool]


def _normalise(path: str) -> str:
    return path.replace("\\", "/")


def _is_one_of(*paths: str) -> Matcher:
    expected = {_normalise(path) for path in paths}
    return lambda path: path in expected


def _starts_with(*prefixes: str) -> Matcher:
    expected = tuple(_normalise(prefix) for prefix in prefixes)
    return lambda path: path.startswith(expected)


GATES: dict[str, tuple[float, Matcher]] = {
    "security_and_state": (
        85.0,
        _is_one_of(
            "src/omnicrawl/security/policy.py",
            "src/omnicrawl/security/egress.py",  # 出口管控核心（S37 补漏）
            "src/omnicrawl/fetching/archives.py",
            "src/omnicrawl/state/state_store.py",
            "src/omnicrawl/core/migrations.py",
        ),
    ),
    "pipeline_http_sources": (
        75.0,
        _starts_with(
            "src/omnicrawl/pipeline/",
        ),
    ),
    "pipeline_http_client": (
        75.0,
        _is_one_of(
            # 曾把不存在的 src/omnicrawl/http_client.py 列在此处——门禁在查空集合
            # （S37 死路径），已移除；真实路径为 fetching/http_client.py
            "src/omnicrawl/fetching/http_client.py",
            "src/omnicrawl/sources/sources.py",
        ),
    ),
    "browser_and_api": (
        70.0,
        _is_one_of(
            "src/omnicrawl/fetching/browser_fetcher.py",
            "src/omnicrawl/extraction/api_discovery.py",
        ),
    ),
    "pdf_and_ocr": (
        65.0,
        lambda path: path.startswith("src/omnicrawl/pdfx/")
        or path
        in {
            "src/omnicrawl/pipeline_ops/pdf_integration.py",
            "src/omnicrawl/pipeline_ops/pdf_region.py",
            "src/omnicrawl/apps/pdf_processor.py",
            "src/omnicrawl/apps/field_extractor.py",
        },
    ),
    "desktop_core": (
        65.0,
        lambda path: path.startswith("src/omnicrawl/gui/core/")
        or path
        in {
            "src/omnicrawl/gui/runner/worker_task_runner.py",
            "src/omnicrawl/gui/wizard/step1_source.py",
            "src/omnicrawl/gui/wizard/step2_urls.py",
            "src/omnicrawl/gui/wizard/step4_download.py",
            "src/omnicrawl/gui/wizard/step5_preview.py",
        },
    ),
}

OVERALL_COVERAGE_GATE = 66.0

# 单文件下限：分组门禁是加权聚合，关键模块可以被同组高覆盖率"赎买"
# （审查报告 S37③）。对最关键的文件单独设下限——低于即失败，不许借道。
# 取值贴近 2026-08 实测水平，作为"禁止继续下滑"的护栏；
# 提高这些下限需要配套补齐测试（见审查报告 §8 修复优先级）。
_FILE_FLOORS: dict[str, float] = {
    "src/omnicrawl/security/policy.py": 80.0,
    "src/omnicrawl/security/egress.py": 80.0,
    "src/omnicrawl/fetching/http_client.py": 70.0,
    "src/omnicrawl/apps/field_extractor.py": 30.0,
    "src/omnicrawl/apps/pdf_processor.py": 40.0,
}


def _coverage(files: dict[str, Any], matcher: Matcher) -> tuple[int, int, float]:
    statements = covered = matched = 0
    for raw_path, details in files.items():
        path = _normalise(raw_path)
        if not matcher(path):
            continue
        matched += 1
        summary = details["summary"]
        statements += int(summary["num_statements"])
        covered += int(summary["covered_lines"])
    if not matched or not statements:
        raise ValueError("coverage report does not contain the required source group")
    return covered, statements, covered * 100.0 / statements


def _file_coverage(files: dict[str, Any], raw_path: str) -> float | None:
    """单文件覆盖率；报告里不存在该文件时返回 None（视为缺失）。

    coverage.json 的 key 是**绝对路径**（CI runner 上各不相同），因此先按
    相对路径精确匹配，再按「以 src/... 结尾」兜底匹配，避免不同机器上
    覆盖率报告路径前缀差异导致的漏检（曾导致 Windows CI 全报 missing）。
    """
    norm = _normalise(raw_path)
    details = files.get(norm)
    if details is None:
        for key, value in files.items():
            candidate = _normalise(key)
            if candidate == norm or candidate.endswith("/" + norm):
                details = value
                break
    if details is None:
        return None
    summary = details["summary"]
    statements = int(summary["num_statements"])
    if not statements:
        return 100.0
    return int(summary["covered_lines"]) * 100.0 / statements


def main() -> int:
    parser = argparse.ArgumentParser(description="Enforce OmniCrawler subsystem coverage gates")
    parser.add_argument("report", nargs="?", default="coverage.json", type=Path)
    args = parser.parse_args()
    payload = json.loads(args.report.read_text(encoding="utf-8"))
    files: dict[str, Any] = payload.get("files", {})
    failures: list[str] = []

    print("coverage gate                 covered/lines   actual   minimum")
    print("-" * 67)
    total = payload.get("totals", {})
    total_actual = float(total.get("percent_covered", 0.0))
    total_minimum = OVERALL_COVERAGE_GATE
    print(
        f"{'all_source':29} {int(total.get('covered_lines', 0)):5}/"
        f"{int(total.get('num_statements', 0)):<7} {total_actual:7.2f}% {total_minimum:7.2f}%"
    )
    if total_actual < total_minimum:
        failures.append(f"all_source {total_actual:.2f}% < {total_minimum:.2f}%")

    for name, (minimum, matcher) in GATES.items():
        try:
            covered, statements, actual = _coverage(files, matcher)
        except ValueError as exc:
            failures.append(f"{name}: {exc}")
            print(f"{name:29} {'missing':>13} {'--':>8} {minimum:7.2f}%")
            continue
        print(f"{name:29} {covered:5}/{statements:<7} {actual:7.2f}% {minimum:7.2f}%")
        if actual < minimum:
            failures.append(f"{name} {actual:.2f}% < {minimum:.2f}%")

    # 单文件下限（S37③）：分组聚合掩盖关键模块，逐文件兜底
    for raw_path, floor in sorted(_FILE_FLOORS.items()):
        actual = _file_coverage(files, raw_path)
        if actual is None:
            failures.append(f"{raw_path}: missing from coverage report (file-level floor {floor:.2f}%)")
            print(f"{raw_path:29} {'missing':>13} {'--':>8} {floor:7.2f}%")
            continue
        print(f"{raw_path:29} {'':>5}{'':<7} {actual:7.2f}% {floor:7.2f}%")
        if actual < floor:
            failures.append(f"{raw_path} {actual:.2f}% < {floor:.2f}%")

    if failures:
        print("\nCoverage gates failed:", file=sys.stderr)
        for failure in failures:
            print(f"- {failure}", file=sys.stderr)
        return 1
    print("\nAll coverage gates passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
