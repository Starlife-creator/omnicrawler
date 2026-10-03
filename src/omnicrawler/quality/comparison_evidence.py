"""Compare labeled snapshots through the production extractor, without network I/O."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import Any

from ..core.config import AppConfig
from ..core.models import FetchResult
from ..extraction.extractors import HTMLProcessor
from .shadow_repair import RepairCandidate, ShadowComparison, config_digest, shadow_config


@dataclass(frozen=True, slots=True)
class ComparisonSample:
    """A captured response and manually accepted, ordered production record data."""

    sample_id: str
    response: FetchResult
    expected: tuple[dict[str, Any], ...]


def compare_snapshots(
    config: AppConfig,
    candidate: RepairCandidate,
    current: Sequence[ComparisonSample],
    historical: Sequence[ComparisonSample],
) -> ShadowComparison:
    """Require improvement on current data and exact preservation of accepted history.

    Missing evidence, duplicate identities and mismatched baseline rules fail closed.
    Full record comparisons preserve item scope, attributes, regex and list semantics.
    Expected records must include all extracted fields, not only the repaired field.
    """
    unknown = ShadowComparison(0, 0, 0.0, 0.0, 0, False)
    samples = [*current, *historical]
    ids = [sample.sample_id for sample in samples]
    if not current or not historical or any(not value for value in ids) or len(set(ids)) != len(ids):
        return unknown
    if any(not sample.expected or not 200 <= sample.response.status < 300 for sample in samples):
        return unknown
    fields = config.section("extract").get("fields", {})
    field = fields.get(candidate.field, {}) if isinstance(fields, dict) else {}
    key = "selector" if candidate.rule_type == "css" else candidate.rule_type
    if not isinstance(field, dict) or field.get(key) != candidate.old_rule:
        return unknown
    old_processor = HTMLProcessor(config)
    shadow = shadow_config(config.raw, candidate)
    new_processor = HTMLProcessor(replace(config, raw=shadow))
    old_count = new_count = old_correct = new_correct = false_matches = expected_count = 0
    compatible = True
    for index, sample in enumerate(samples):
        old = [record.data for record in old_processor.process(sample.response).records]
        new = [record.data for record in new_processor.process(sample.response).records]
        expected = list(sample.expected)
        if index >= len(current):
            compatible = compatible and old == expected and new == expected
            continue
        old_count += len(old)
        new_count += len(new)
        expected_count += len(expected)
        old_correct += sum(value == expected[i] for i, value in enumerate(old) if i < len(expected))
        new_correct += sum(value == expected[i] for i, value in enumerate(new) if i < len(expected))
        false_matches += sum(i >= len(expected) or value != expected[i] for i, value in enumerate(new))
    return ShadowComparison(
        old_count, new_count, old_correct / expected_count,
        new_correct / expected_count, false_matches, compatible,
        config_digest(config.raw), config_digest(shadow),
        hashlib.sha256(json.dumps([
            {"id": sample.sample_id, "body": sample.response.content_hash,
             "url": sample.response.final_url, "expected": sample.expected,
             "historical": index >= len(current)}
            for index, sample in enumerate(samples)
        ], ensure_ascii=False, sort_keys=True).encode()).hexdigest(),
    )
