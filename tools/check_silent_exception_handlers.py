"""Fail when a broad `except` silently swallows an exception without saying why.

## Why this exists

An `except Exception: pass` (or `: continue`) makes a failure indistinguishable from
success: the caller sees no error, the log has no line, and the only symptom is that
something downstream behaves oddly much later. Two real cases in this repo were
"external edit of the YAML file is never noticed" and "XPath probe failed vs. this
level genuinely has no sibling elements look identical".

Swallowing an exception is sometimes correct (optional dependency missing, best-effort
cleanup, teardown paths). The problem is not the swallow, it is the **silence**. So the
rule is:

- narrow handler types (`ImportError`, `KeyError`, ...) may swallow silently — the type
  itself documents the expected condition;
- broad handler types (`Exception`, `BaseException`, or a bare `except:`) whose entire
  body is a single `pass` / `continue` must carry an explicit `# noqa: BLE001` marker on
  the `except` line, i.e. somebody looked at it and decided the silence is intended;
- anything else should log or re-raise.

## Criteria (same discipline as the rest of tools/)

- the marker must be on the `except` line itself — a marker elsewhere in the file does
  not count, otherwise one comment would excuse every handler below it;
- scanning zero handlers is a failure ("empty selection is not a pass"), because an empty
  set makes every assertion trivially true;
- `except Exception: pass` written across several lines is still one statement, so line
  numbers are reported from the AST, not from a text regex.

## Usage

    python tools/check_silent_exception_handlers.py            # check the source tree
    python tools/check_silent_exception_handlers.py --json     # machine readable
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = REPO_ROOT / "src" / "omnicrawler"

#: Handler types wide enough that the type alone does not explain the silence.
WIDE_HANDLER_NAMES = frozenset({"Exception", "BaseException"})

#: Marker that records "somebody reviewed this silence on purpose".
_EXEMPTION = re.compile(r"#.*\bnoqa\b[^#]*\bBLE001\b")


@dataclass(frozen=True, slots=True)
class SilentHandler:
    """One `except ...: pass|continue` site."""

    path: str
    line: int
    type_name: str
    body: str
    exempt: bool

    def describe(self) -> str:
        marker = "" if self.exempt else "  (missing '# noqa: BLE001')"
        return f"{self.path}:{self.line} except {self.type_name}: {self.body}{marker}"


def _handler_type_names(handler: ast.ExceptHandler) -> list[str]:
    """Return the handler's type names; a bare `except:` yields `["<bare>"]`."""
    if handler.type is None:
        return ["<bare>"]
    node = handler.type
    if isinstance(node, ast.Tuple):
        names: list[str] = []
        for element in node.elts:
            names.append(ast.unparse(element))
        return names
    return [ast.unparse(node)]


def _is_silent(handler: ast.ExceptHandler) -> bool:
    """True when the whole handler body is a single `pass` or `continue`."""
    if len(handler.body) != 1:
        return False
    statement = handler.body[0]
    return isinstance(statement, ast.Pass | ast.Continue)


def _is_wide(names: list[str]) -> bool:
    return any(name in WIDE_HANDLER_NAMES or name == "<bare>" for name in names)


def scan_source(source: str, *, path: str) -> tuple[list[SilentHandler], list[str]]:
    """Scan one module's source. Returns (silent handlers, problems)."""
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return [], [f"{path}: cannot parse ({exc.msg} at line {exc.lineno})"]
    lines = source.splitlines()
    handlers: list[SilentHandler] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ExceptHandler):
            continue
        if not _is_silent(node):
            continue
        names = _handler_type_names(node)
        line_text = lines[node.lineno - 1] if 0 < node.lineno <= len(lines) else ""
        handlers.append(
            SilentHandler(
                path=path,
                line=node.lineno,
                type_name=", ".join(names),
                body="pass" if isinstance(node.body[0], ast.Pass) else "continue",
                exempt=bool(_EXEMPTION.search(line_text)),
            )
        )
    return handlers, []


def collect(root: Path = SOURCE_ROOT) -> tuple[list[SilentHandler], list[str]]:
    """Scan every module under `root`."""
    handlers: list[SilentHandler] = []
    problems: list[str] = []
    for path in sorted(root.rglob("*.py")):
        try:
            source = path.read_text(encoding="utf-8")
        except OSError as exc:
            problems.append(f"{path}: cannot read ({exc})")
            continue
        found, file_problems = scan_source(source, path=str(path.relative_to(REPO_ROOT)))
        handlers.extend(found)
        problems.extend(file_problems)
    return handlers, problems


def check(root: Path = SOURCE_ROOT) -> list[str]:
    """Return the list of problems; empty list means the tree is clean."""
    handlers, problems = collect(root)
    if not handlers:
        problems.append(
            "empty selection is not a pass: scanned zero 'except ...: pass|continue' "
            "handlers (wrong root, or the scanner stopped matching)"
        )
        return problems
    for handler in handlers:
        if _is_wide([name.strip() for name in handler.type_name.split(",")]):
            if not handler.exempt:
                problems.append(handler.describe())
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=str(SOURCE_ROOT))
    parser.add_argument("--json", action="store_true", help="emit machine readable report")
    args = parser.parse_args(argv)
    handlers, read_problems = collect(Path(args.root))
    problems = list(read_problems)
    if not handlers:
        problems.append("empty selection is not a pass: scanned zero silent handlers")
    else:
        for handler in handlers:
            if _is_wide([name.strip() for name in handler.type_name.split(",")]):
                if not handler.exempt:
                    problems.append(handler.describe())
    if args.json:
        payload = {
            "ok": not problems,
            "silent_handlers": len(handlers),
            "exempt_wide_handlers": sum(
                1
                for handler in handlers
                if handler.exempt
                and _is_wide([name.strip() for name in handler.type_name.split(",")])
            ),
            "problems": problems,
        }
        print(json.dumps(payload, ensure_ascii=True, indent=2))
        return 0 if not problems else 1
    if problems:
        for problem in problems:
            print(f"ERROR  {problem}", file=sys.stderr)
        print(f"silent exception handlers: {len(problems)} problem(s)", file=sys.stderr)
        return 1
    print(
        f"silent exception handlers: {len(handlers)} silent handler(s), "
        "every broad one carries an explicit '# noqa: BLE001' marker"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
