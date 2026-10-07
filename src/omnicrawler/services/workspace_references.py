"""Preview and repair explicitly declared paths into a new, identity-preserving config."""
from __future__ import annotations

import hashlib
import json
import os
import uuid
from pathlib import Path
from typing import Any

import yaml

from ..core.config import AppConfig, load_config
from ..core.utils import atomic_write
from ..core.workspace_paths import declared_paths


def _source_config(path: Path) -> tuple[AppConfig, dict[str, Any]]:
    payload = path.read_bytes()
    if len(payload) > 2 * 1024**2:
        raise ValueError("配置超过引用修复大小限制")
    raw = yaml.safe_load(payload)
    if not isinstance(raw, dict):
        raise ValueError("配置结构无效")
    # Never serialize AppConfig.raw: it contains resolved credential values.
    return load_config(path), raw


def references(raw: dict[str, Any]) -> list[tuple[Any, str | int, str, str]]:
    result = declared_paths(raw)
    for section, field, kind in (("source", "query_file", "file"), ("source", "spider_file", "file"), ("download", "output_dir", "directory")):
        node = raw.get(section, {})
        if isinstance(node, dict) and isinstance(node.get(field), str) and node[field] and not any(parent is node and key == field for parent, key, _, _ in result):
            result.append((node, field, section + "." + field, kind))
    return result


def inspect(config_path: Path) -> dict[str, Any]:
    config, raw = _source_config(config_path)
    return {"config": str(config.path), "references": [
        {"field": label, "kind": kind, "value": node[key], "exists": (config.root / node[key]).exists()}
        for node, key, label, kind in references(raw)]}


def _files(source: Path, kind: str) -> list[Path]:
    if source.is_symlink() or getattr(source, "is_junction", lambda: False)():
        raise ValueError("引用修复不接受符号链接或目录联接")
    if kind == "file":
        if not source.is_file():
            raise ValueError("请选择存在的文件")
        files = [source]
    else:
        if not source.is_dir():
            raise ValueError("请选择存在的目录")
        files = []
        for directory, subdirs, names in os.walk(source, followlinks=False):
            children = [Path(directory) / name for name in subdirs + names]
            if any(path.is_symlink() or getattr(path, "is_junction", lambda: False)() for path in children):
                raise ValueError("目录包含符号链接或目录联接")
            files.extend(Path(directory) / name for name in names)
            if len(files) > 1000:
                raise ValueError("引用修复目录超过1000个文件")
    if sum(path.stat().st_size for path in files) > 100 * 1024**2:
        raise ValueError("引用修复超过100 MiB，请选择更小范围")
    return files


def _digest(path: Path) -> str:
    digest = hashlib.sha256()
    total = 0
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024**2), b""):
            total += len(chunk)
            if total > 100 * 1024**2:
                raise ValueError("引用文件超过100 MiB")
            digest.update(chunk)
    return digest.hexdigest()


def preview(config_path: Path, field: str, source: Path, mode: str) -> dict[str, Any]:
    if mode not in {"copy", "rebind"}:
        raise ValueError("引用修复模式必须为copy或rebind")
    config, raw = _source_config(config_path)
    entry = next((row for row in references(raw) if row[2] == field), None)
    if entry is None:
        raise ValueError("只能修复已声明的文件引用")
    original = source.expanduser().absolute()
    if original.is_symlink() or getattr(original, "is_junction", lambda: False)():
        raise ValueError("引用修复不接受符号链接或目录联接")
    source = original.resolve()
    files = _files(source, entry[3])
    manifest = [{"relative": path.name if entry[3] == "file" else path.relative_to(source).as_posix(),
                 "bytes": path.stat().st_size, "sha256": _digest(path)} for path in sorted(files)]
    result: dict[str, Any] = {"config": str(config.path), "config_sha256": hashlib.sha256(config.path.read_bytes()).hexdigest(),
                            "field": field, "kind": entry[3], "old_value": entry[0][entry[1]], "source": str(source),
                            "mode": mode, "files": manifest, "bytes": sum(row["bytes"] for row in manifest),
                            "effect": "生成新配置，保留原配置与任务身份；复制模式将所选文件纳入工作区"}
    result["binding"] = hashlib.sha256(json.dumps(result, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    return result


def apply(config_path: Path, field: str, source: Path, mode: str, *, binding: str) -> dict[str, Any]:
    plan = preview(config_path, field, source, mode)
    if not binding or binding != plan["binding"]:
        raise ValueError("配置或源文件已变化，请重新预览")
    config, raw = _source_config(config_path)
    node, key, _label, kind = next(row for row in references(raw) if row[2] == field)
    identity = uuid.uuid4().hex
    target = Path(plan["source"])
    if mode == "copy":
        directory = config.workspace / "relinked" / identity
        directory.mkdir(parents=True, exist_ok=False)
        for row in plan["files"]:
            relative = row["relative"]
            origin = target if kind == "file" else target / relative
            dest = directory / relative
            dest.parent.mkdir(parents=True, exist_ok=True)
            total = 0
            with origin.open("rb") as reader, dest.open("xb") as writer:
                for chunk in iter(lambda: reader.read(1024**2), b""):
                    total += len(chunk)
                    if total > row["bytes"]:
                        raise ValueError("源文件在复制期间增长，修复未发布")
                    writer.write(chunk)
            if _digest(dest) != row["sha256"]:
                raise ValueError("源文件在复制期间变化，修复未发布")
        target = directory / Path(plan["source"]).name if kind == "file" else directory
    node[key] = str(target)
    # Relative project roots are bound to the original config directory.
    raw.setdefault("project", {})["root"] = str(config.root)
    raw["project"]["workspace"] = str(config.workspace)
    result_path = config.path.with_name(config.path.stem + ".repaired-" + identity + ".yaml")
    content = yaml.safe_dump(raw, allow_unicode=True, sort_keys=False).encode("utf-8")
    if hashlib.sha256(config.path.read_bytes()).hexdigest() != plan["config_sha256"]:
        raise ValueError("配置已变化，修复未发布")
    atomic_write(result_path, content)
    load_config(result_path)
    result = {"config": str(result_path), "original_config": str(config.path), "field": field,
              "value": str(target), "mode": mode, "task_id": raw["project"].get("task_id"), "preview_binding": binding}
    report = config.workspace / ("reference-repair-" + identity + ".json")
    atomic_write(report, json.dumps(result, ensure_ascii=False, indent=2).encode())
    result["report"] = str(report)
    return result
