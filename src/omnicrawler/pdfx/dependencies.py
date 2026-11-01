"""Semantic dependencies of extraction from already parsed PDF pages."""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict
from pathlib import Path
from typing import Any

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
    return _digest(payload)


def _digest(payload: Any) -> str:
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True,
                                    default=str).encode("utf-8")).hexdigest()


def _file_identity(value: Any, base: Path) -> dict[str, Any]:
    if not isinstance(value, str) or not value.strip():
        return {"configured": False}
    path = Path(value).expanduser()
    path = path if path.is_absolute() else base / path
    try:
        if path.is_symlink() or not path.is_file():
            return {"configured": True, "path": str(path), "identity": "missing"}
        digest = hashlib.sha256()
        size = 0
        with path.open("rb") as stream:
            while block := stream.read(1024 * 1024):
                size += len(block)
                digest.update(block)
        return {"configured": True, "path": str(path.resolve()),
                "size": size, "sha256": digest.hexdigest()}
    except OSError as exc:
        return {"configured": True, "path": str(path), "identity": type(exc).__name__}


def parser_digest(config: ProjectConfig, parser_version: str) -> str:
    return _digest({"contract": 1, "parser_version": parser_version,
                    "parser": {key: value for key, value in config.parser.items() if key != "workers"}})


def ocr_digest(config: ProjectConfig, component_version: str | None = None) -> str:
    ocr = {key: value for key, value in config.ocr.items() if not key.startswith("_")}
    component_version = component_version or config.ocr.get("_resolved_component_version")
    command = ocr.get("command") or os.environ.get("TESSERACT_CMD", "")
    config_path = getattr(config, "path", None)
    base = Path(config_path).parent if config_path is not None else Path.cwd()
    identity = {
        "component_version": component_version,
        "command": _file_identity(command, base),
    }
    return _digest({"contract": 1, "ocr": ocr, "runtime": identity})
