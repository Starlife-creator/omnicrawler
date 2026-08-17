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


def alias_map(tree: ast.AST) -> dict[str, str]:
    """本文件内导入别名 → 完全限定名映射。

    覆盖 `import x as y`、`from a.b import c as d` 与裸 `import x`、
    `from a.b import c`（本地名 = 末段），使别名调用的裸 Name 也能
    回溯到真实模块（P2-1，防止 `from urllib.request import urlopen`
    后 `urlopen()` 绕过门禁）。
    """
    aliases: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                local = alias.asname or alias.name.split(".")[0]
                aliases[local] = alias.name
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            for alias in node.names:
                local = alias.asname or alias.name
                target = f"{module}.{alias.name}" if module else alias.name
                aliases[local] = target
    return aliases


def resolve_transport(name: str | None, aliases: dict[str, str]) -> str | None:
    """把调用名经别名映射解析为完全限定名；首段非本地别名则原样返回。

    注意：`import urllib.request` 会把 `urllib` 记录为别名；此时调用
    `urllib.request.urlopen` 已是完整限定名，别名目标只是其前缀，不应
    替换（否则会拼出 `urllib.request.request.urlopen` 而漏检）。
    """
    if name is None:
        return None
    first, _, rest = name.partition(".")
    if first in aliases:
        target = aliases[first]
        if name == target or name.startswith(target + "."):
            return name  # 别名目标只是完整限定名的前缀，无需替换
        return f"{target}.{rest}" if rest else target
    return name


def find_direct_calls(source_root: Path) -> list[tuple[str, int, str]]:
    findings: list[tuple[str, int, str]] = []
    for path in sorted(source_root.rglob("*.py")):
        relative = path.relative_to(source_root).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        aliases = alias_map(tree)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = resolve_transport(dotted_name(node.func), aliases)
            if name in DIRECT_TRANSPORTS:
                findings.append((relative, node.lineno, name))
    return findings


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, default=Path("src/omnicrawler"))
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
