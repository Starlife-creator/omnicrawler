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


def _directory_size(root: Path) -> int:
    total = 0
    for path in root.rglob("*"):
        try:
            if path.is_file() and not path.is_symlink():
                total += path.stat().st_size
        except OSError:
            continue
    return total
