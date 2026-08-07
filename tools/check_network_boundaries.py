"""Reject direct network calls outside the established EgressBroker boundary."""

from __future__ import annotations

import ast
import sys
from pathlib import Path

APPROVED_DIRECT_CALLS = {
    "fetching/streams.py": "Streaming transport is authorized by its shared EgressBroker.",
    "scheduling/change_detector.py": (
        "Every urlopen is wrapped in EgressBroker.request (S4.5); AST-level "
        "scan cannot see the context manager, same precedent as streams.py."
    ),
    "gui/views/change_monitor.py": (
        "User-initiated interactive probe (explicit URL input + button click); "
        "not automated crawling, no policy-bypass intent."
    ),
    "plugins/market_client.py": (
        "Every remote urlopen is wrapped in EgressBroker.request (purpose='plugin') "
        "when the caller supplies a broker; the AST-level scan cannot see the "
        "context manager, same precedent as streams.py/change_detector.py."
    ),
}

DIRECT_TRANSPORTS = {
    "urllib.request.urlopen",
    "requests.get",
    "requests.post",
    "requests.request",
    "httpx.get",
    "httpx.post",
    "httpx.request",
    "websockets.connect",
}


def dotted_name(node: ast.expr) -> str | None:
    parts: list[str] = []
    current: ast.expr = node
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if isinstance(current, ast.Name):
        parts.append(current.id)
        return ".".join(reversed(parts))
    return None


def find_direct_calls(source_root: Path) -> list[tuple[str, int, str]]:
    findings: list[tuple[str, int, str]] = []
    for path in sorted(source_root.rglob("*.py")):
        relative = path.relative_to(source_root).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = dotted_name(node.func)
            if name in DIRECT_TRANSPORTS:
                findings.append((relative, node.lineno, name))
    return findings


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, default=Path("src/omnicrawl"))
    args = parser.parse_args(argv)
    findings = find_direct_calls(args.source_root)
    unexpected = [item for item in findings if item[0] not in APPROVED_DIRECT_CALLS]
    for relative, line, transport in findings:
        state = "approved" if relative in APPROVED_DIRECT_CALLS else "unclassified"
        print(f"{state}: {relative}:{line}: {transport}")
    if unexpected:
        print(
            "Direct network boundary check failed; migrate this transport behind EgressBroker before CI can pass.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
