from __future__ import annotations

from pathlib import Path
from typing import Any

from ..core.config import load_config
from ..services.workspace import WorkspaceManager


def execute(config: str, action: str, *, target: str = "", kind: str = "full", destination: str = "") -> dict[str, Any]:
    if action == "import":
        if not target or not destination:
            raise ValueError("workspace import必须提供--target工作区包和--destination新目录")
        return WorkspaceManager.import_package(Path(target), Path(destination))
    if not config:
        raise ValueError("当前工作区操作必须提供--config")
    manager = WorkspaceManager(load_config(config))
    if action == "init":
        return manager.initialize()
    if action == "health":
        return manager.health()
    if action == "package":
        if not target:
            raise ValueError("workspace package必须提供--target")
        return manager.package(Path(target).expanduser().resolve(), kind=kind)
    if action == "snapshot":
        return {"snapshot": str(manager.snapshot("manual"))}
    if action == "rollback":
        if not target:
            raise ValueError("workspace rollback必须提供--target快照")
        return manager.rollback(Path(target))
    raise ValueError(f"未知工作区操作: {action}")
