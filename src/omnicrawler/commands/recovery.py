from __future__ import annotations

from pathlib import Path
from typing import Any

from ..core.config import load_config
from ..pipeline import Pipeline
from ..runtime.recovery import RecoveryCenter


def execute(config: str, action: str, *, limit: int | None = None, backup: str = "") -> dict[str, Any]:
    loaded = load_config(config)
    center = RecoveryCenter(loaded)
    if action == "overview":
        return center.overview()
    if action == "continue":
        return center.continue_incomplete()
    if action == "retry-failed":
        return center.retry_failed(limit)
    if action == "relogin":
        return center.reset_login()
    if action == "reprocess":
        with Pipeline(loaded) as pipeline:
            return pipeline.reprocess_records()
    if action == "rollback-config":
        if not backup:
            raise ValueError("rollback-config必须提供--backup")
        return center.rollback_config(Path(backup))
    raise ValueError(f"未知恢复操作: {action}")
