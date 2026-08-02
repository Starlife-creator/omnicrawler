from __future__ import annotations

import email.utils
import random
from collections.abc import Mapping
from datetime import UTC, datetime

RETRYABLE_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504})


def retry_after_seconds(headers: Mapping[str, str], *, now: datetime | None = None) -> float | None:
    value = str(headers.get("Retry-After", headers.get("retry-after", ""))).strip()
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        pass
    try:
        target = email.utils.parsedate_to_datetime(value)
        if target.tzinfo is None:
            target = target.replace(tzinfo=UTC)
        current = now or datetime.now(UTC)
        return max(0.0, (target - current).total_seconds())
    except (TypeError, ValueError, OverflowError):
        return None


def backoff_seconds(
    attempt: int,
    *,
    base: float = 1.0,
    maximum: float = 30.0,
    jitter: float = 0.25,
) -> float:
    delay = min(maximum, max(0.0, base) * (2 ** max(0, attempt)))
    return delay + random.uniform(0.0, max(0.0, jitter) * delay)


def parse_retry_config(http_config: dict) -> dict:
    return {
        "max_retries": int(http_config.get("retry_max", 3)),
        "base_seconds": float(http_config.get("retry_base_seconds", 1)),
        "max_seconds": float(http_config.get("retry_max_seconds", 30)),
        "jitter": float(http_config.get("retry_jitter", 0.25)),
        "status_codes": set(map(int, http_config.get("retry_on_status", [429, 502, 503, 504]))),
    }
