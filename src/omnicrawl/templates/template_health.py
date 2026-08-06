from __future__ import annotations

import hashlib
import json
import re
import zipfile
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

import yaml

from .template_catalog import TemplateCatalog, TemplateRecord


@dataclass(frozen=True, slots=True)
class TemplateHealth:
    template_id: str
    ok: bool
    errors: tuple[str, ...]
    warnings: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class StructureSnapshot:
    template_id: str
    captured_at: str
    source_url: str
    content_sha256: str
    features: tuple[str, ...]
    field_success: dict[str, float]

    @classmethod
    def from_html(
        cls,
        template_id: str,
        source_url: str,
        html: str,
        field_success: dict[str, float] | None = None,
    ) -> StructureSnapshot:
        return cls(
            template_id=template_id,
            captured_at=datetime.now(timezone.utc).isoformat(),
            source_url=source_url,
            content_sha256=hashlib.sha256(html.encode("utf-8")).hexdigest(),
            features=tuple(sorted(_html_features(html))),
            field_success=field_success or {},
        )

    def similarity(self, previous: StructureSnapshot) -> float:
        current = set(self.features)
        old = set(previous.features)
        if not current and not old:
            return 1.0
        return len(current & old) / max(1, len(current | old))

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(self), ensure_ascii=False, indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> StructureSnapshot:
        raw = json.loads(path.read_text(encoding="utf-8"))
        raw["features"] = tuple(raw.get("features", []))
        return cls(**raw)


def validate_template(record: TemplateRecord) -> TemplateHealth:
    errors: list[str] = []
    warnings: list[str] = []
    meta = record.metadata
    if not re.fullmatch(r"[a-z0-9][a-z0-9._/-]*", meta.template_id):
        errors.append("template.id must use stable lowercase path characters")
    if not meta.description.strip():
        errors.append("template.description is required")
    if not re.fullmatch(r"\d+\.\d+(?:\.\d+)?(?:[-+][A-Za-z0-9.-]+)?", meta.version):
        errors.append("template.version must be a semantic version")
    if not meta.capabilities:
        warnings.append("template.capabilities is empty")
    if not meta.verified_at:
        warnings.append("template.verified_at is missing")
    placeholders = TemplateCatalog.placeholders(record)
    undeclared = placeholders - set(meta.placeholders)
    if undeclared:
        errors.append("undeclared placeholders: " + ", ".join(sorted(undeclared)))
    unused = set(meta.placeholders) - placeholders
    if unused:
        warnings.append("unused placeholder declarations: " + ", ".join(sorted(unused)))
    source = record.config.get("source", {})
    if not isinstance(source, dict) or not source.get("kind"):
        errors.append("source.kind is required")
    if not isinstance(source, dict) or not isinstance(source.get("seeds"), list) or not source.get("seeds"):
        errors.append("source.seeds must contain at least one entry")
    return TemplateHealth(meta.template_id, not errors, tuple(errors), tuple(warnings))


def validate_catalog(catalog: TemplateCatalog, *, include_legacy: bool = False) -> list[TemplateHealth]:
    records = catalog.discover()
    if not include_legacy:
        records = [record for record in records if record.metadata.category != "legacy"]
    return [validate_template(record) for record in records]


class TemplatePack:
    """Safe import/export of template bundles with manifest hashes and no implicit overwrite."""

    MANIFEST = "omnicrawl-template-pack.json"

    @classmethod
    def export(cls, records: Iterable[TemplateRecord], target: Path) -> Path:
        selected = list(records)
        target.parent.mkdir(parents=True, exist_ok=True)
        manifest: dict[str, Any] = {"format": 1, "created_at": datetime.now(timezone.utc).isoformat(), "files": {}}
        with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for record in selected:
                name = f"templates/{record.metadata.template_id}.yaml"
                payload = record.path.read_bytes()
                manifest["files"][name] = hashlib.sha256(payload).hexdigest()
                archive.writestr(name, payload)
            archive.writestr(cls.MANIFEST, json.dumps(manifest, ensure_ascii=False, indent=2))
        return target

    @classmethod
    def import_pack(cls, pack: Path, target_dir: Path, *, overwrite: bool = False) -> list[Path]:
        created: list[Path] = []
        target_dir = target_dir.resolve()
        target_dir.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(pack) as archive:
            manifest = json.loads(archive.read(cls.MANIFEST))
            files = manifest.get("files", {})
            if not isinstance(files, dict):
                raise ValueError("Invalid template pack manifest")
            prepared: list[tuple[Path, bytes]] = []
            for name, expected_hash in files.items():
                pure = PurePosixPath(name)
                if pure.is_absolute() or ".." in pure.parts or not str(pure).startswith("templates/"):
                    raise ValueError(f"Unsafe template pack path: {name}")
                payload = archive.read(name)
                if hashlib.sha256(payload).hexdigest() != expected_hash:
                    raise ValueError(f"Template pack checksum mismatch: {name}")
                yaml.safe_load(payload.decode("utf-8"))
                destination = (target_dir / Path(*pure.parts[1:])).resolve()
                if target_dir not in destination.parents:
                    raise ValueError(f"Unsafe template destination: {name}")
                if destination.exists() and not overwrite:
                    raise FileExistsError(f"Template already exists: {destination}")
                prepared.append((destination, payload))
            for destination, payload in prepared:
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(payload)
                created.append(destination)
        return created


def _html_features(html: str) -> set[str]:
    features: set[str] = set()
    for tag in re.findall(r"<\s*([A-Za-z][A-Za-z0-9:-]*)", html):
        features.add("tag:" + tag.casefold())
    for attribute, prefix in (("id", "id"), ("class", "class")):
        pattern = rf"\b{attribute}\s*=\s*['\"]([^'\"]+)['\"]"
        for value in re.findall(pattern, html, re.IGNORECASE):
            for token in value.split()[:20]:
                if len(token) <= 100:
                    features.add(f"{prefix}:{token.casefold()}")
    return features
