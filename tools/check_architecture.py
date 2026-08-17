"""Enforce the lightweight dependency boundaries that protect core execution.

This intentionally checks only stable, low-controversy rules.  It prevents new
delivery-layer dependencies from leaking into reusable core packages without
requiring a risky, one-off reorganisation of existing modules.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

FORBIDDEN_TARGETS: dict[str, frozenset[str]] = {
    "core": frozenset({"cli", "commands", "gui"}),
    "state": frozenset({"cli", "commands", "gui"}),
    "fetching": frozenset({"cli", "commands", "gui"}),
    "extraction": frozenset({"cli", "commands", "gui"}),
    "pipeline_ops": frozenset({"cli", "commands", "gui"}),
    "sdk": frozenset({"cli", "commands", "gui"}),
}


def _module_name(source_root: Path, path: Path) -> tuple[str, bool]:
    relative = path.relative_to(source_root).with_suffix("")
    parts = list(relative.parts)
    is_package = parts[-1] == "__init__"
    if is_package:
        parts.pop()
    return ".".join(parts), is_package


def _relative_target(module: str, is_package: bool, node: ast.ImportFrom) -> str | None:
    if node.level == 0:
        return node.module
    package = module.split(".") if is_package else module.split(".")[:-1]
    base = package[: len(package) - (node.level - 1)]
    if node.module:
        base.extend(node.module.split("."))
    return ".".join(base)


def _targets(module: str, is_package: bool, tree: ast.AST) -> set[str]:
    targets: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            targets.update(alias.name for alias in node.names if alias.name.startswith("omnicrawler."))
        elif isinstance(node, ast.ImportFrom):
            target = _relative_target(module, is_package, node)
            if target == "omnicrawler":
                # from omnicrawler import cli：别名是顶层子包，逐一展开才能被
                # 顶层包匹配捕获（P2-4）。符号导入（如 AppConfig）的第二段
                # 不在 forbidden 集合，不会误报。
                for alias in node.names:
                    if alias.name:
                        targets.add(f"omnicrawler.{alias.name}")
            elif target and target.startswith("omnicrawler."):
                targets.add(target)
    return targets


def check(source_root: Path) -> list[str]:
    errors: list[str] = []
    for path in sorted((source_root / "omnicrawler").rglob("*.py")):
        module, is_package = _module_name(source_root, path)
        parts = module.split(".")
        if len(parts) < 2:
            continue
        source_package = parts[1]
        forbidden = FORBIDDEN_TARGETS.get(source_package)
        if not forbidden:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for target in sorted(_targets(module, is_package, tree)):
            target_parts = target.split(".")
            if len(target_parts) > 1 and target_parts[1] in forbidden:
                errors.append(
                    f"{path.relative_to(source_root)} imports delivery layer {target}; "
                    f"{source_package} must remain reusable"
                )
    return errors


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    errors = check(root / "src")
    if errors:
        print("Architecture dependency check failed:", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1
    print("Architecture dependency check passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
