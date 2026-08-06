from __future__ import annotations

import csv
import re
import threading
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from .config import FieldSpec, ProjectConfig
from .templates import is_builtin_pdf_reference, resolve_builtin_pdf_reference

AMOUNT_UNITS = {
    "亿元": 100_000_000,
    "亿": 100_000_000,
    "千万元": 10_000_000,
    "百万元": 1_000_000,
    "百万": 1_000_000,
    "万元": 10_000,
    "万": 10_000,
    "千元": 1_000,
    "百元": 100,
    "元": 1,
}

# S2.5.1：外币识别补符号/ISO 形式（$100 / USD 100 / €50 / HK$），
# 中文币名保留原 _FOREIGN_CURRENCIES 语义。¥ 是人民币符号不拒。
_FOREIGN_CURRENCY_RE = re.compile(
    r"(?:美元|美金|港币|港元|欧元|日元|日圆|英镑|瑞郎|法郎)"
    r"|(?<![A-Za-z])(?:USD|EUR|JPY|GBP|HKD|AUD|CAD)(?=\s*\d)"
    r"|(?<=[\d，,.])\s*(?:USD|EUR|JPY|GBP|HKD|AUD|CAD)\b"
    r"|(?:HK\$|\$|€|£)(?=\s*\d)"
)


def _number(text: str) -> float | None:
    """提取数字；识别会计负数括号 (1,234) / （1,234）（D49）。"""
    stripped = text.strip()
    bracket = re.search(r"[(（]\s*([\d,，.]+)\s*[)）]", stripped)
    if bracket:
        value_str = bracket.group(1)
        sign = -1.0
    else:
        match = re.search(r"[-+]?\d[\d,，]*(?:\.\d+)?", text)
        if not match:
            return None
        value_str = match.group(0)
        sign = -1.0 if value_str.startswith("-") else 1.0
        if value_str.startswith(("-", "+")):
            value_str = value_str[1:]
    try:
        return sign * float(value_str.replace(",", "").replace("，", ""))
    except ValueError:
        return None


def _format_decimal(value: object) -> str:
    """D51：Decimal 换算格式化，避免 IEEE754 浮点误差（1.15亿 → 114999999.99999999）。"""
    from decimal import Decimal

    decimal_value = Decimal(str(value))
    if decimal_value == decimal_value.to_integral_value():
        return str(decimal_value.to_integral_value())
    return format(decimal_value, "f").rstrip("0").rstrip(".")


def normalize_amount(raw: str, target_unit: str | None = "元") -> tuple[str | None, str | None]:
    from decimal import Decimal

    value = _number(raw)
    if value is None:
        return None, target_unit
    # D50/S2.5.1：外币（亿美元/港币/$100/USD 100/€50）无法按人民币换算，拒绝交人工复核
    if _FOREIGN_CURRENCY_RE.search(raw):
        return None, target_unit
    source_unit = next((unit for unit in AMOUNT_UNITS if unit in raw), None)
    source_multiplier = AMOUNT_UNITS.get(source_unit or "元", 1)
    target = target_unit or "元"
    target_multiplier = AMOUNT_UNITS.get(target, 1)
    yuan = Decimal(str(value)) * Decimal(source_multiplier)
    normalized = yuan / Decimal(target_multiplier)
    return _format_decimal(normalized), target


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
            # D52：该 pattern 解析失败（如 2023年13月 的 OCR 错字）继续尝试下一 pattern
            continue
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
# S4.5 P3#144：缓存键含 mtime——CSV 变更后重新加载，不再永不过期。
_entity_cache: dict[str, dict[str, str]] = {}
_entity_lock = threading.Lock()


def _load_entities(csv_path: Path) -> dict[str, str]:
    """Load entity aliases from a CSV file with thread-safe caching.

    Uses double-checked locking: the cache is checked first without the lock,
    then re-checked under the lock before performing the actual load.
    """
    resolved = csv_path.resolve()
    try:
        mtime = resolved.stat().st_mtime_ns
    except OSError:
        mtime = -1
    cache_key = f"{resolved}|{mtime}"
    cached = _entity_cache.get(cache_key)
    if cached is not None:
        return cached
    with _entity_lock:
        cached = _entity_cache.get(cache_key)
        if cached is not None:
            return cached
        aliases: dict[str, str] = {}
        if resolved.exists():
            with resolved.open("r", encoding="utf-8-sig", newline="") as handle:
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
    # D44：预计算去空白键字典（resolve 每次调用不再重建全表）
    _direct: dict[str, str] = field(default_factory=dict)

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
        direct = {re.sub(r"\s+", "", key): value for key, value in aliases.items()}
        return cls(aliases, direct)

    def resolve(self, raw: str) -> str:
        key = re.sub(r"\s+", "", raw).casefold()
        return self._direct.get(key, raw.strip())


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
        # D53：精确匹配优先；别名按最长优先，且拒绝含否定词（非/不/未/无）的误匹配
        for canonical, _aliases in spec.value_aliases.items():
            if text == canonical:
                return canonical, None
        best: str | None = None
        best_length = -1
        for canonical, aliases in spec.value_aliases.items():
            for alias in aliases:
                index = text.find(alias)
                if index < 0:
                    continue
                if index > 0 and any(ch in text[max(0, index - 2):index] for ch in "非不未无"):
                    continue  # “不是/并非/并未 全资子公司”不得归一为正向关系
                if len(alias) > best_length:
                    best = canonical
                    best_length = len(alias)
        if best:
            return best, None
        return text, None
    if kind == "entity" and entity_resolver:
        return entity_resolver.resolve(text), None
    return text, spec.target_unit
