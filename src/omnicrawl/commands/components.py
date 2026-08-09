from __future__ import annotations

from pathlib import Path
from typing import Any

from ..core.runtime_paths import portable_data_root
from ..services.component_manager import ComponentManager


def execute(
    action: str, *, package: str = "", name: str = "", allow_unsigned: bool = False,
    sha256: str = "",
) -> Any:
    manager = ComponentManager(portable_data_root() / ".omnicrawler" / "components")
    if action == "list":
        return manager.list()
    if action == "inspect":
        if not package:
            raise ValueError("components inspect必须提供--package")
        info = manager.inspect_package(Path(package), allow_unsigned=allow_unsigned)
        return {field: getattr(info, field) for field in info.__dataclass_fields__}
    if action == "import":
        if not package:
            raise ValueError("components import必须提供--package")
        return manager.import_offline(Path(package), allow_unsigned=allow_unsigned)
    if action == "uninstall":
        if not name:
            raise ValueError("components uninstall必须提供--name")
        return manager.uninstall(name)
    if action == "rollback":
        if not name:
            raise ValueError("components rollback必须提供--name")
        return manager.rollback(name)
    if action == "stage":
        if not package or not sha256:
            raise ValueError("components stage必须提供--package和--sha256")
        return manager.stage_resumable(Path(package), sha256)
    raise ValueError(f"未知组件操作: {action}")
