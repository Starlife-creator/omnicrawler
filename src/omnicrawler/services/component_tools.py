"""Reviewed GUI component operations reuse the signed offline installer."""
from __future__ import annotations

import hashlib
import shutil
import tempfile
from dataclasses import asdict
from pathlib import Path
from typing import Any

from ..commands.components import manager
from .component_compatibility import check_registry


def execute(operation: str, arguments: dict[str, Any]) -> dict[str, Any]:
    components = manager()
    registry_sha = components.registry_digest()
    if operation == "list":
        rows = components.list()
        if components.registry_digest() != registry_sha:
            raise ValueError("组件状态已变化，请重新读取")
        return {"components": rows, "registry_sha256": registry_sha}
    if operation in {"inspect", "import"}:
        package = Path(str(arguments.get("package", ""))).resolve()
        with tempfile.TemporaryDirectory(dir=components.root) as directory:
            snapshot = Path(directory) / "reviewed.ocp"
            if shutil.disk_usage(components.root).free < package.stat().st_size:
                raise OSError("磁盘空间不足，无法检查组件包")
            shutil.copyfile(package, snapshot)
            with snapshot.open("rb") as stream:
                digest = hashlib.file_digest(stream, "sha256").hexdigest()
            if operation == "import":
                if arguments.get("confirmed") is not True or not arguments.get("registry_sha256") or not arguments.get("package_sha256"):
                    raise ValueError("请先检查组件并确认安装")
                result = components.import_offline(snapshot, expected_registry_sha256=str(arguments["registry_sha256"]),
                                                   expected_package_sha256=str(arguments["package_sha256"]))
                return {"component": result, "status": "installed", "registry_sha256": components.registry_digest()}
            info = asdict(components.inspect_package(snapshot))
            rows = components.list()
            compatible, reason = True, ""
            try:
                check_registry({**{row["name"]: row for row in rows}, info["name"]: info})
            except ValueError as exc:
                compatible, reason = False, str(exc)
            if components.registry_digest() != registry_sha:
                raise ValueError("组件状态已变化，请重新检查")
            return {"component": info, "signature_status": "trusted", "compatible": compatible,
                    "compatibility_reason": reason, "package_sha256": digest, "registry_sha256": registry_sha}
    if operation in {"uninstall", "rollback"}:
        name = str(arguments.get("name", ""))
        if arguments.get("confirmed") is not True or not arguments.get("registry_sha256") or not name:
            raise ValueError("请先读取组件信息并确认操作")
        action = getattr(components, operation)
        result = action(name, expected_registry_sha256=str(arguments["registry_sha256"]))
        return {"component": result, "status": operation, "registry_sha256": components.registry_digest()}
    raise ValueError("不支持的组件管理操作")
