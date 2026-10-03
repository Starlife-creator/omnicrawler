"""Cross-process transactions for the shared component registry."""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from ..core.file_lock import file_lock
from ..core.utils import atomic_write


def safe_identifier(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", value) or value.endswith("."):
        raise ValueError("不安全的组件名称或版本")
    if value.split(".")[0].upper() in {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}:
        raise ValueError("不安全的组件名称或版本")
    return value


def contained_path(root: Path, value: str) -> Path:
    result = (root / value).resolve()
    if root not in result.parents:
        raise ValueError("组件路径越出组件根目录")
    return result


@contextmanager
def registry_lock(root: Path, *, timeout: float = 10.0) -> Iterator[None]:
    """OS locks are released automatically if the owning process exits."""
    with file_lock(contained_path(root, ".registry.lock"), timeout=timeout,
                   busy_message="组件注册表正在使用，请稍后重试"):
        yield


def read_document(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("组件注册表格式无效")
    return value


def commit_document(root: Path, value: dict[str, Any], previous: dict[str, Any] | None = None) -> None:
    """Immutable version directories make replay of the manifest commit safe."""
    journal = contained_path(root, ".registry.pending.json")
    raw = json.dumps({"format": 1, "installed": value, "previous": previous or {}}, ensure_ascii=False, indent=2).encode("utf-8")
    atomic_write(journal, raw)
    recover_document(root)


def recover_document(root: Path) -> None:
    journal = contained_path(root, ".registry.pending.json")
    if journal.exists():
        value = read_document(journal)
        if value.get("format") == 1:
            for name, entry in value["previous"].items():
                rollback = contained_path(root, f".rollback/{safe_identifier(name)}.json")
                rollback.parent.mkdir(exist_ok=True)
                atomic_write(rollback, json.dumps(entry, ensure_ascii=False).encode("utf-8"))
            value = value["installed"]
        atomic_write(contained_path(root, "installed.json"), json.dumps(value, ensure_ascii=False, indent=2).encode("utf-8"))
        journal.unlink()
