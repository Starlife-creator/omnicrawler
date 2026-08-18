"""Independent CLI command handlers built on application services.

Public entry points::

    from omnicrawler.commands import run_task, run_status, field, ...
"""

from __future__ import annotations

import importlib
from typing import Any

__all__ = [
    "run_task", "run_status", "field", "init_project",
    "components", "template", "plan", "recovery", "security",
    "workspace", "schedule", "worker", "field_suggest",
]

_SUBMODULES = {
    "run_task": "run_task",
    "run_status": "run_status",
    "field": "field",
    "init_project": "init_project",
    "components": "components",
    "template": "template",
    "plan": "plan",
    "recovery": "recovery",
    "security": "security",
    "workspace": "workspace",
    "schedule": "schedule",
    "worker": "worker",
}


def __getattr__(name: str) -> Any:
    module_name = _SUBMODULES.get(name)
    if module_name:
        return importlib.import_module(f"{__name__}.{module_name}")
    if name == "field_suggest":
        return importlib.import_module(f"{__name__}.field").execute_field_suggest
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
