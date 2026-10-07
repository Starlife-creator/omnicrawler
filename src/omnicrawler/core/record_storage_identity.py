"""One record ID definition for persistence and provenance."""
from __future__ import annotations

import hashlib
import uuid

from .models import CrawlRequest, ExtractedRecord
from .utils import json_text


def storage_identity(run_id: str, request: CrawlRequest, record: ExtractedRecord,
                     index: int, deduplicate_by: tuple[str, ...] = ()) -> tuple[str, bool]:
    identity = [record.data.get(name) for name in deduplicate_by]
    stable = bool(identity) and all(value not in (None, "", []) for value in identity)
    key = f"{run_id}:{request.fingerprint}:{index}"
    if stable:
        digest = hashlib.sha256(json_text(record.data).encode("utf-8")).hexdigest()
        key = f"{run_id}:entity:{record.record_type}:{json_text(identity)}:{digest}"
    return uuid.uuid5(uuid.NAMESPACE_URL, key).hex, stable
