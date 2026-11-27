from __future__ import annotations

from pathlib import Path
from typing import Any

from ..core.config import load_config

# ★ 2026-11-27：两个重模块都改为**用到时**才导入。
#
#   实测（`import omnicrawler.cli._handlers` 的 importtime）：
#
#       `omnicrawler.runtime.recovery`   137 ms   ← 只有 recovery 命令用
#       `omnicrawler.pipeline`           418 ms   ← 只有 `reprocess` 分支用
#
#   而本模块被 `cli._handlers` **顶层导入** ⇒ 这两笔会传染给每一条 CLI 命令，
#   包括 `--help` 与 `status`。


def execute(config: str, action: str, *, limit: int | None = None, backup: str = "",
            fingerprints: list[str] | None = None, run_id: str | None = None, offset: int = 0) -> dict[str, Any]:
    from ..runtime.recovery import RecoveryCenter  # 惰性：见文件头说明

    loaded = load_config(config)
    center = RecoveryCenter(loaded)
    if action == "overview":
        return center.overview()
    if action == "failures":
        return center.failed_requests(limit if limit is not None else 100)
    if action == "coverage":
        return center.coverage(limit if limit is not None else 100, offset=offset, run_id=run_id)
    if action == "retry-discovery":
        return center.retry_discovery(fingerprints or [], run_id=run_id)
    if action == "continue":
        return center.continue_incomplete()
    if action == "retry-failed":
        return center.retry_failed(limit, fingerprints=fingerprints)
    if action == "relogin":
        return center.reset_login()
    if action == "reprocess":
        from ..pipeline import Pipeline  # 惰性：见文件头说明

        with Pipeline(loaded) as pipeline:
            return pipeline.reprocess_records()
    if action == "rollback-config":
        if not backup:
            raise ValueError("rollback-config必须提供--backup")
        return center.rollback_config(Path(backup))
    raise ValueError(f"未知恢复操作: {action}")
