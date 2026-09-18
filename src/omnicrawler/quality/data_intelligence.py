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


#: 不参与"近似重复"比较的框架列（每行都有、与内容无关）
_DEDUP_SKIP_FIELDS = frozenset({"record_id", "source_url", "record_type", "created_at"})
#: 自动挑选参与比较的字段时，最多取几个（宽表不必把所有列都拉进比较）
_DEDUP_AUTO_FIELD_LIMIT = 12
#: 自动挑选字段时抽样的记录数
_DEDUP_AUTO_SAMPLE = 200


def _effective_dedup_fields(
    records: list[ExtractedRecord], configured: list[str]
) -> list[str]:
    """决定「哪些字段参与近似重复比较」。

    ★ 显式配置优先；**未配置时，用当前记录里实际存在的非空文本字段**。

    为什么必须有这个兜底（2026-09-18 走查 R1.1 实测）：旧默认是英文硬编码
    ``["title", "text"]``，而本项目分析器产出的字段名是中文（标题 / 正文 / 内容_p …）
    ⇒ 取不到任何值 ⇒ 拼接出的 ``text`` 恒为空 ⇒ ``continue`` ⇒ **一条记录都不会进入
    simhash** ⇒ ``near_duplicates`` 恒为 0，而质量报告照样报
    ``average_quality_score: 1.0``。实测场景：某电商站点交付 448 条、实际只有 80 个不同商品，
    报告却显示零重复。即**不是「没发现重复」，而是判据从未被调用**——与 ``record_identity``
    当年只认英文键属同一类缺陷（见 tests/unit/extraction/test_semantic.py）。

    兜底取向：**宁可多比，不可不比**；实际比较范围随返回值里的 ``dedup_fields`` 一并回报，
    让"没有重复"与"没有比对"在报告里可区分。
    """
    if configured:
        return configured
    picked: list[str] = []
    for record in records[:_DEDUP_AUTO_SAMPLE]:
        for name, value in record.data.items():
            key = str(name)
            if key in _DEDUP_SKIP_FIELDS or key in picked:
                continue
            if isinstance(value, str) and value.strip():
                picked.append(key)
                if len(picked) >= _DEDUP_AUTO_FIELD_LIMIT:
                    return picked
    return picked


def enrich_records(records: list[ExtractedRecord], config: AppConfig) -> dict[str, Any]:
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

    text_fields = _effective_dedup_fields(
        records, [str(item) for item in settings.get("near_duplicate_fields", [])]
    )
    threshold = max(0, min(32, int(settings.get("near_duplicate_hamming", 3))))
    maximum = max(0, int(settings.get("near_duplicate_max_records", 5000)))
    hashes: list[tuple[int, ExtractedRecord]] = []
    duplicates = 0
    compared = 0
    buckets: dict[tuple[int, int], list[tuple[int, ExtractedRecord]]] = defaultdict(list)
    for record in records[:maximum]:
        text = " ".join(str(record.data.get(field, "")) for field in text_fields).strip()
        if not text:
            continue
        # ★ 记录"判据真的用上了"——报告据此区分「没有重复」与「没有比对」（走查 R1.1）。
        #   该键会被 quality.assess_records 的 prior_quality 合并逻辑保留，不会被覆盖。
        record.evidence.setdefault("_quality", {})["dedup_compared"] = True
        compared += 1
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
    return {
        "entities_resolved": resolved,
        "near_duplicates": duplicates,
        # 判据可观测性（走查 R1.1）：这四项让「没重复」与「没比对」在报告里可区分。
        "dedup_compared": compared,
        "dedup_skipped": max(0, min(len(records), maximum) - compared),
        "dedup_truncated": max(0, len(records) - maximum),
        "dedup_fields": list(text_fields),
    }
