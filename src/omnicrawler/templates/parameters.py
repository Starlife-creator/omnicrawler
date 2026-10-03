"""Declared template parameter validation shared by GUI drafts and CLI rendering."""

from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping
from typing import Any


def validate_parameters(declarations: Mapping[str, Any], values: Mapping[str, Any], *, strict: bool) -> dict[str, Any]:
    merged = {key: spec.get("default") if isinstance(spec, Mapping) else spec
              for key, spec in declarations.items()}
    merged.update(values)
    for key, spec in declarations.items():
        if not isinstance(spec, Mapping):
            continue
        kind = spec.get("type")
        if kind not in {None, "string", "integer", "number", "boolean", "array", "object"}:
            raise ValueError(f"Invalid parameter type declaration: {key}")
        if "required" in spec and type(spec["required"]) is not bool:
            raise ValueError(f"Invalid required parameter declaration: {key}")
        for boundary in ("minimum", "maximum"):
            if boundary in spec and (type(spec[boundary]) not in (int, float) or not math.isfinite(spec[boundary])):
                raise ValueError(f"Invalid numeric parameter constraint: {key}")
        if "minimum" in spec and "maximum" in spec and spec["minimum"] > spec["maximum"]:
            raise ValueError(f"Invalid numeric parameter range: {key}")
        value = merged.get(key)
        if value is None or value == "":
            if strict and spec.get("required"):
                raise ValueError(f"Missing required template value: {key}")
            continue
        # CLI --set values are text. Only explicitly typed declarations are converted.
        if isinstance(value, str) and kind in {"integer", "number", "boolean", "array", "object"}:
            try:
                if kind == "integer" and re.fullmatch(r"[+-]?\d+", value):
                    value = int(value)
                else:
                    value = json.loads(value)
            except (ValueError, json.JSONDecodeError):
                raise ValueError(f"Invalid template parameter type: {key} ({kind})") from None
        valid = {
            None: True, "string": isinstance(value, str),
            "integer": type(value) is int,
            "number": type(value) in (int, float),
            "boolean": type(value) is bool,
            "array": isinstance(value, list), "object": isinstance(value, dict),
        }[kind]
        if not valid or (isinstance(value, float) and not math.isfinite(value)):
            raise ValueError(f"Invalid template parameter type: {key} ({kind})")
        for boundary, below in (("minimum", True), ("maximum", False)):
            if boundary not in spec:
                continue
            bound = spec[boundary]
            if type(value) not in (int, float) or type(bound) not in (int, float) or not math.isfinite(bound):
                raise ValueError(f"Invalid numeric parameter constraint: {key}")
            if (value < bound if below else value > bound):
                raise ValueError(f"Template parameter out of range: {key} ({boundary})")
        if "enum" in spec:
            choices = spec["enum"]
            if not isinstance(choices, list) or not any(type(value) is type(item) and value == item for item in choices):
                raise ValueError(f"Template parameter outside enum: {key}")
        merged[key] = value
    return merged
