"""Select older stable update baselines using the project version, never a branch name."""

from __future__ import annotations

import argparse
import re
import subprocess
import tomllib
from pathlib import Path


def select_baselines(tags: list[str], current: str, *, enabled: bool, limit: int = 3) -> list[str]:
    if not enabled:
        return []
    if not re.fullmatch(r"\d+\.\d+\.\d+", current):
        raise ValueError("Project version must be a stable three-part version")
    target = tuple(map(int, current.split(".")))
    candidates = set()
    for tag in tags:
        if re.fullmatch(r"v\d+\.\d+\.\d+", tag):
            version = tuple(map(int, tag[1:].split(".")))
            if version < target:
                candidates.add(version)
    return [".".join(map(str, version)) for version in sorted(candidates, reverse=True)[:limit]]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--enabled", action="store_true")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    current = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    tags = subprocess.run(["git", "-c", "gc.auto=0", "tag", "--list"], cwd=root,
                          check=True, capture_output=True, text=True).stdout.splitlines() if args.enabled else []
    print(",".join(select_baselines(tags, current, enabled=args.enabled)))


if __name__ == "__main__":
    main()
