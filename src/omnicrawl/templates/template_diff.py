from __future__ import annotations

import copy
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

_MISSING = object()


@dataclass(frozen=True, slots=True)
class MergeConflict:
    path: str
    base: Any
    user: Any
    update: Any

    def to_dict(self) -> dict[str, Any]:
        return {"path": self.path, "base": self.base, "user": self.user, "update": self.update}


def diff_templates(before: Any, after: Any) -> list[dict[str, Any]]:
    """Return stable, field-level changes between two template/config values."""
    changes: list[dict[str, Any]] = []
    _diff(before, after, "", changes)
    return changes


def merge_template_upgrade(
    base: Mapping[str, Any],
    user: Mapping[str, Any],
    update: Mapping[str, Any],
) -> tuple[dict[str, Any], list[MergeConflict]]:
    """Three-way merge an update while preferring explicit user edits on conflicts."""
    conflicts: list[MergeConflict] = []
    merged = _merge(dict(base), dict(user), dict(update), "", conflicts)
    assert isinstance(merged, dict)
    return merged, conflicts


def compare_template_files(before: Path, after: Path) -> dict[str, Any]:
    old = _load_mapping(before)
    new = _load_mapping(after)
    changes = diff_templates(old, new)
    return {
        "before": str(before.resolve()),
        "after": str(after.resolve()),
        "changed": bool(changes),
        "change_count": len(changes),
        "changes": changes,
    }


def merge_template_files(base: Path, user: Path, update: Path) -> tuple[dict[str, Any], list[MergeConflict]]:
    return merge_template_upgrade(_load_mapping(base), _load_mapping(user), _load_mapping(update))


def _load_mapping(path: Path) -> dict[str, Any]:
    value = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(value, dict):
        raise ValueError(f"模板根节点必须是映射: {path}")
    return value


def _display(value: Any) -> Any:
    return None if value is _MISSING else copy.deepcopy(value)


def _clone(value: Any) -> Any:
    """Copy a merge value without duplicating the identity-based missing sentinel."""

    return _MISSING if value is _MISSING else copy.deepcopy(value)


def _diff(before: Any, after: Any, path: str, changes: list[dict[str, Any]]) -> None:
    if before == after:
        return
    if isinstance(before, dict) and isinstance(after, dict):
        for key in sorted(set(before) | set(after), key=str):
            child = f"{path}.{key}" if path else str(key)
            old = before.get(key, _MISSING)
            new = after.get(key, _MISSING)
            if old is _MISSING:
                changes.append({"path": child, "change_type": "added", "before": None, "after": _display(new)})
            elif new is _MISSING:
                changes.append({"path": child, "change_type": "removed", "before": _display(old), "after": None})
            else:
                _diff(old, new, child, changes)
        return
    changes.append({"path": path or "$", "change_type": "modified", "before": _display(before), "after": _display(after)})


def _merge(base: Any, user: Any, update: Any, path: str, conflicts: list[MergeConflict]) -> Any:
    if user == base:
        return _clone(update)
    if update == base or user == update:
        return _clone(user)
    if all(value is _MISSING or isinstance(value, dict) for value in (base, user, update)):
        base_map = {} if base is _MISSING else base
        user_map = {} if user is _MISSING else user
        update_map = {} if update is _MISSING else update
        result: dict[str, Any] = {}
        for key in sorted(set(base_map) | set(user_map) | set(update_map), key=str):
            child = f"{path}.{key}" if path else str(key)
            value = _merge(
                base_map.get(key, _MISSING),
                user_map.get(key, _MISSING),
                update_map.get(key, _MISSING),
                child,
                conflicts,
            )
            if value is not _MISSING:
                result[key] = value
        return result
    conflicts.append(MergeConflict(path or "$", _display(base), _display(user), _display(update)))
    return _clone(user)
