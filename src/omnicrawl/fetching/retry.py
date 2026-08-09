from __future__ import annotations

import email.utils
import random
from collections.abc import Mapping
from datetime import datetime, timezone

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
            target = target.replace(tzinfo=timezone.utc)
        current = now or datetime.now(timezone.utc)
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
    delay += random.uniform(0.0, max(0.0, jitter) * delay)
    # S4.5 P3#131：jitter 叠加后不再突破封顶
    return min(maximum, max(0.0, delay))


def parse_retry_config(http_config: dict) -> dict:
    """从 http 段解析重试配置；默认值与 DEFAULTS 单点一致。

    `retries` 是总尝试次数（0 表示不重试）；`retry_on_status` 缺省时用
    RETRYABLE_STATUS，显式空列表表示不重试任何 HTTP 状态码。
    """
    raw_status = http_config.get("retry_on_status", RETRYABLE_STATUS)
    try:
        status_codes = {int(code) for code in raw_status}
    except (TypeError, ValueError):
        status_codes = set(RETRYABLE_STATUS)
    return {
        "max_retries": int(http_config.get("retries", 3)),
        "base_seconds": float(http_config.get("retry_base_seconds", 1)),
        "max_seconds": float(http_config.get("retry_max_seconds", 30)),
        "jitter": float(http_config.get("retry_jitter", 0.25)),
        "status_codes": status_codes,
    }
