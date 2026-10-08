"""Semantic dependencies of extraction from already parsed PDF pages."""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict

from .config import ProjectConfig
from .normalization import EntityResolver


def extraction_digest(config: ProjectConfig, resolver: EntityResolver) -> str:
    payload = {
        "contract": 1,
        "fields": [asdict(field) for field in config.fields],
        "retrieval": config.retrieval,
        "extraction": {key: value for key, value in config.extraction.items() if key != "workers"},
        "normalization": config.normalization,
        "validation": config.validation,
        "llm": config.llm,
        "entity_aliases": resolver.aliases,
    }
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True,
                                    default=str).encode("utf-8")).hexdigest()
