"""Enforce per-platform portable artifact budgets and emit a size report."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any


def check_artifacts(
    directory: Path,
    platform: str,
    budgets: dict[str, Any],
) -> tuple[dict[str, Any], list[str]]:
    platform_budget = budgets.get(platform)
    if not isinstance(platform_budget, dict):
        return {}, [f"artifact budget has no platform {platform}"]
    pattern = re.compile(
        rf"^OmniCrawler-.+-{re.escape(platform)}-Portable-(Standard|Full)\."
        r"(?:zip|tar\.xz|tar\.gz|dmg)$",
        re.IGNORECASE,
    )
    artifacts: dict[str, Path] = {}
    for path in directory.iterdir() if directory.is_dir() else ():
        if not path.is_file():
            continue
        match = pattern.match(path.name)
        if match:
            artifacts[match.group(1).title()] = path

    report: dict[str, Any] = {"platform": platform, "artifacts": {}}
    errors: list[str] = []
    for edition in ("Standard", "Full"):
        artifact_path = artifacts.get(edition)
        budget = platform_budget.get(edition)
        if artifact_path is None:
            errors.append(f"missing {platform} {edition} portable artifact")
            continue
        if not isinstance(budget, dict) or "max_bytes" not in budget:
            errors.append(f"artifact budget missing {platform}/{edition}")
            continue
        size = artifact_path.stat().st_size
        maximum = int(budget["max_bytes"])
        report["artifacts"][edition] = {
            "name": artifact_path.name,
            "bytes": size,
            "mib": round(size / 1024**2, 1),
            "baseline_mib": budget.get("baseline_mib"),
            "max_bytes": maximum,
            "within_budget": size <= maximum,
        }
        if size > maximum:
            errors.append(
                f"{platform} {edition} artifact is {size / 1024**2:.1f} MiB; "
                f"budget is {maximum / 1024**2:.1f} MiB"
            )
    return report, errors


def main() -> int:
    project_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser()
    parser.add_argument("--platform", required=True, choices=("Windows", "Linux", "macOS"))
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument(
        "--budget-file",
        type=Path,
        default=project_root / "packaging" / "artifact-budgets.json",
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    budgets = json.loads(args.budget_file.read_text(encoding="utf-8"))
    report, errors = check_artifacts(args.directory, args.platform, budgets)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    for edition, item in report.get("artifacts", {}).items():
        print(f"{args.platform} {edition}: {item['mib']:.1f} MiB / {item['max_bytes'] / 1024**2:.1f} MiB")
    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
