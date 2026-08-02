"""Versioned dataset contracts and pre-run compatibility analysis."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Literal

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


def analyse_contract_change(before: DatasetContract, after: DatasetContract) -> ContractImpact:
    old = {field.name: field for field in before.fields}
    new = {field.name: field for field in after.fields}
    added = tuple(sorted(set(new) - set(old)))
    removed = tuple(sorted(set(old) - set(new)))
    type_changes = tuple(sorted(name for name in set(old) & set(new) if old[name].data_type != new[name].data_type))
    required = tuple(sorted(name for name in set(old) & set(new) if not old[name].required and new[name].required))
    if removed or type_changes:
        level: Literal["compatible", "migration_required", "breaking"] = "breaking"
    elif required or any(new[name].required for name in added):
        level = "migration_required"
    else:
        level = "compatible"
    return ContractImpact(level, added, removed, type_changes, required, tuple(sorted(set(before.consumers) | set(after.consumers))), level != "compatible")


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

