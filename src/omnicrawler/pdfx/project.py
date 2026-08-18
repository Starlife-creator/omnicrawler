"""项目向导、模板校验与字段摘要。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from .config import load_config, validate_runtime_config
from .safe_regex import validate_pattern
from .templates import is_builtin_pdf_reference, resolve_builtin_pdf_reference, resolve_pdf_project_config
from .utils import atomic_output_path


def create_project_config(
    template_path: str | Path,
    destination: str | Path,
    *,
    project_name: str,
    input_dir: str | Path,
    work_dir: str | Path,
    output_dir: str | Path,
    ocr_backend: str = "none",
) -> Path:
    template = resolve_pdf_project_config(template_path)
    destination = Path(destination).expanduser().resolve()
    if not template.exists():
        raise FileNotFoundError(f"字段模板不存在: {template}")
    raw = yaml.safe_load(template.read_text(encoding="utf-8")) or {}
    normalization = raw.get("normalization")
    if isinstance(normalization, dict):
        entity_reference = normalization.get("entity_master_csv")
        if entity_reference and is_builtin_pdf_reference(str(entity_reference)):
            source = resolve_builtin_pdf_reference(str(entity_reference))
            asset = (destination.parent / "templates" / "pdf" / source.name).resolve()
            asset.parent.mkdir(parents=True, exist_ok=True)
            asset.write_bytes(source.read_bytes())
            normalization["entity_master_csv"] = str(asset)
    raw.update(
        {
            "project_name": project_name.strip() or destination.stem,
            "input_dir": str(Path(input_dir).expanduser().resolve()),
            "work_dir": str(Path(work_dir).expanduser().resolve()),
            "output_dir": str(Path(output_dir).expanduser().resolve()),
            "database": str(Path(work_dir).expanduser().resolve() / "pipeline.sqlite3"),
        }
    )
    raw.setdefault("ocr", {})["backend"] = ocr_backend
    with atomic_output_path(destination, suffix=destination.suffix or ".yaml") as temp:
        temp.write_text(
            yaml.safe_dump(raw, allow_unicode=True, sort_keys=False, width=120),
            encoding="utf-8",
        )
    return destination


def validate_project_template(config_path: str | Path) -> dict[str, Any]:
    config = load_config(config_path)
    errors: list[str] = []
    warnings: list[str] = []
    for field in config.fields:
        for pattern in field.patterns:
            try:
                validate_pattern(pattern)
            except Exception as exc:
                errors.append(f"{field.label} ({field.name}) 正则无效: {exc}")
    try:
        warnings = validate_runtime_config(config)
    except (TypeError, ValueError) as exc:
        errors.append(f"运行配置无效: {exc}")
    return {
        "valid": not errors,
        "errors": errors,
        "warnings": warnings,
        "project_name": config.project_name,
        "input_dir": str(config.input_dir),
        "work_dir": str(config.work_dir),
        "output_dir": str(config.output_dir),
        "database": str(config.database),
        "fields": [
            {
                "name": field.name,
                "label": field.label,
                "type": field.type,
                "required": field.required,
                "aliases": field.aliases,
                "patterns": len(field.patterns),
            }
            for field in config.fields
        ],
    }
