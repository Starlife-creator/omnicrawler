"""Safe composition of a recommended recipe with the user's current task."""

from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any

from ..core.utils import deep_merge


@dataclass(frozen=True, slots=True)
class ConfigChange:
    path: str
    before: Any
    after: Any


def compose_recipe(current: dict[str, Any], recipe: dict[str, Any]) -> dict[str, Any]:
    """Add recipe capabilities while preserving business choices and task identity."""
    current = copy.deepcopy(current)
    recipe = copy.deepcopy(recipe)
    recipe.pop("template", None)
    result = deep_merge(current, recipe)
    if isinstance(current.get("project"), dict):
        result["project"] = deep_merge(result.get("project", {}), current["project"])
    current_source = current.get("source", {})
    if isinstance(current_source, dict) and current_source.get("seeds"):
        result.setdefault("source", {})["seeds"] = copy.deepcopy(current_source["seeds"])
    current_extract = current.get("extract", {})
    if isinstance(current_extract, dict) and current_extract.get("fields"):
        result.setdefault("extract", {})["fields"] = copy.deepcopy(current_extract["fields"])
    for section in ("task", "selection", "ai", "outputs"):
        if isinstance(current.get(section), dict):
            result[section] = deep_merge(result.get(section, {}), current[section])
    return result


def diff_config(before: Any, after: Any, path: str = "") -> list[ConfigChange]:
    if isinstance(before, dict) and isinstance(after, dict):
        changes: list[ConfigChange] = []
        for key in sorted(set(before) | set(after)):
            child = f"{path}.{key}" if path else str(key)
            if key not in before:
                changes.append(ConfigChange(child, None, after[key]))
            elif key not in after:
                changes.append(ConfigChange(child, before[key], None))
            else:
                changes.extend(diff_config(before[key], after[key], child))
        return changes
    return [] if before == after else [ConfigChange(path, before, after)]
