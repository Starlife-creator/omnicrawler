"""Enforce the lightweight dependency boundaries that protect core execution.

This intentionally checks only stable, low-controversy rules.  It prevents new
delivery-layer dependencies from leaking into reusable core packages without
requiring a risky, one-off reorganisation of existing modules.
"""

from __future__ import annotations

import ast
import json
import sys
from collections.abc import Mapping
from pathlib import Path

FORBIDDEN_TARGETS: dict[str, frozenset[str]] = {
    "core": frozenset({"cli", "commands", "gui", "pdfx"}),
    "state": frozenset({"cli", "commands", "gui"}),
    "fetching": frozenset({"cli", "commands", "gui"}),
    "extraction": frozenset({"cli", "commands", "gui"}),
    "pipeline_ops": frozenset({"cli", "commands", "gui"}),
    "sdk": frozenset({"cli", "commands", "gui"}),
    "services": frozenset({"gui"}),
}

# Optional runtimes must not leak into the import-safe core.  Capability
# reporting is the sole exception: its job is to probe optional installations,
# and those imports happen only in explicit deep-verification paths.
OPTIONAL_RUNTIME_MODULES = frozenset(
    {
        "PIL",
        "PySide6",
        "bs4",
        "boto3",
        "crawl4ai",
        "cssselect",
        "curl_cffi",
        "cv2",
        "ddddocr",
        "duckdb",
        "httpx",
        "lxml",
        "numpy",
        "onnxruntime",
        "openpyxl",
        "opensearchpy",
        "paddle",
        "paddleocr",
        "patchright",
        "pdfplumber",
        "playwright",
        "psutil",
        "psycopg",
        "pyarrow",
        "pypdf",
        "pypdfium2",
        "pytesseract",
        "redis",
        "reportlab",
        "requests",
        "ruamel",
        "scrapy",
        "selectolax",
        "selenium",
        "websockets",
    }
)
FORBIDDEN_EXTERNALS: dict[str, frozenset[str]] = {
    "core": OPTIONAL_RUNTIME_MODULES,
}
EXTERNAL_EXEMPTIONS: dict[str, frozenset[str]] = {
    "omnicrawler.core.capabilities": OPTIONAL_RUNTIME_MODULES,
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


def _external_targets(tree: ast.AST) -> set[str]:
    """Return imported top-level third-party module names.

    Walking the whole tree intentionally includes lazy/function-local imports:
    they avoid startup cost, but still violate a package ownership boundary.
    """
    targets: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            targets.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            targets.add(node.module.split(".", 1)[0])
    return targets


def _module_graph(source_root: Path) -> dict[str, set[str]]:
    modules: dict[str, tuple[Path, bool]] = {}
    for path in sorted((source_root / "omnicrawler").rglob("*.py")):
        module, is_package = _module_name(source_root, path)
        modules[module] = (path, is_package)

    graph: dict[str, set[str]] = {module: set() for module in modules}
    for module, (path, is_package) in modules.items():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for raw_target in _targets(module, is_package, tree):
            target = raw_target
            while target and target not in modules:
                target = target.rpartition(".")[0]
            if target and target != module:
                graph[module].add(target)
    return graph


def _strongly_connected_components(graph: Mapping[str, set[str]]) -> list[set[str]]:
    """Return non-trivial strongly connected components using Tarjan's algorithm."""
    indices: dict[str, int] = {}
    lowlinks: dict[str, int] = {}
    stack: list[str] = []
    on_stack: set[str] = set()
    components: list[set[str]] = []

    def visit(module: str) -> None:
        indices[module] = lowlinks[module] = len(indices)
        stack.append(module)
        on_stack.add(module)
        for target in sorted(graph[module]):
            if target not in indices:
                visit(target)
                lowlinks[module] = min(lowlinks[module], lowlinks[target])
            elif target in on_stack:
                lowlinks[module] = min(lowlinks[module], indices[target])
        if lowlinks[module] != indices[module]:
            return
        component: set[str] = set()
        while stack:
            target = stack.pop()
            on_stack.remove(target)
            component.add(target)
            if target == module:
                break
        if len(component) > 1:
            components.append(component)

    for module in sorted(graph):
        if module not in indices:
            visit(module)
    return components


def cycle_metrics(source_root: Path) -> dict[str, int]:
    """Measure current static import cycles for a monotonic complexity budget."""
    graph = _module_graph(source_root)
    components = _strongly_connected_components(graph)
    return {
        "components": len(components),
        "modules": sum(len(component) for component in components),
        "edges": sum(
            1
            for component in components
            for source in component
            for target in graph[source]
            if target in component
        ),
        "largest_component": max((len(component) for component in components), default=0),
    }


def check_cycle_budget(source_root: Path, budget: Mapping[str, int]) -> list[str]:
    metrics = cycle_metrics(source_root)
    errors: list[str] = []
    for name, actual in metrics.items():
        maximum = int(budget[name])
        if actual > maximum:
            errors.append(
                f"import cycle budget exceeded for {name}: {actual} > {maximum}; "
                "remove a cyclic dependency or deliberately lower the architecture baseline"
            )
    return errors


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
                    f"{path.relative_to(source_root)} imports forbidden package {target}; "
                    f"{source_package} must remain reusable"
                )
        forbidden_externals = FORBIDDEN_EXTERNALS.get(source_package, frozenset())
        exempt = EXTERNAL_EXEMPTIONS.get(module, frozenset())
        for target in sorted((_external_targets(tree) & forbidden_externals) - exempt):
            errors.append(
                f"{path.relative_to(source_root)} imports optional module {target}; "
                f"{source_package} must remain import-safe"
            )
    return errors


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    errors = check(root / "src")
    budget_path = root / "tools" / "architecture-cycle-budget.json"
    budget = json.loads(budget_path.read_text(encoding="utf-8"))
    errors.extend(check_cycle_budget(root / "src", budget))
    if errors:
        print("Architecture dependency check failed:", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1
    metrics = cycle_metrics(root / "src")
    print(f"Architecture dependency check passed; cycle budget={metrics}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
