"""OS-owned database leases prevent maintenance from replacing a live store."""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from pathlib import Path
from uuid import uuid4

from .file_lock import file_lock


def pending_restore_path(database: Path) -> Path:
    return database.resolve().with_name(f".{database.name}.restore.pending.json")


def _paths(database: Path) -> tuple[Path, Path]:
    database = database.resolve()
    return database.with_name(f".{database.name}.maintenance.lock"), database.with_name(f".{database.name}.leases")


@contextmanager
def database_lease(database: Path) -> Iterator[None]:
    if str(database) == ":memory:":
        yield
        return
    gate, directory = _paths(database)
    held = ExitStack()
    lease = directory / f"{uuid4().hex}.lock"
    try:
        with file_lock(gate):
            if pending_restore_path(database).exists():
                raise RuntimeError("工作区回滚尚未恢复；请先执行工作区健康检查或回滚恢复")
            held.enter_context(file_lock(lease, timeout=0))
        yield
    finally:
        held.close()
        lease.unlink(missing_ok=True)


@contextmanager
def database_maintenance(database: Path) -> Iterator[None]:
    gate, directory = _paths(database)
    with file_lock(gate, busy_message="数据库维护正在进行"):
        stale = list(directory.glob("*.lock"))
        with ExitStack() as held:
            for lease in stale:
                if not lease.exists():
                    continue
                try:
                    held.enter_context(file_lock(lease, timeout=0))
                except TimeoutError as exc:
                    raise RuntimeError("数据库正在使用，请先停止任务并关闭工作区相关视图") from exc
            yield
        for lease in stale:
            lease.unlink(missing_ok=True)
