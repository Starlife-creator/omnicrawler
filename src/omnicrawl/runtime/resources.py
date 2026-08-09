from __future__ import annotations

import shutil
import time
from pathlib import Path

from ..core.config import AppConfig


class ResourceLimitError(RuntimeError):
    """Raised when continuing a run could exhaust a local machine resource."""


class ResourceGuard:
    def __init__(self, config: AppConfig) -> None:
        settings = config.section("resources")
        self.workspace = config.workspace.resolve()
        self.minimum_free_disk_bytes = max(0, int(settings.get("minimum_free_disk_bytes", 0)))
        self.maximum_runtime_seconds = max(0.0, float(settings.get("maximum_runtime_seconds", 0)))
        self.maximum_workspace_bytes = max(0, int(settings.get("maximum_workspace_bytes", 0)))
        self.check_interval_seconds = max(0.0, float(settings.get("check_interval_seconds", 5)))
        self.started = time.monotonic()
        self._last_check = 0.0

    def check(self, *, force: bool = False) -> dict[str, int | float]:
        now = time.monotonic()
        elapsed = now - self.started
        if self.maximum_runtime_seconds and elapsed >= self.maximum_runtime_seconds:
            raise ResourceLimitError(
                f"Maximum runtime reached ({self.maximum_runtime_seconds:g} seconds)"
            )
        if not force and now - self._last_check < self.check_interval_seconds:
            return {"elapsed_seconds": elapsed}
        self._last_check = now

        usage = shutil.disk_usage(self.workspace)
        if self.minimum_free_disk_bytes and usage.free < self.minimum_free_disk_bytes:
            raise ResourceLimitError(
                f"Free disk space {usage.free} bytes is below safety reserve "
                f"{self.minimum_free_disk_bytes} bytes"
            )
        workspace_bytes = _directory_size(self.workspace) if self.maximum_workspace_bytes else 0
        if self.maximum_workspace_bytes and workspace_bytes >= self.maximum_workspace_bytes:
            raise ResourceLimitError(
                f"Workspace size {workspace_bytes} bytes reached limit {self.maximum_workspace_bytes} bytes"
            )
        return {
            "elapsed_seconds": elapsed,
            "disk_free_bytes": usage.free,
            "workspace_bytes": workspace_bytes,
        }


# S2.5.27：工作区大小缓存——目录 mtime 摘要不变时跳过全量 stat
_SIZE_CACHE: dict[Path, tuple[int, int]] = {}


def _directory_size(root: Path) -> int:
    key = _tree_mtime_digest(root)
    cached = _SIZE_CACHE.get(root)
    if cached is not None and cached[1] == key:
        return cached[0]
    total = 0
    for path in root.rglob("*"):
        try:
            if path.is_file() and not path.is_symlink():
                total += path.stat().st_size
        except OSError:
            continue
    _SIZE_CACHE[root] = (total, key)
    return total


def _tree_mtime_digest(root: Path) -> int:
    """目录树变化指纹：目录 mtime 摘要 + 文件数量。

    文件写入通常更新父目录 mtime，但 Windows 上并不总是可靠，
    因此把文件计数一并纳入（新增/删除文件必然改变指纹）。
    """
    digest = 0
    file_count = 0
    for entry in root.rglob("*"):
        if entry.is_dir():
            try:
                digest = (digest * 31 + entry.stat().st_mtime_ns) % (2**64)
            except OSError:
                continue
        elif entry.is_file() and not entry.is_symlink():
            file_count += 1
    return (digest * 31 + file_count) % (2**64)
