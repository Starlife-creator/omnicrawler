from __future__ import annotations

from pathlib import Path
from typing import Any

from ..core.runtime_paths import portable_data_root
from ..services.component_manager import ComponentManager
from ..services.component_trust import load_trusted_key


def manager() -> ComponentManager:
    """构造组件管理器（信任根按两级查找，见 services.component_trust）。

    此前只认 ``portable_data_root()/.omnicrawler/component_trust.pub.pem``——
    那是运行期数据目录，**不随包发布**，所以组件默认不可用。
    现在与另外两把钥匙一致：随包 ``configs/component_trust.pub.pem`` 兜底，
    显式配置（环境变量/数据目录文件）优先。
    """
    root = portable_data_root() / ".omnicrawler" / "components"
    return ComponentManager(root, trusted_public_key=load_trusted_key())


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
