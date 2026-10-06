from __future__ import annotations

from pathlib import Path
from typing import Any

from ..core.runtime_paths import portable_data_root
from ..services.component_manager import ComponentManager
from ..services.update_feed import decode_public_key


def manager() -> ComponentManager:
    root = portable_data_root() / ".omnicrawler" / "components"
    trust_file = root.parent / "component_trust.pub.pem"
    key = decode_public_key(trust_file.read_text(encoding="utf-8")) if trust_file.is_file() else None
    return ComponentManager(root, trusted_public_key=key)


def execute(
    action: str, *, package: str = "", name: str = "", allow_unsigned: bool = False,
    sha256: str = "",
) -> Any:
    component_manager = manager()
    if action == "list":
        return component_manager.list()
    if action == "inspect":
        if not package:
            raise ValueError("components inspect必须提供--package")
        info = component_manager.inspect_package(Path(package), allow_unsigned=allow_unsigned)
        return {field: getattr(info, field) for field in info.__dataclass_fields__}
    if action == "import":
        if not package:
            raise ValueError("components import必须提供--package")
        return component_manager.import_offline(Path(package), allow_unsigned=allow_unsigned)
    if action == "uninstall":
        if not name:
            raise ValueError("components uninstall必须提供--name")
        return component_manager.uninstall(name)
    if action == "rollback":
        if not name:
            raise ValueError("components rollback必须提供--name")
        return component_manager.rollback(name)
    if action == "stage":
        if not package or not sha256:
            raise ValueError("components stage必须提供--package和--sha256")
        return component_manager.stage_resumable(Path(package), sha256)
    raise ValueError(f"未知组件操作: {action}")
