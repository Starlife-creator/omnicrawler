"""Bounded OS file locks, released by the kernel when a process exits."""

from __future__ import annotations

import importlib
import os
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def file_lock(path: Path, *, timeout: float = 10.0, busy_message: str = "资源正在使用，请稍后重试") -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as stream:
        if path.stat().st_size == 0:
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
                    raise TimeoutError(busy_message) from exc
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
