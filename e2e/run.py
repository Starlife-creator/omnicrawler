"""Run the local reusable E2E suite with an honest, scoped coverage gate."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path


def command(python: str, *args: str) -> list[str]:
    return [python, *args]


def run(args: argparse.Namespace) -> int:
    root = Path(__file__).resolve().parents[1]
    artifacts = root / "e2e-artifacts"
    report = root / "docs" / "E2E_TEST_REPORT.md"
    shutil.rmtree(artifacts, ignore_errors=True)
    artifacts.mkdir(parents=True, exist_ok=True)
    environment = os.environ.copy()
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    environment["OMNICRAWL_BROWSER_TESTS"] = "1" if args.browser else "0"
    marker = "e2e" if args.browser else "e2e and not e2e_browser"
    expected_tests = 4 if args.browser else 3
    if args.full_regression:
        regression = subprocess.run(
            command(
                args.python, "-m", "pytest", "-q", "tests",
                "--basetemp", str(artifacts / "full-regression-tmp"),
            ),
            cwd=root,
            env=environment,
        )
        if regression.returncode:
            return regression.returncode
    subprocess.run(command(args.python, "-m", "coverage", "erase"), cwd=root, env=environment, check=True)
    pytest = subprocess.run(
        command(
            args.python, "-m", "coverage", "run", "--source=e2e.harness,e2e.render_report", "-m", "pytest", "-q",
            "--basetemp", str(artifacts / "pytest-tmp"), "-m", marker, "e2e/tests",
            "--junitxml", str(artifacts / "junit.xml"),
        ),
        cwd=root,
        env=environment,
    )
    for format_name, destination in (("xml", "coverage.xml"), ("json", "coverage.json")):
        subprocess.run(command(args.python, "-m", "coverage", format_name, "-o", str(artifacts / destination)), cwd=root, env=environment)
    with (artifacts / "coverage.txt").open("w", encoding="utf-8") as handle:
        subprocess.run(command(args.python, "-m", "coverage", "report"), cwd=root, env=environment, stdout=handle)
    subprocess.run(
        command(
            args.python, "e2e/render_report.py", "--artifacts", str(artifacts), "--output", str(report),
            "--pytest-exit", str(pytest.returncode), "--coverage-target", str(args.coverage_target),
            "--expected-tests", str(expected_tests), *( ["--browser"] if args.browser else [] ),
        ),
        cwd=root,
        env=environment,
        check=True,
    )
    coverage = subprocess.run(
        command(args.python, "-m", "coverage", "report", f"--fail-under={args.coverage_target}"),
        cwd=root,
        env=environment,
    )
    return pytest.returncode or coverage.returncode


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--coverage-target", type=float, default=float(os.environ.get("E2E_COVERAGE_TARGET", "95")))
    parser.add_argument("--browser", action="store_true", help="Run the local Chromium extension too")
    parser.add_argument("--full-regression", action="store_true", help="Run tests/ before the E2E suite")
    return run(parser.parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
