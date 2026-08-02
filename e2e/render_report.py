from __future__ import annotations

import argparse
import json
import tomllib
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from pathlib import Path


def _junit_counts(path: Path) -> dict[str, int]:
    if not path.is_file():
        return {"tests": 0, "failures": 0, "errors": 0, "skipped": 0}
    root = ET.parse(path).getroot()
    nodes = [root] if root.tag == "testsuite" else list(root.findall(".//testsuite"))
    return {
        key: sum(int(node.attrib.get(key, "0")) for node in nodes)
        for key in ("tests", "failures", "errors", "skipped")
    }


def project_version(root: Path | None = None) -> str:
    """Read the package version without importing optional application dependencies."""
    project_root = root or Path(__file__).resolve().parents[1]
    metadata = tomllib.loads((project_root / "pyproject.toml").read_text(encoding="utf-8"))
    return str(metadata["project"]["version"])


def build_report(
    *,
    counts: dict[str, int],
    total_coverage: float | None,
    pytest_exit: int,
    coverage_target: float,
    expected_tests: int,
    browser_enabled: bool,
    version: str,
) -> str:
    scenario_rate = (
        (counts["tests"] - counts["failures"] - counts["errors"] - counts["skipped"]) * 100 / counts["tests"]
        if counts["tests"]
        else 0.0
    )
    passed = (
        pytest_exit == 0
        and counts["tests"] == expected_tests
        and counts["failures"] == 0
        and counts["errors"] == 0
        and counts["skipped"] == 0
    )
    coverage_passed = total_coverage is not None and total_coverage >= coverage_target
    status = "通过" if passed and coverage_passed else "未通过"
    coverage_text = f"{total_coverage:.2f}%" if total_coverage is not None else "未生成"
    profile = "核心本地链路 + 本地 Chromium 扩展" if browser_enabled else "核心本地链路"
    return "\n".join([
        f"# OmniCrawler {version} E2E 测试结果", "",
        f"- 生成时间（UTC）：{datetime.now(UTC).isoformat(timespec='seconds')}",
        f"- 执行配置：{profile}",
        "- 覆盖范围：`e2e.harness` 与 `e2e.render_report`；不把 E2E 结果伪装为全源码覆盖率。",
        f"- 总结：**{status}**", f"- Pytest 退出码：{pytest_exit}", "",
        "| 指标 | 结果 | 目标 | 状态 |", "| --- | ---: | ---: | --- |",
        f"| 本地 E2E 通过率 | {scenario_rate:.2f}% | 100.00% | {'通过' if passed else '未通过'} |",
        f"| E2E 支撑代码行覆盖率 | {coverage_text} | {coverage_target:.2f}% | {'通过' if coverage_passed else '未通过'} |",
        f"| 测试总数 | {counts['tests']} | {expected_tests} | {'通过' if counts['tests'] == expected_tests else '未通过'} |",
        f"| 失败 / 错误 / 跳过 | {counts['failures']} / {counts['errors']} / {counts['skipped']} | 0 / 0 / 0 | {'通过' if passed else '未通过'} |",
        "", "## 已验证链路", "",
        "- 本地 HTML 抓取、PDF 下载、字段提取、结构化交付与幂等重跑。",
        "- CLI 配置校验与可解释执行计划。",
        "- 可选：本地 Chromium 动态渲染、XHR JSON 捕获和浏览器池复用。",
        "", "原始产物位于 `e2e-artifacts/`：`junit.xml`、`coverage.xml`、`coverage.json` 与 `coverage.txt`。",
    ]) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Create the reusable E2E Markdown report")
    parser.add_argument("--artifacts", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--pytest-exit", required=True, type=int)
    parser.add_argument("--coverage-target", type=float, default=95.0)
    parser.add_argument("--expected-tests", required=True, type=int)
    parser.add_argument("--browser", action="store_true")
    args = parser.parse_args()

    counts = _junit_counts(args.artifacts / "junit.xml")
    coverage_path = args.artifacts / "coverage.json"
    total = None
    if coverage_path.is_file():
        total = float(json.loads(coverage_path.read_text(encoding="utf-8"))["totals"]["percent_covered"])
    args.output.write_text(
        build_report(
            counts=counts, total_coverage=total, pytest_exit=args.pytest_exit,
            coverage_target=args.coverage_target, expected_tests=args.expected_tests,
            browser_enabled=args.browser, version=project_version(),
        ),
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
