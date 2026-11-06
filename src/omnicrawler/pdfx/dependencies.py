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


def _tessdata_identities(config: ProjectConfig, base: Path) -> dict[str, Any]:
    """Hash the selected traineddata files, not an unversioned directory name.

    Tesseract's language models affect output independently of the executable.
    Only the languages explicitly selected by the task participate: unrelated
    installed models do not invalidate otherwise reusable OCR results.
    """
    ocr = config.ocr
    if str(ocr.get("backend", "tesseract")).lower() != "tesseract":
        return {"configured": False}
    directory = Path(os.environ.get("TESSDATA_PREFIX", "")).expanduser() if os.environ.get("TESSDATA_PREFIX") else None
    if directory is None:
        candidates = [base / "tessdata"]
        command = str(ocr.get("command") or os.environ.get("TESSERACT_CMD", ""))
        if command:
            command_path = Path(command).expanduser()
            command_path = command_path if command_path.is_absolute() else base / command_path
            candidates.extend([command_path.parent / "tessdata", command_path.parent.parent / "share" / "tessdata"])
        directory = next((path for path in candidates if path.is_dir()), candidates[0])
    languages = [language.strip() for language in str(ocr.get("lang", "eng")).split("+") if language.strip()]
    return {"configured": True, "languages": languages,
            "files": {language: _file_identity(str(directory / f"{language}.traineddata"), base)
                      for language in languages}}


def ocr_digest(config: ProjectConfig, component_version: str | None = None) -> str:
    ocr = {key: value for key, value in config.ocr.items() if not key.startswith("_")}
    component_version = component_version or config.ocr.get("_resolved_component_version")
    command = ocr.get("command") or os.environ.get("TESSERACT_CMD", "")
    config_path = getattr(config, "path", None)
    base = Path(config_path).parent if config_path is not None else Path.cwd()
    identity = {
        "component_version": component_version,
        "command": _file_identity(command, base),
        "tessdata": _tessdata_identities(config, base),
    }
    return _digest({"contract": 1, "ocr": ocr, "runtime": identity})
