from __future__ import annotations

import hashlib
import json
import platform
import re
import sqlite3
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

import yaml

from .. import __version__
from ..core.config import AppConfig

MANIFEST_NAME = "omnicrawler-package.json"
_SENSITIVE = (
    "password",
    "passwd",
    "secret",
    "token",
    "authorization",
    "cookie",
    "proxy",
    "api_key",
    "api-key",
    "access_key",
    "client_secret",
)


# B08-007：值内嵌 URL 凭据（scheme://user:pass@host）也须脱敏——研究包默认即分享，
# 凭据可能藏在 jdbc:/http 连接串里而键名不含敏感词。
_URL_CREDENTIAL_RE = re.compile(r"(//[^/\s:@]+):([^@/\s]+)@")


def _redact(value: Any, key: str = "") -> Any:
    normalized_key = key.casefold().replace(" ", "_")
    if any(marker in normalized_key for marker in _SENSITIVE) and value not in (None, "", [], {}):
        return "<redacted; restore through secret:// or environment variable>"
    if isinstance(value, dict):
        return {str(name): _redact(item, str(name)) for name, item in value.items()}
    if isinstance(value, list):
        return [_redact(item) for item in value]
    if isinstance(value, str) and _URL_CREDENTIAL_RE.search(value):
        return _URL_CREDENTIAL_RE.sub(r"\1:<redacted>@", value)
    return value


def _sqlite_snapshot(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    src = sqlite3.connect(source)
    dst = sqlite3.connect(target)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()


def create_research_package(
    config: AppConfig,
    target: Path,
    *,
    include_raw: bool = False,
    include_artifacts: bool = False,
) -> dict[str, Any]:
    """Create a checksum-verified, secret-redacted package for research reproduction."""

    target = target.resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    entries: dict[str, bytes | Path] = {}
    try:
        source_config = yaml.safe_load(config.path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        source_config = config.raw
    safe_config = _redact(source_config)
    entries["project/config.redacted.yaml"] = yaml.safe_dump(
        safe_config, allow_unicode=True, sort_keys=False
    ).encode("utf-8")
    environment = {
        "omnicrawler_version": __version__,
        "python": platform.python_version(),
        "platform": platform.platform(),
            "created_at": datetime.now(timezone.utc).isoformat(),
    }
    entries["project/environment.json"] = json.dumps(
        environment, ensure_ascii=False, indent=2
    ).encode("utf-8")
    output = config.workspace / "output"
    if output.is_dir():
        for path in sorted(output.rglob("*")):
            if path.is_file():
                entries[f"project/output/{path.relative_to(output).as_posix()}"] = path
    for path in sorted((config.root / "plugins").rglob("*")) if (config.root / "plugins").is_dir() else []:
        if path.is_file() and "__pycache__" not in path.parts:
            entries[f"project/plugins/{path.relative_to(config.root / 'plugins').as_posix()}"] = path
    for folder, enabled in (("raw", include_raw), ("artifacts", include_artifacts)):
        source = config.workspace / folder
        if enabled and source.is_dir():
            for path in sorted(source.rglob("*")):
                if path.is_file():
                    entries[f"project/{folder}/{path.relative_to(source).as_posix()}"] = path

    with tempfile.TemporaryDirectory(prefix="omnicrawler-package-") as temporary:
        snapshot = Path(temporary) / "state.sqlite3"
        state_path = config.workspace / "state.sqlite3"
        if state_path.is_file():
            _sqlite_snapshot(state_path, snapshot)
            entries["project/state.sqlite3"] = snapshot
        readme = (
            b"OmniCrawler research reproduction package\n\n"
            b"1. Install OmniCrawler with install_windows.ps1 or pip install .\n"
            b"2. Review project/config.redacted.yaml and restore required secrets using secret:// references.\n"
            b"3. Run: omnicrawl run -c project/config.redacted.yaml --resume\n"
            b"The manifest contains SHA-256 hashes for integrity verification.\n"
        )
        entries["README.txt"] = readme
        hashes = {
            name: hashlib.sha256(value.read_bytes() if isinstance(value, Path) else value).hexdigest()
            for name, value in entries.items()
        }
        manifest = {
            "format": 1,
            "kind": "research-reproduction",
            "created_at": environment["created_at"],
            "include_raw": include_raw,
            "include_artifacts": include_artifacts,
            "files": hashes,
        }
        with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
            for name, value in entries.items():
                archive.writestr(name, value.read_bytes() if isinstance(value, Path) else value)
            archive.writestr(MANIFEST_NAME, json.dumps(manifest, ensure_ascii=False, indent=2))
    return {"created": str(target), "files": len(entries), "sha256": _sha256(target)}


def create_backup(config: AppConfig, target: Path, *, include_raw: bool = False) -> dict[str, Any]:
    return create_research_package(
        config,
        target,
        include_raw=include_raw,
        include_artifacts=True,
    )


def restore_package(package: Path, target: Path) -> dict[str, Any]:
    package = package.resolve()
    target = target.resolve()
    target.mkdir(parents=True, exist_ok=True)
    if any(target.iterdir()):
        raise FileExistsError("Restore target must be an empty directory")
    with zipfile.ZipFile(package) as archive:
        manifest = json.loads(archive.read(MANIFEST_NAME))
        files = manifest.get("files", {})
        if not isinstance(files, dict):
            raise ValueError("Invalid package manifest")
        prepared: list[tuple[Path, bytes]] = []
        for name, expected in files.items():
            pure = PurePosixPath(name)
            if pure.is_absolute() or ".." in pure.parts:
                raise ValueError(f"Unsafe package path: {name}")
            payload = archive.read(name)
            if hashlib.sha256(payload).hexdigest() != expected:
                raise ValueError(f"Checksum mismatch: {name}")
            destination = (target / Path(*pure.parts)).resolve()
            if destination != target and target not in destination.parents:
                raise ValueError(f"Unsafe restore destination: {name}")
            prepared.append((destination, payload))
        for destination, payload in prepared:
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(payload)
    return {"restored": str(target), "files": len(prepared), "verified": True}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()
