from __future__ import annotations

import hashlib
import json
from typing import Any

from .template_catalog import TemplateRecord


def template_binding(record: TemplateRecord, parameters: dict[str, Any]) -> dict[str, Any]:
    """Share identity, never raw parameter values or an execution approval."""
    def digest(value: Any) -> str:
        return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                         separators=(",", ":"), default=str).encode()).hexdigest()

    return {
        "format": 1,
        "template_id": record.metadata.template_id,
        "template_version": record.metadata.version,
        "template_sha256": digest(dict(record.config)),
        "parameters_sha256": digest(parameters),
        "historical_reference_only": True,
    }
