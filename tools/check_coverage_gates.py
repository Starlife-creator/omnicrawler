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


def _is_one_of(*paths: str, prefixes: tuple[str, ...] = ()) -> Matcher:
    """精确路径集合，外加可选前缀族（P1-3 拆分后的「模块族」用前缀覆盖）。"""
    expected = {_normalise(path) for path in paths}
    expanded = tuple(_normalise(prefix) for prefix in prefixes)
    return lambda path: path in expected or path.startswith(expanded)


def _starts_with(*prefixes: str) -> Matcher:
    expected = tuple(_normalise(prefix) for prefix in prefixes)
    return lambda path: path.startswith(expected)


GATES: dict[str, tuple[float, Matcher]] = {
    "security_and_state": (
        85.0,
        # state_store 自 P1-3 起是「门面 + 6 个 Mixin」，用前缀纳入全部模块，
        # 否则门禁只能看到 103 行门面、约 900 行实现漏出视野。
        _is_one_of(
            "src/omnicrawler/security/policy.py",
            "src/omnicrawler/security/egress.py",  # 出口管控核心（S37 补漏）
            "src/omnicrawler/fetching/archives.py",
            "src/omnicrawler/core/migrations.py",
            prefixes=("src/omnicrawler/state/state_store",),
        ),
    ),
    "pipeline_http_sources": (
        75.0,
        _starts_with(
            "src/omnicrawler/pipeline/",
        ),
    ),
    "pipeline_http_client": (
        75.0,
        _is_one_of(
            # 曾把不存在的 src/omnicrawler/http_client.py 列在此处——门禁在查空集合
            # （S37 死路径），已移除；真实路径为 fetching/http_client.py
            "src/omnicrawler/fetching/http_client.py",
            "src/omnicrawler/sources/sources.py",
        ),
    ),
    "browser_and_api": (
        70.0,
        # browser_fetcher 自 P1-3 起拆出 browser_engines/guards/pool，同样用前缀纳入。
        _is_one_of(
            "src/omnicrawler/extraction/api_discovery.py",
            prefixes=("src/omnicrawler/fetching/browser_",),
        ),
    ),
    "pdf_and_ocr": (
        65.0,
        lambda path: path.startswith("src/omnicrawler/pdfx/")
        or path
        in {
            "src/omnicrawler/pipeline_ops/pdf_integration.py",
            "src/omnicrawler/pipeline_ops/pdf_region.py",
            "src/omnicrawler/apps/pdf_processor.py",
            "src/omnicrawler/apps/field_extractor.py",
        },
    ),
    "desktop_core": (
        65.0,
        lambda path: path.startswith("src/omnicrawler/gui/core/")
        or path
        in {
            "src/omnicrawler/gui/runner/worker_task_runner.py",
            "src/omnicrawler/gui/wizard/step1_source.py",
            "src/omnicrawler/gui/wizard/step2_urls.py",
            "src/omnicrawler/gui/wizard/step4_download.py",
            "src/omnicrawler/gui/wizard/step5_preview.py",
        },
    ),
}

OVERALL_COVERAGE_GATE = 66.0

# 按顶层子包设「只降不升」下限（P2-1 ratchet）。
# 取值口径 = 2026-09-11 本地实测值 − 8（经验余量）：CI quality job 在 Ubuntu、无
# browser/extras，会 skip 部分 GUI/browser 测试，覆盖率系统性低于本地（原全局阈值
# 66 与本地实测 74.46 的差距即源于此）。待 CI 实测覆盖率回收后，按实测收紧到零余量。
# 先覆盖方案点名的三个「非 GUI、测试更便宜」的包，其余包待后续批次逐个纳入。
PACKAGE_FLOORS: dict[str, float] = {
    "core": 81.0,      # 本地实测 89.83%
    "state": 87.0,     # 本地实测 95.01%
    "fetching": 70.0,  # 本地实测 78.03%
}

# 单文件下限：分组门禁是加权聚合，关键模块可以被同组高覆盖率"赎买"
# （审查报告 S37③）。对最关键的文件单独设下限——低于即失败，不许借道。
# 取值贴近 2026-08 实测水平，作为"禁止继续下滑"的护栏；
# 提高这些下限需要配套补齐测试（见审查报告 §8 修复优先级）。
_FILE_FLOORS: dict[str, float] = {
    "src/omnicrawler/security/policy.py": 80.0,
    "src/omnicrawler/security/egress.py": 80.0,
    "src/omnicrawler/fetching/http_client.py": 70.0,
    # --- P1-3 拆分产出的关键模块（2026-09-11 新增；同样按“本地实测 −8”预留 CI 余量）---
    "src/omnicrawler/plugins/plugins.py": 92.0,  # 纯门面（8 行语句），必须全覆盖
    "src/omnicrawler/state/state_store_records.py": 91.0,  # 实测 99.0%
    "src/omnicrawler/fetching/browser_engines.py": 86.0,  # 实测 93.9%
    "src/omnicrawler/state/state_store_runs.py": 79.0,  # 实测 87.2%
    "src/omnicrawler/plugins/plugin_loader.py": 77.0,  # 实测 84.9%
    "src/omnicrawler/fetching/browser_pool.py": 57.0,  # 实测 65.3%
    "src/omnicrawler/gui/views/plugin_market_install.py": 25.0,  # GUI Mixin，实测 32.5%
    "src/omnicrawler/gui/views/pdf_workbench_worker.py": 13.0,  # GUI worker，实测 20.5%
    "src/omnicrawler/apps/field_extractor.py": 30.0,
    "src/omnicrawler/apps/pdf_processor.py": 40.0,
}


def _normalise_for_match(path: str) -> str:
    """把 coverage.json 的 key 归一成 `src/...` 形式。

    coverage 的 json reporter 在部分环境（如 CI runner）输出**绝对路径**，
    而 GATES 的 matcher 写的是仓库相对路径 `src/omnicrawler/...`；此处统一
    截取到 `/src/` 之后，避免“报告里明明有该文件、门禁却报 missing”的误报
    （_file_coverage 早先已单独做过同类兜底，这里补齐分组门禁）。
    """
    norm = _normalise(path)
    marker = "/src/"
    if not norm.startswith("src/") and marker in norm:
        return "src/" + norm.split(marker, 1)[1]
    return norm


def _coverage(files: dict[str, Any], matcher: Matcher) -> tuple[int, int, float]:
    statements = covered = matched = 0
    for raw_path, details in files.items():
        path = _normalise_for_match(raw_path)
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
    norm = _normalise_for_match(raw_path)
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

    # 按顶层子包下限（P2-1 ratchet）：与分组门禁互补 —— 分组是跨包的功能视图，
    # 这里按“包”整体看待，防止某包整体滑落被其它包的高覆盖平均掉。
    for package, floor in sorted(PACKAGE_FLOORS.items()):
        prefix = "src/omnicrawler/" + package + "/"
        covered, statements, actual = _coverage(
            files, lambda path, _p=prefix: path.startswith(_p)
        )
        label = "pkg:" + package
        print(f"{label:29} {covered:5}/{statements:<7} {actual:7.2f}% {floor:7.2f}%")
        if actual < floor:
            failures.append(f"package {package} {actual:.2f}% < {floor:.2f}%")

    if failures:
        print("\nCoverage gates failed:", file=sys.stderr)
        for failure in failures:
            print(f"- {failure}", file=sys.stderr)
        return 1
    print("\nAll coverage gates passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
