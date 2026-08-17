"""模板管理命令：搜索、识别、渲染、校验、打包模板。"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import yaml

from ..sources.site_inspector import inspect_url
from ..templates.template_catalog import TemplateProbe, bundled_template_catalog
from ..templates.template_diff import compare_template_files, merge_template_files
from ..templates.template_health import TemplatePack, validate_catalog


def _key_values(items: list[str], separator: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for item in items:
        if separator not in item:
            raise ValueError(f"参数必须使用 NAME{separator}VALUE 格式: {item}")
        key, value = item.split(separator, 1)
        key = key.strip()
        if not key:
            raise ValueError(f"参数名不能为空: {item}")
        result[key] = value.strip()
    return result


def execute(
    action: str, *,
    query: str = "", category: str = "", tags: list[str] | None = None,
    capabilities: list[str] | None = None,
    url: str = "", headers: list[str] | None = None,
    body_file: str = "", json_file: str = "", limit: int = 5,
    template_id: str = "", sets: list[str] | None = None,
    output: str = "", force: bool = False,
    include_legacy: bool = False,
    pack: str = "", overwrite: bool = False,
    target: str = "",
    timeout: float = 20.0,
    before: str = "", after: str = "",
    base: str = "", user: str = "", update: str = "",
) -> Any:
    catalog = bundled_template_catalog()

    if action == "list":
        records = catalog.search(query, category=category, tags=tags or [], capabilities=capabilities or [])
        # 按类别分组
        grouped: dict[str, list[dict[str, Any]]] = {}
        builtin_count = 0
        user_count = 0
        for record in records:
            cat = record.metadata.category or "未分类"
            grouped.setdefault(cat, []).append({
                "id": record.metadata.template_id,
                "name": record.metadata.name,
                "description": record.metadata.description,
                "tags": record.metadata.tags,
                "capabilities": record.metadata.capabilities,
            })
            if record.builtin:
                builtin_count += 1
            else:
                user_count += 1
        return {
            "total": len(records),
            "builtin": builtin_count,
            "user": user_count,
            "categories": grouped,
        }
    if action == "recommend":
        parsed_headers = _key_values(headers or [], ":")
        body = Path(body_file).read_text(encoding="utf-8") if body_file else ""
        json_data = json.loads(Path(json_file).read_text(encoding="utf-8")) if json_file else None
        matches = catalog.recommend(TemplateProbe(url, parsed_headers, body, json_data), limit)
        return [
            {
                "id": match.record.metadata.template_id,
                "name": match.record.metadata.name,
                "score": match.score,
                "reasons": match.reasons,
            }
            for match in matches
        ]
    if action == "render":
        rendered = catalog.render(template_id, _key_values(sets or [], "="))
        rendered.setdefault("project", {})["template_id"] = template_id
        target_path = Path(output).expanduser().resolve()
        target_path.parent.mkdir(parents=True, exist_ok=True)
        if target_path.exists() and not force:
            raise FileExistsError(f"目标已存在；使用 --force 才会覆盖: {target_path}")
        target_path.write_text(yaml.safe_dump(rendered, allow_unicode=True, sort_keys=False), encoding="utf-8")
        # E13：渲染出的配置先跑校验——内部不合法（缺必填/数值越界）直接报错，
        # 不再生成"能写盘但跑不起来"的配置；--force 仅放行写入，校验错误仍报告。
        from ..core.config import load_config, validate_config
        loaded = load_config(target_path)
        errors, _warnings = validate_config(loaded)
        if errors:
            detail = "\n".join(f"  - {item}" for item in errors)
            if force:
                print(f"⚠ 渲染出的配置校验不通过（--force 已放行，请手工修复）:\n{detail}", file=sys.stderr)
            else:
                raise ValueError(f"渲染出的配置校验不通过:\n{detail}")
        return {"created": str(target_path), "next": f"omnicrawler validate -c {target_path}"}
    if action == "validate":
        health = validate_catalog(catalog, include_legacy=include_legacy)
        payload = [
            {"id": item.template_id, "ok": item.ok, "errors": item.errors, "warnings": item.warnings}
            for item in health
        ]
        return {"ok": all(item.ok for item in health), "templates": payload}
    if action == "export-pack":
        template_ids = template_id if isinstance(template_id, list) else ([template_id] if template_id else [])
        raw = [catalog.get(tid) for tid in template_ids]
        missing = [tid for tid, rec in zip(template_ids, raw, strict=False) if rec is None]
        if missing:
            raise KeyError(f"模板不存在: {', '.join(missing)}")
        records = [rec for rec in raw if rec is not None]
        target = str(TemplatePack.export(records, Path(output).expanduser().resolve()))
        return {"created": str(target), "templates": [r.metadata.template_id for r in records]}
    if action == "import-pack":
        created = TemplatePack.import_pack(
            Path(pack).expanduser().resolve(), Path(target).expanduser().resolve(), overwrite=overwrite,
        )
        return {"created": [str(p) for p in created]}
    if action == "inspect":
        return inspect_url(url, catalog, timeout_seconds=timeout).to_dict()
    if action == "diff":
        return compare_template_files(Path(before), Path(after))
    if action == "merge":
        merged, conflicts = merge_template_files(
            Path(base).expanduser().resolve(), Path(user).expanduser().resolve(),
            Path(update).expanduser().resolve(),
        )
        target_path = Path(output).expanduser().resolve()
        if target_path.exists() and not force:
            raise FileExistsError(f"目标已存在；使用 --force 才会覆盖: {target_path}")
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_text(yaml.safe_dump(merged, allow_unicode=True, sort_keys=False), encoding="utf-8")
        return {"created": str(target_path), "conflicts": [item.to_dict() for item in conflicts]}
    raise ValueError(f"未知模板操作: {action}")
