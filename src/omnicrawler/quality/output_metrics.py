"""Count persisted declared-contract evidence without claiming source truth."""
from __future__ import annotations

from collections.abc import Iterable
from typing import Any


def record_quality_counts(evidence: Iterable[Any]) -> dict[str, int]:
    counts = {key: 0 for key in ("records", "assessed", "valid", "review", "unassessed", "invalid", "duplicates", "anomalies")}
    for item in evidence:
        counts["records"] += 1
        quality = item.get("_quality") if isinstance(item, dict) else None
        if (not isinstance(quality, dict) or type(quality.get("review_required")) is not bool
                or not isinstance(quality.get("validation_errors"), list) or not isinstance(quality.get("missing_required"), list)):
            counts["unassessed"] += 1
            continue
        counts["assessed"] += 1
        invalid = bool(quality["validation_errors"] or quality["missing_required"])
        duplicate = bool(quality.get("duplicate"))
        anomaly = bool(quality.get("anomalies"))
        review = quality["review_required"] or invalid or duplicate or anomaly
        counts["invalid"] += invalid
        counts["duplicates"] += duplicate
        counts["anomalies"] += anomaly
        counts["review"] += review
        counts["valid"] += not review
    return counts
