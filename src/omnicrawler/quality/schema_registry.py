"""Versioned dataset contracts and pre-run compatibility analysis."""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any, Literal
from urllib.parse import urlparse

Sensitivity = Literal["public", "internal", "sensitive", "personal", "highly_sensitive"]


@dataclass(frozen=True, slots=True)
class FieldContract:
    name: str
    data_type: str
    meaning: str
    required: bool = False
    unique: bool = False
    enum: tuple[Any, ...] = ()
    evidence_required: bool = True
    sensitivity: Sensitivity = "public"
    nullable: bool | None = None
    allow_empty: bool = False
    minimum: float | None = None
    maximum: float | None = None
    items: dict[str, Any] | None = None
    properties: dict[str, Any] | None = None
    additional_properties: bool = False
    min_length: int | None = None
    max_length: int | None = None

    def to_rule(self) -> dict[str, Any]:
        rule = {"type": self.data_type, "required": self.required, "allow_empty": self.allow_empty,
                "values": list(self.enum), "min": self.minimum, "max": self.maximum,
                "items": self.items, "properties": self.properties,
                "additional_properties": self.additional_properties,
                "min_length": self.min_length, "max_length": self.max_length}
        if self.nullable is not None:
            rule["nullable"] = self.nullable
        return rule


@dataclass(frozen=True, slots=True)
class DatasetContract:
    dataset: str
    version: str
    fields: tuple[FieldContract, ...]
    quality_threshold: float = 0.8
    retention_days: int | None = None
    consumers: tuple[str, ...] = ()

    def to_mapping(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class ContractImpact:
    compatibility: Literal["compatible", "migration_required", "breaking"]
    added: tuple[str, ...]
    removed: tuple[str, ...]
    type_changes: tuple[str, ...]
    required_changes: tuple[str, ...]
    affected_consumers: tuple[str, ...]
    historical_reprocess_required: bool
    constraint_changes: tuple[str, ...] = ()
    meaning_changes: tuple[str, ...] = ()


def _tightens_rule(before: dict[str, Any], after: dict[str, Any], depth: int = 0) -> bool:
    """Conservatively detect restrictions that may invalidate stored JSON values."""
    if depth > 20:
        return before != after
    if before.get("type", "text") != after.get("type", "text"):
        return True
    for key in ("required",):
        if after.get(key, False) and not before.get(key, False):
            return True
    if after.get("nullable") is False and before.get("nullable") is not False:
        return True
    if before.get("nullable") is True and after.get("nullable") is not True:
        return True
    if before.get("allow_empty", False) and not after.get("allow_empty", False):
        return True
    for key in ("min", "min_length", "max", "max_length"):
        old, new = before.get(key), after.get(key)
        if new is not None and (old is None or (new > old if key.startswith("min") else new < old)):
            return True
    if after.get("type") == "enum" and any(item not in after.get("values", []) for item in before.get("values", [])):
        return True
    old_items, new_items = before.get("items"), after.get("items")
    if new_items is not None and (old_items is None or _tightens_rule(old_items, new_items, depth + 1)):
        return True
    old_properties, new_properties = before.get("properties"), after.get("properties")
    if new_properties is not None:
        if old_properties is None:
            return True
        if before.get("additional_properties", False) and not after.get("additional_properties", False):
            return True
        if set(old_properties) - set(new_properties) and not after.get("additional_properties", False):
            return True
        for name, rule in new_properties.items():
            if name not in old_properties:
                if rule.get("required", False) or before.get("additional_properties", False):
                    return True
            elif _tightens_rule(old_properties[name], rule, depth + 1):
                return True
    return False


def analyse_contract_change(before: DatasetContract, after: DatasetContract) -> ContractImpact:
    old = {field.name: field for field in before.fields}
    new = {field.name: field for field in after.fields}
    added = tuple(sorted(set(new) - set(old)))
    removed = tuple(sorted(set(old) - set(new)))
    type_changes = tuple(sorted(name for name in set(old) & set(new) if old[name].data_type != new[name].data_type))
    required = tuple(sorted(name for name in set(old) & set(new) if not old[name].required and new[name].required))
    constraints = tuple(sorted(name for name in set(old) & set(new) if (
        _tightens_rule(old[name].to_rule(), new[name].to_rule())
        or (new[name].unique and not old[name].unique)
        or (new[name].evidence_required and not old[name].evidence_required)
    )))
    meanings = tuple(sorted(name for name in set(old) & set(new) if old[name].meaning != new[name].meaning))
    if removed or type_changes or meanings:
        level: Literal["compatible", "migration_required", "breaking"] = "breaking"
    elif required or constraints or any(new[name].required for name in added) or after.quality_threshold > before.quality_threshold:
        level = "migration_required"
    else:
        level = "compatible"
    return ContractImpact(level, added, removed, type_changes, required, tuple(sorted(set(before.consumers) | set(after.consumers))), level != "compatible", constraints, meanings)


class SchemaRegistry:
    def __init__(self) -> None:
        self._contracts: dict[tuple[str, str], DatasetContract] = {}

    def register(self, contract: DatasetContract) -> None:
        key = (contract.dataset, contract.version)
        if key in self._contracts and self._contracts[key] != contract:
            raise ValueError("同一数据契约版本不可覆盖")
        if len({field.name for field in contract.fields}) != len(contract.fields):
            raise ValueError("数据契约字段名不能重复")
        self._contracts[key] = contract

    def get(self, dataset: str, version: str) -> DatasetContract:
        return self._contracts[(dataset, version)]



def compile_field_contract(name: str, rule: dict[str, Any]) -> FieldContract:
    """Compile the existing selector/AI rule shape without converting source values."""
    if not isinstance(rule, dict):
        raise ValueError(f"字段 {name} 契约必须是对象")
    for key in ("required", "nullable", "allow_empty", "additional_properties"):
        if key in rule and rule[key] is not None and type(rule[key]) is not bool:
            raise ValueError(f"字段 {name} 的 {key} 必须是布尔值")
    for key in ("min", "max"):
        bound = rule.get(key)
        if bound is not None and (type(bound) not in (int, float) or not math.isfinite(bound)):
            raise ValueError(f"字段 {name} 的 {key} 必须是有限数字")
    for key in ("min_length", "max_length"):
        bound = rule.get(key)
        if bound is not None and (type(bound) is not int or bound < 0):
            raise ValueError(f"字段 {name} 的 {key} 必须是非负整数")
    if rule.get("min") is not None and rule.get("max") is not None and rule["min"] > rule["max"]:
        raise ValueError(f"字段 {name} 数值范围倒置")
    for key in ("items", "properties"):
        if rule.get(key) is not None and not isinstance(rule[key], dict):
            raise ValueError(f"字段 {name} 的 {key} 必须是对象")
    return FieldContract(name, str(rule.get("type", "text")).casefold(), str(rule.get("description", "")),
                         required=rule.get("required", False), enum=tuple(rule.get("values", [])),
                         nullable=rule.get("nullable"), allow_empty=rule.get("allow_empty", False),
                         minimum=rule.get("min"), maximum=rule.get("max"), items=rule.get("items"),
                         properties=rule.get("properties"), additional_properties=rule.get("additional_properties", False),
                         min_length=rule.get("min_length"), max_length=rule.get("max_length"))


def validate_target_fields(
    value: dict[str, Any], schema: dict[str, dict[str, Any]], *, allow_extra: bool = False, _depth: int = 0,
) -> list[str]:
    """Validate native JSON types, retaining legacy missing-value defaults.

    Explicit nullable/allow_empty declarations distinguish a valid present value
    from a missing field. Required fields are assessed again after chunk merging.
    """
    if not isinstance(value, dict) or _depth > 20:
        raise ValueError("目标字段必须是有限深度的 JSON 对象")
    unknown = set(value) - set(schema)
    if unknown and not allow_extra:
        raise ValueError(f"AI 目标字段未声明: {', '.join(sorted(str(key) for key in unknown))}")
    missing: list[str] = []
    for name, rule in schema.items():
        contract = compile_field_contract(name, rule)
        if name not in value:
            if contract.required:
                missing.append(name)
            continue
        item = value[name]
        if item is None:
            if contract.nullable is False:
                raise ValueError(f"AI 目标字段 {name} 不允许 null")
            if contract.required and contract.nullable is not True:
                missing.append(name)
            continue
        if (item == "" or item == []) and not contract.allow_empty:
            if contract.required:
                missing.append(name)
            continue
        kind = contract.data_type
        valid = False
        if kind in {"number", "float", "money"}:
            valid = type(item) in (int, float) and math.isfinite(item)
        elif kind in {"integer", "int"}:
            valid = type(item) is int
        elif kind in {"boolean", "bool"}:
            valid = type(item) is bool
        elif kind in {"text", "string", "date", "datetime", "url", "enum"}:
            valid = isinstance(item, str)
        elif kind in {"list", "array"}:
            valid = isinstance(item, list)
        elif kind in {"object", "dict"}:
            valid = isinstance(item, dict)
        else:
            raise ValueError(f"AI 目标字段 {name} 未知类型: {kind}")
        if not valid:
            raise ValueError(f"AI 目标字段 {name} 类型错误: 需要 {kind}")
        if kind in {"date", "datetime"}:
            try:
                datetime.fromisoformat(item.replace("Z", "+00:00"))
            except ValueError as exc:
                raise ValueError(f"AI 目标字段 {name} 需要 ISO 日期") from exc
        if kind == "url":
            parsed = urlparse(item)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                raise ValueError(f"AI 目标字段 {name} 需要 HTTP(S) URL")
        if kind == "enum" and item not in contract.enum:
            raise ValueError(f"AI 目标字段 {name} 不在枚举范围")
        if type(item) in (int, float):
            if contract.minimum is not None and item < contract.minimum:
                raise ValueError(f"AI 目标字段 {name} 超出 min 范围")
            if contract.maximum is not None and item > contract.maximum:
                raise ValueError(f"AI 目标字段 {name} 超出 max 范围")
        if isinstance(item, (str, list, dict)):
            if contract.min_length is not None and len(item) < contract.min_length:
                raise ValueError(f"AI 目标字段 {name} 短于最小长度")
            if contract.max_length is not None and len(item) > contract.max_length:
                raise ValueError(f"AI 目标字段 {name} 长于最大长度")
        if isinstance(item, list) and contract.items is not None:
            for index, child in enumerate(item):
                child_name = f"{name}[{index}]"
                child_rule = {**contract.items, "required": True}
                if validate_target_fields({child_name: child}, {child_name: child_rule}, _depth=_depth + 1):
                    raise ValueError(f"AI 目标字段 {child_name} 缺少有效值")
        if isinstance(item, dict) and contract.properties is not None:
            child_missing = validate_target_fields(item, contract.properties,
                                                   allow_extra=contract.additional_properties, _depth=_depth + 1)
            missing.extend(f"{name}.{child}" for child in child_missing)
    return missing
