from __future__ import annotations

import csv
import re
import threading
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from .config import FieldSpec, ProjectConfig
from .templates import is_builtin_pdf_reference, resolve_builtin_pdf_reference

AMOUNT_UNITS = {
    "亿元": 100_000_000,
    "亿": 100_000_000,
    "百万元": 1_000_000,
    "万元": 10_000,
    "万": 10_000,
    "千元": 1_000,
    "元": 1,
}


def _number(text: str) -> float | None:
    match = re.search(r"[-+]?\d[\d,，]*(?:\.\d+)?", text)
    if not match:
        return None
    return float(match.group(0).replace(",", "").replace("，", ""))


def normalize_amount(raw: str, target_unit: str | None = "元") -> tuple[str | None, str | None]:
    value = _number(raw)
    if value is None:
        return None, target_unit
    source_unit = next((unit for unit in AMOUNT_UNITS if unit in raw), None)
    source_multiplier = AMOUNT_UNITS.get(source_unit or "元", 1)
    yuan = value * source_multiplier
    target = target_unit or "元"
    target_multiplier = AMOUNT_UNITS.get(target, 1)
    normalized = yuan / target_multiplier
    text = str(int(normalized)) if normalized.is_integer() else f"{normalized:.8f}".rstrip("0").rstrip(".")
    return text, target


def normalize_date(raw: str) -> tuple[str | None, None]:
    text = raw.strip()
    patterns = [
        r"(?P<y>20\d{2}|19\d{2})[年./-](?P<m>\d{1,2})[月./-](?P<d>\d{1,2})日?",
        r"(?P<y>20\d{2}|19\d{2})年(?P<m>\d{1,2})月",
        r"(?P<y>20\d{2}|19\d{2})年?",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if not match:
            continue
        year = int(match.group("y"))
        month = int(match.groupdict().get("m") or 1)
        day = int(match.groupdict().get("d") or 1)
        try:
            parsed = date(year, month, day)
        except ValueError:
            return None, None
        if "d" not in match.groupdict() or not match.groupdict().get("d"):
            normalized = f"{year:04d}-{month:02d}" if match.groupdict().get("m") else f"{year:04d}"
            return normalized, None
        return parsed.isoformat(), None
    return None, None


def normalize_percent(raw: str) -> tuple[str | None, str]:
    value = _number(raw)
    if value is None:
        return None, "%"
    if "%" not in raw and "百分之" not in raw and 0 <= value <= 1:
        value *= 100
    text = str(int(value)) if value.is_integer() else f"{value:.8f}".rstrip("0").rstrip(".")
    return text, "%"


# Module-level entity cache with double-checked locking for thread safety.
_entity_cache: dict[str, dict[str, str]] = {}
_entity_lock = threading.Lock()


def _load_entities(csv_path: Path) -> dict[str, str]:
    """Load entity aliases from a CSV file with thread-safe caching.

    Uses double-checked locking: the cache is checked first without the lock,
    then re-checked under the lock before performing the actual load.
    """
    cache_key = str(csv_path.resolve())
    cached = _entity_cache.get(cache_key)
    if cached is not None:
        return cached
    with _entity_lock:
        cached = _entity_cache.get(cache_key)
        if cached is not None:
            return cached
        aliases: dict[str, str] = {}
        if csv_path.exists():
            with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
                for row in csv.DictReader(handle):
                    canonical = (row.get("standard_name") or "").strip()
                    if not canonical:
                        continue
                    aliases[canonical.casefold()] = canonical
                    for alias in (row.get("aliases") or "").split("|"):
                        if alias.strip():
                            aliases[alias.strip().casefold()] = canonical
        _entity_cache[cache_key] = aliases
        return aliases


@dataclass(slots=True)
class EntityResolver:
    aliases: dict[str, str]

    @classmethod
    def from_config(cls, config: ProjectConfig) -> EntityResolver:
        path_value = config.normalization.get("entity_master_csv")
        if not path_value:
            return cls({})
        path = (
            resolve_builtin_pdf_reference(str(path_value))
            if is_builtin_pdf_reference(str(path_value))
            else Path(path_value)
        )
        if not path.is_absolute():
            base = config.path.parent
            project_root_found = False
            for candidate in [config.path.parent, *config.path.parents]:
                if (candidate / "pyproject.toml").is_file():
                    base = candidate
                    project_root_found = True
                    break
            if not project_root_found:
                for candidate in config.path.parents:
                    if candidate.name == "configs":
                        base = candidate.parent
                        break
            path = (base / path).resolve()
        aliases = _load_entities(path)
        return cls(aliases)

    def resolve(self, raw: str) -> str:
        key = re.sub(r"\s+", "", raw).casefold()
        direct = {re.sub(r"\s+", "", k): v for k, v in self.aliases.items()}
        return direct.get(key, raw.strip())


def normalize_value(
    raw: str | None,
    spec: FieldSpec,
    entity_resolver: EntityResolver | None = None,
) -> tuple[str | None, str | None]:
    if raw is None or not str(raw).strip():
        return None, spec.target_unit
    text = str(raw).strip()
    kind = spec.type.lower()
    if kind in {"amount", "currency"}:
        return normalize_amount(text, spec.target_unit or "元")
    if kind == "date":
        return normalize_date(text)
    if kind == "percent":
        return normalize_percent(text)
    if kind == "integer":
        value = _number(text)
        return (str(int(value)) if value is not None else None), None
    if kind == "number":
        value = _number(text)
        if value is None:
            return None, spec.target_unit
        normalized = str(int(value)) if value.is_integer() else str(value)
        return normalized, spec.target_unit
    if kind == "boolean":
        folded = text.casefold()
        if folded in {"是", "有", "true", "yes", "1"}:
            return "1", None
        if folded in {"否", "无", "false", "no", "0"}:
            return "0", None
        return None, None
    if kind in {"enum", "relationship"}:
        for canonical, aliases in spec.value_aliases.items():
            if text == canonical or any(alias in text for alias in aliases):
                return canonical, None
        return text, None
    if kind == "entity" and entity_resolver:
        return entity_resolver.resolve(text), None
    return text, spec.target_unit
