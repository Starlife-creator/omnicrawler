"""Coordinate task resources with session editing across processes."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from ..core.file_lock import file_lock


@contextmanager
def session_lease(workspace: Path) -> Iterator[None]:
    with file_lock(workspace / ".session-use.lock", timeout=0,
                   busy_message="任务或登录窗口仍在使用此工作区会话，请先停止并等待资源关闭"):
        yield
