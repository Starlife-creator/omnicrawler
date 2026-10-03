"""Cross-process transactions for the shared component registry."""

from __future__ import annotations

import importlib
import json
import os
import re
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

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
    lock_path = contained_path(root, ".registry.lock")
    with lock_path.open("a+b") as stream:
        if lock_path.stat().st_size == 0:
            stream.write(b"\0")
            stream.flush()
        deadline = time.monotonic() + timeout
        while True:
            stream.seek(0)
            try:
                if os.name == "nt":
                    windows_lock = importlib.import_module("msvcrt")
                    windows_lock.locking(stream.fileno(), windows_lock.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError as exc:
                if time.monotonic() >= deadline:
                    raise TimeoutError("组件注册表正在使用，请稍后重试") from exc
                time.sleep(0.05)
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                windows_lock = importlib.import_module("msvcrt")
                windows_lock.locking(stream.fileno(), windows_lock.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def read_document(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("组件注册表格式无效")
    return value


def commit_document(root: Path, value: dict[str, Any]) -> None:
    """Immutable version directories make replay of the manifest commit safe."""
    journal = contained_path(root, ".registry.pending.json")
    raw = json.dumps(value, ensure_ascii=False, indent=2).encode("utf-8")
    atomic_write(journal, raw)
    atomic_write(contained_path(root, "installed.json"), raw)
    journal.unlink()


def recover_document(root: Path) -> None:
    journal = contained_path(root, ".registry.pending.json")
    if journal.exists():
        value = read_document(journal)
        atomic_write(contained_path(root, "installed.json"), json.dumps(value, ensure_ascii=False, indent=2).encode("utf-8"))
        journal.unlink()
