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
            "src/omnicrawl/http_client.py",
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
        10.0,
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

OVERALL_COVERAGE_GATE = 50.0


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

    if failures:
        print("\nCoverage gates failed:", file=sys.stderr)
        for failure in failures:
            print(f"- {failure}", file=sys.stderr)
        return 1
    print("\nAll coverage gates passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
