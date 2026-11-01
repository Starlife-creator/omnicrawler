"""Auditable, three-way PDF template updates; no silent overwrite of edits."""
from __future__ import annotations

import copy
import hashlib
import json
import shutil
import tempfile
from pathlib import Path
from typing import Any

import yaml

from .utils import atomic_output_path


def template_digest(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _mapping(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("PDF 模板和项目配置必须是 YAML 对象")
    return value


def merge_template_config(current_path: str | Path, base_path: str | Path,
                          new_path: str | Path) -> dict[str, Any]:
    """Merge only unambiguous template changes, preserving local edits.

    Runtime paths, project name and database are deliberately project-owned.
    If a field changed both locally and upstream, the current value is retained
    and reported as a conflict; nothing is silently replaced.
    """
    current, base, new = (_mapping(yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {})
                          for path in (current_path, base_path, new_path))
    protected = {"project_name", "input_dir", "work_dir", "output_dir", "database"}
    merged: dict[str, Any] = copy.deepcopy(current)
    applied: list[str] = []
    conflicts: list[str] = []
    skipped: list[str] = []

    def merge_value(key: str, before: Any, after: Any, existing: Any) -> Any:
        if before == after:
            return existing
        if isinstance(before, dict) and isinstance(after, dict) and isinstance(existing, dict):
            result = copy.deepcopy(existing)
            for child in sorted(set(before) | set(after) | set(existing)):
                name = f"{key}.{child}"
                old_child, new_child = before.get(child), after.get(child)
                if child not in existing:
                    if child not in before and child in after:
                        result[child] = new_child
                        applied.append(name)
                    continue
                result[child] = merge_value(name, old_child, new_child, existing[child])
            return result
        if existing == before:
            applied.append(key)
            return after
        conflicts.append(key)
        return existing

    for key in sorted(set(base) | set(new)):
        if key in protected:
            skipped.append(key)
            continue
        before, after = base.get(key), new.get(key)
        if key not in current:
            if key not in base and key in new:
                merged[key] = after
                applied.append(key)
            elif key in base and key not in new:
                # Locally removed, upstream unchanged: preserve removal.
                continue
            else:
                conflicts.append(key)
            continue
        merged[key] = merge_value(key, before, after, current[key])
    return {"config": merged, "applied": applied, "conflicts": conflicts,
            "project_owned": skipped}


def merge_project_template(config_path: str | Path, base_path: str | Path,
                           new_path: str | Path, *, apply: bool = False) -> dict[str, Any]:
    result = merge_template_config(config_path, base_path, new_path)
    config = _mapping(result.pop("config"))
    from .config import load_config, validate_runtime_config
    from .safe_regex import validate_pattern
    target = Path(config_path).resolve()
    if apply:
        temporary = Path(tempfile.mkdtemp(prefix="omnicrawler-template-merge-"))
        candidate = temporary / "project.yaml"
        try:
            candidate.write_text(yaml.safe_dump(config, allow_unicode=True, sort_keys=False, width=120), encoding="utf-8")
            loaded = load_config(candidate)
            warnings = validate_runtime_config(loaded)
            for spec in loaded.fields:
                for pattern in spec.patterns:
                    validate_pattern(pattern)
        finally:
            shutil.rmtree(temporary)
        with atomic_output_path(target, suffix=target.suffix) as temporary:
            temporary.write_text(yaml.safe_dump(config, allow_unicode=True, sort_keys=False, width=120), encoding="utf-8")
        sidecar = target.with_name(target.name + ".template-base.json")
        with atomic_output_path(sidecar, suffix=".json") as temporary:
            temporary.write_text(json.dumps({"version": 1, "sha256": template_digest(new_path)}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    else:
        warnings = []
    return {**result, "applied_to_file": apply, "path": str(target), "warnings": warnings,
            "new_template_sha256": template_digest(new_path)}
