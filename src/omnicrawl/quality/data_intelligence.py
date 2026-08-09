from __future__ import annotations

import csv
import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from ..core.config import AppConfig
from ..core.models import ExtractedRecord


def normalize_entity(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    text = re.sub(r"[\s\-—_·•,，.。()（）\[\]【】]+", "", text)
    suffixes = ("有限责任公司", "股份有限公司", "有限公司", "公司", "大学", "学院")
    for suffix in suffixes:
        if text.endswith(suffix.casefold()) and len(text) > len(suffix):
            text = text[: -len(suffix)]
            break
    return text


def simhash(text: str) -> int:
    tokens = re.findall(r"[\w\u4e00-\u9fff]+", unicodedata.normalize("NFKC", text).casefold())
    if not tokens:
        return 0
    vector = [0] * 64
    for token in tokens:
        import hashlib
        value = int.from_bytes(hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest(), "big")
        for bit in range(64):
            vector[bit] += 1 if value & (1 << bit) else -1
    result = 0
    for bit, score in enumerate(vector):
        if score >= 0:
            result |= 1 << bit
    return result


def hamming_distance(left: int, right: int) -> int:
    return (left ^ right).bit_count()


@dataclass(slots=True)
class EntityResolver:
    aliases: dict[str, str]

    @classmethod
    def from_config(cls, config: AppConfig) -> EntityResolver:
        settings = config.section("data_quality").get("entity_resolution", {})
        aliases: dict[str, str] = {}
        if isinstance(settings, dict):
            raw_aliases = settings.get("aliases", {})
            if isinstance(raw_aliases, dict):
                for canonical, values in raw_aliases.items():
                    aliases[normalize_entity(canonical)] = str(canonical)
                    if isinstance(values, list):
                        for value in values:
                            aliases[normalize_entity(value)] = str(canonical)
            csv_path = str(settings.get("csv", "")).strip()
            if csv_path:
                path = config.resolve(csv_path)
                with path.open(encoding="utf-8-sig", newline="") as handle:
                    for row in csv.DictReader(handle):
                        canonical = str(row.get("canonical", "")).strip()
                        alias = str(row.get("alias", "")).strip()
                        if canonical and alias:
                            aliases[normalize_entity(alias)] = canonical
                            aliases.setdefault(normalize_entity(canonical), canonical)
        return cls(aliases)

    def resolve(self, value: Any) -> tuple[Any, bool]:
        key = normalize_entity(value)
        if key and key in self.aliases:
            canonical = self.aliases[key]
            return canonical, canonical != value
        return value, False


def enrich_records(records: list[ExtractedRecord], config: AppConfig) -> dict[str, int]:
    settings = config.section("data_quality")
    resolver = EntityResolver.from_config(config)
    entity_fields = [str(item) for item in settings.get("entity_fields", [])]
    resolved = 0
    for record in records:
        for field in entity_fields:
            if field not in record.data:
                continue
            old = record.data[field]
            new, changed = resolver.resolve(old)
            if changed:
                record.data[field] = new
                record.evidence.setdefault("_entity_resolution", []).append(
                    {"field": field, "original": old, "canonical": new}
                )
                resolved += 1

    text_fields = [str(item) for item in settings.get("near_duplicate_fields", ["title", "text"])]
    threshold = max(0, min(32, int(settings.get("near_duplicate_hamming", 3))))
    maximum = max(0, int(settings.get("near_duplicate_max_records", 5000)))
    hashes: list[tuple[int, ExtractedRecord]] = []
    duplicates = 0
    buckets: dict[tuple[int, int], list[tuple[int, ExtractedRecord]]] = defaultdict(list)
    for record in records[:maximum]:
        text = " ".join(str(record.data.get(field, "")) for field in text_fields).strip()
        if not text:
            continue
        value = simhash(text)
        match = None
        checked: set[int] = set()
        for band in range(4):
            band_value = (value >> (band * 16)) & 0xFFFF
            for previous_hash, previous in buckets.get((band, band_value), []):
                marker = id(previous)
                if marker in checked:
                    continue
                checked.add(marker)
                if hamming_distance(value, previous_hash) <= threshold:
                    match = previous
                    break
            if match:
                break
        if match:
            record.evidence.setdefault("_quality", {})["near_duplicate"] = True
            record.evidence["_quality"]["near_duplicate_source_url"] = match.source_url
            record.evidence["_quality"]["review_required"] = True
            duplicates += 1
        for band in range(4):
            band_value = (value >> (band * 16)) & 0xFFFF
            buckets[(band, band_value)].append((value, record))
        hashes.append((value, record))
    return {"entities_resolved": resolved, "near_duplicates": duplicates}
