"""Validate explicit portable file references without importing or executing plugins."""
from __future__ import annotations

import re
from typing import Any


def path_reference(raw: dict[str, Any], pointer: str) -> tuple[Any, str | int]:
    if not isinstance(pointer, str) or not pointer.startswith("/") or len(pointer) > 1024:
        raise ValueError("workspace_paths.pointer需要有界JSON Pointer")
    parts = pointer[1:].split("/")
    if any(not part or re.search(r"~(?![01])", part) for part in parts):
        raise ValueError("workspace_paths.pointer包含无效路径片段")
    parts = [part.replace("~1", "/").replace("~0", "~") for part in parts]
    if parts[:2] in (["project", "root"], ["project", "workspace"], ["plugins", "workspace_paths"]):
        raise ValueError("workspace_paths不能声明项目根或声明自身")
    node: Any = raw
    for index, part in enumerate(parts):
        key: str | int = part
        if isinstance(node, list):
            if not re.fullmatch(r"0|[1-9][0-9]{0,6}", part) or int(part) >= len(node):
                raise ValueError("workspace_paths数组索引无效")
            key = int(part)
        elif not isinstance(node, dict) or part not in node:
            raise ValueError("workspace_paths声明的字段不存在")
        if index == len(parts) - 1:
            if not isinstance(node[key], str) or not node[key]:
                raise ValueError("workspace_paths字段必须是非空路径字符串")
            return node, key
        node = node[key]
    raise ValueError("workspace_paths.pointer无效")


def declared_paths(raw: dict[str, Any]) -> list[tuple[Any, str | int, str, str]]:
    plugins = raw.get("plugins", {})
    declarations = plugins.get("workspace_paths", []) if isinstance(plugins, dict) else []
    if not isinstance(declarations, list) or len(declarations) > 128:
        raise ValueError("plugins.workspace_paths需要最多128项的数组")
    result: list[tuple[Any, str | int, str, str]] = []
    seen: set[str] = set()
    for item in declarations:
        if not isinstance(item, dict) or set(item) != {"pointer", "kind"} or not isinstance(item["kind"], str) or item["kind"] not in {"file", "directory"}:
            raise ValueError("workspace_paths每项需要pointer和file/directory类型")
        pointer = item["pointer"]
        node, key = path_reference(raw, pointer)
        if pointer in seen:
            raise ValueError("workspace_paths不得重复声明字段")
        seen.add(pointer)
        result.append((node, key, pointer, item["kind"]))
    return result
