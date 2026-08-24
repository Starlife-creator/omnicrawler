from __future__ import annotations

import ast
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .plugins import CORE_VERSION, PLUGIN_API_VERSION


@dataclass(frozen=True, slots=True)
class PluginInspection:
    path: str
    name: str
    version: str
    api_version: int
    description: str
    permissions: tuple[str, ...]
    capabilities: tuple[str, ...]
    compatible: bool
    errors: tuple[str, ...]
    # Phase 3（B2 双契约适配）：契约形态（1=register/2=handle/0=未知）+ 声明模式
    contract_shape: int = 0
    execution_mode: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def inspect_plugin(path: Path) -> PluginInspection:
    errors: list[str] = []
    metadata: dict[str, Any] = {}
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, UnicodeError, SyntaxError) as exc:
        return PluginInspection(str(path), path.stem, "", 0, "", (), (), False, (str(exc),))
    has_register = any(isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "register" for node in tree.body)
    # Phase 3（B2）：契约 2 = 顶层 handle 函数（可 subprocess）
    has_handle = any(isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "handle" for node in tree.body)
    if has_handle:
        contract_shape = 2
    elif has_register:
        contract_shape = 1
    else:
        contract_shape = 0
        errors.append("缺少 register(registry)（契约 1）或 handle(operation,payload)（契约 2）入口")
    for node in tree.body:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        if not any(isinstance(target, ast.Name) and target.id == "PLUGIN_METADATA" for target in targets):
            continue
        try:
            value = ast.literal_eval(node.value) if node.value is not None else {}
        except (ValueError, TypeError):
            errors.append("PLUGIN_METADATA 必须使用可静态读取的字面量")
        else:
            if isinstance(value, dict):
                metadata = value
            else:
                errors.append("PLUGIN_METADATA 必须是字典")
        break
    api_version = int(metadata.get("api_version", PLUGIN_API_VERSION))
    if api_version != PLUGIN_API_VERSION:
        errors.append(f"插件 API {api_version} 与当前 API {PLUGIN_API_VERSION} 不兼容")
    min_core = str(metadata.get("min_core_version", "0.0.1"))
    max_core = str(metadata.get("max_core_version", ""))
    if _version(min_core) > _version(CORE_VERSION):
        errors.append(f"需要 OmniCrawler >= {min_core}")
    if max_core and _version(max_core) < _version(CORE_VERSION):
        errors.append(f"仅支持 OmniCrawler <= {max_core}")
    execution_mode = str(metadata.get("execution_mode", "")).strip()
    # Phase 3（B2）契约一致性：契约 1（register）+ subprocess 声明 → 不可运行
    if contract_shape == 1 and execution_mode in ("", "subprocess"):
        errors.append(
            "契约 1（register）插件不能以 subprocess 运行（请迁移契约 2 或显式声明 execution_mode: in_process）"
        )
    return PluginInspection(
        str(path.resolve()),
        str(metadata.get("name", path.stem)),
        str(metadata.get("version", "0.0.0")),
        api_version,
        str(metadata.get("description", "legacy plugin")),
        tuple(str(item) for item in metadata.get("permissions", [])),
        tuple(str(item) for item in metadata.get("capabilities", [])),
        not errors,
        tuple(errors),
        contract_shape=contract_shape,
        execution_mode=execution_mode,
    )


def inspect_directory(directory: Path) -> list[PluginInspection]:
    return [inspect_plugin(path) for path in sorted(directory.glob("*.py")) if path.name != "__init__.py"]


def _version(value: str) -> tuple[int, ...]:
    return tuple(int("".join(char for char in token if char.isdigit()) or 0) for token in value.split("."))
