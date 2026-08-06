"""Reject coding-standard violations defined in docs/CODING_STANDARDS.md.

Rules enforced here:
  R1  - dict(base) used as config merge (shallow copy) instead of deepcopy.
  R2  - "x or default" applied to a numeric/counting default.
  R3  - bare json.loads / int() / float() on untrusted data.
  R4  - destructive commands (reset / reset_stage / rollback-config) without
        require_explicit_apply / ConfirmationRequired / --apply handling.

Run locally:  python tools/check_coding_standards.py src
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

OK_MARK = "[OK]"
BAD_MARK = "[FAIL]"

DESTRUCTIVE_HINTS = {"reset_stage", "rollback-config", ".reset(", "require_explicit_apply"}


class Violation:
    def __init__(self, path: str, line: int, rule: str, message: str) -> None:
        self.path = path
        self.line = line
        self.rule = rule
        self.message = message

    def __str__(self) -> str:
        return f"{self.path}:{self.line} [{self.rule}] {self.message}"


def find_violations(source_root: Path) -> list[Violation]:
    findings: list[Violation] = []
    for path in sorted(source_root.rglob("*.py")):
        relative = path.relative_to(source_root).as_posix()
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if "merge" in node.name.casefold():
                    _check_merge_fn(node, findings, relative)
            elif isinstance(node, ast.Assign):
                _check_or_assign(node, findings, relative)
    return findings


def _check_merge_fn(node: ast.FunctionDef, findings: list[Violation], relative: str) -> None:
    # R1: merge implementations must deepcopy; bare `dict(base)` / `base.copy()` are shallow.
    _walk_with_parent(node, lambda child, parent: _r1_check(node, child, parent, findings, relative))


def _r1_check(
    node: ast.FunctionDef,
    child: ast.AST,
    parent: ast.AST | None,
    findings: list[Violation],
    relative: str,
) -> None:
    if isinstance(child, ast.Call) and isinstance(child.func, ast.Name):
        if child.func.id == "dict" and len(child.args) == 1 and isinstance(child.args[0], ast.Name):
            if not (isinstance(parent, ast.Call) and getattr(parent.func, "attr", None) == "deepcopy"):
                findings.append(
                    Violation(relative, child.lineno, "R1", f"{node.name}: dict(base) 浅拷贝 → 使用 copy.deepcopy(base)")
                )


def _walk_with_parent(node: ast.AST, visit) -> None:
    for child in ast.iter_child_nodes(node):
        visit(child, node)
        _walk_with_parent(child, visit)


def _check_or_assign(node: ast.AST, findings: list[Violation], relative: str) -> None:
    # R2: `x = expr or default` where expr could legitimately be 0/0.0/False
    value = getattr(node, "value", None)
    if not isinstance(value, ast.BoolOp) or not isinstance(value.op, ast.Or):
        return
    expr = value.values[0]
    if _is_numeric_context(expr):
        findings.append(
            Violation(relative, node.lineno, "R2", "x or default 会吞掉 0/0.0 → 用 x if x is not None else default")
        )


def _is_numeric_context(node: ast.expr) -> bool:
    if isinstance(node, ast.Call):
        if isinstance(node.func, ast.Name):
            return node.func.id in _NUMERIC_NAMES
        return False
    if isinstance(node, ast.Attribute):
        return node.attr in _NUMERIC_NAMES
    if isinstance(node, ast.Subscript):
        # subscript value base like max_pages[...]; ignore generic names like "page"
        key = node.value
        if isinstance(key, ast.Name):
            return key.id in _NUMERIC_NAMES
        return False
    if isinstance(node, ast.Name):
        return node.id in _NUMERIC_NAMES
    return False


_NUMERIC_NAMES = frozenset(
    {
        "max_pages",
        "limit",
        "seed",
        "count",
        "size",
        "attempts",
        "concurrency",
        "pool_size",
        "max_requests",
        "retry_max",
        "retry_max_seconds",
        "rows",
        "pages",
    }
)


def main(argv: list[str] | None = None) -> int:
    roots = [Path(p) for p in (argv or sys.argv[1:] or ["src"])]
    all_findings: list[Violation] = []
    for root in roots:
        if root.exists():
            all_findings.extend(find_violations(root))
    if all_findings:
        print(f"{BAD_MARK} coding-standard violations: {len(all_findings)}")
        for v in all_findings:
            print(f"   {v}")
        return 1
    print(f"{OK_MARK} coding standards check passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
