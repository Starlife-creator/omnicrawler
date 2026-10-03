"""Strict, dependency-free compatibility checks for signed component manifests."""

from __future__ import annotations

import platform
import re
import sys
from collections.abc import Mapping
from typing import Any

from .._version import __version__
from ..core.versions import version_key
from .component_registry import safe_identifier


def satisfies(version: str, constraint: str) -> bool:
    if not constraint:
        return True
    if not re.fullmatch(r"\d+(?:\.\d+){0,3}", version):
        raise ValueError("带版本约束的组件必须使用数字版本")
    actual = version_key(version)
    actual += (0,) * (4 - len(actual))
    for clause in constraint.split(","):
        match = re.fullmatch(r"\s*(==|!=|>=|<=|>|<)\s*(\d+(?:\.\d+){0,3})\s*", clause)
        if not match:
            raise ValueError("组件版本约束格式无效")
        operator, raw = match.groups()
        expected = version_key(raw)
        expected += (0,) * (4 - len(expected))
        if not {"==": actual == expected, "!=": actual != expected, ">=": actual >= expected,
                "<=": actual <= expected, ">": actual > expected, "<": actual < expected}[operator]:
            return False
    return True


def dependency(value: str) -> tuple[str, str]:
    parts = re.split(r"(?=[<>=!])", value, maxsplit=1)
    name = safe_identifier(parts[0].strip())
    constraint = parts[1] if len(parts) > 1 else ""
    # Validate every clause even when the installed dependency has another version.
    if constraint:
        for clause in constraint.split(","):
            if not re.fullmatch(r"\s*(==|!=|>=|<=|>|<)\s*\d+(?:\.\d+){0,3}\s*", clause):
                raise ValueError("组件版本约束格式无效")
    return name, constraint


def check_entry(entry: Mapping[str, Any], installed: Mapping[str, Mapping[str, Any]]) -> None:
    if not satisfies(__version__, str(entry.get("core_version", ""))):
        raise ValueError("组件与当前核心版本不兼容")
    targets = entry.get("platforms", ())
    if targets and sys.platform not in targets:
        raise ValueError("组件与当前操作系统不兼容")
    machine = platform.machine().casefold()
    machine = {"amd64": "x86_64", "aarch64": "arm64"}.get(machine, machine)
    architectures = entry.get("architectures", ())
    if architectures and machine not in architectures:
        raise ValueError("组件与当前架构不兼容")
    for raw in entry.get("dependencies", ()):
        name, constraint = dependency(raw)
        if name not in installed:
            raise ValueError(f"缺少组件依赖: {name}")
        if not satisfies(str(installed[name]["version"]), constraint):
            raise ValueError(f"组件依赖版本不满足约束: {name}")


def check_registry(installed: Mapping[str, Mapping[str, Any]]) -> None:
    for entry in installed.values():
        check_entry(entry, installed)
    visited: set[str] = set()
    visiting: set[str] = set()

    def visit(name: str) -> None:
        if name in visiting:
            raise ValueError("组件依赖形成循环")
        if name in visited:
            return
        visiting.add(name)
        for raw in installed[name].get("dependencies", ()):
            visit(dependency(raw)[0])
        visiting.remove(name)
        visited.add(name)

    for name in installed:
        visit(name)
