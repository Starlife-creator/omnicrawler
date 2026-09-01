"""Validate the versioned public SDK surface without importing the package."""

from __future__ import annotations

import ast
import json
import sys
from pathlib import Path
from typing import Any


def _assignments(path: Path) -> dict[str, Any]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    values: dict[str, Any] = {}
    for node in tree.body:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        value = node.value
        if value is None:
            continue
        for target in targets:
            if isinstance(target, ast.Name) and target.id in {"__all__", "SDK_VERSION", "API_STABILITY"}:
                values[target.id] = ast.literal_eval(value)
    return values


def check(project_root: Path) -> list[str]:
    expected = json.loads(
        (project_root / "schemas" / "sdk-public-api.json").read_text(encoding="utf-8")
    )
    sdk = _assignments(project_root / "src" / "omnicrawler" / "sdk" / "__init__.py")
    plugin_sdk = _assignments(
        project_root / "src" / "omnicrawler" / "plugins" / "plugin_sdk.py"
    )
    actual = {
        "sdk_version": sdk.get("SDK_VERSION"),
        "sdk_exports": sorted(sdk.get("__all__", [])),
        "stability": sdk.get("API_STABILITY"),
        "plugin_sdk_exports": sorted(plugin_sdk.get("__all__", [])),
    }
    errors: list[str] = []
    for key, expected_value in expected.items():
        if actual.get(key) != expected_value:
            errors.append(
                f"SDK contract drift for {key}: expected {expected_value!r}, "
                f"got {actual.get(key)!r}; update the versioned contract deliberately"
            )
    return errors


def main() -> int:
    project_root = Path(__file__).resolve().parents[1]
    errors = check(project_root)
    if errors:
        print("SDK public API check failed:", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1
    print("SDK public API check passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
