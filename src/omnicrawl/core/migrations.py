from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import yaml

from .utils import deep_merge

CURRENT_CONFIG_VERSION = 5


def migrate_config(value: dict[str, Any]) -> tuple[dict[str, Any], tuple[str, ...]]:
    """Non-destructively normalize legacy keys; unknown values are always retained."""
    raw = copy.deepcopy(value)
    notes: list[str] = []
    try:
        version = int(raw.get("config_version", 1))
    except (TypeError, ValueError):
        version = 1
        notes.append("invalid config_version treated as version 1")
    if version > CURRENT_CONFIG_VERSION:
        notes.append(
            f"configuration version {version} is newer than core version {CURRENT_CONFIG_VERSION}; unknown fields retained"
        )
        return raw, tuple(notes)

    source = raw.setdefault("source", {})
    if isinstance(source, dict):
        if not source.get("seeds") and isinstance(raw.get("seed_urls"), list):
            source["seeds"] = copy.deepcopy(raw["seed_urls"])
            notes.append("seed_urls copied to source.seeds")
        if str(source.get("kind", "")).casefold() == "rss":
            source["kind"] = "feed"
            notes.append("source.kind rss migrated to feed")

    # S4.5 P3#129：迁移旧键后清理，杜绝新旧并存（双重消费/歧义）
    if isinstance(raw.get("seed_urls"), list) and isinstance(source.get("seeds"), list):
        del raw["seed_urls"]
        notes.append("legacy seed_urls removed")

    legacy_output = raw.get("output")
    if isinstance(legacy_output, dict):
        current = raw.get("outputs", {})
        raw["outputs"] = deep_merge(legacy_output, current if isinstance(current, dict) else {})
        notes.append("output copied to outputs")
        del raw["output"]  # S4.5 P3#129：旧键清理
        notes.append("legacy output removed")

    crawl = raw.get("crawl", {})
    if isinstance(crawl, dict) and "delay_seconds" in crawl:
        http = raw.setdefault("http", {})
        if isinstance(http, dict) and "delay_seconds" not in http:
            http["delay_seconds"] = crawl["delay_seconds"]
            notes.append("crawl.delay_seconds copied to http.delay_seconds")
        # S4.5 P3#129：旧键清理（复制完成即删除，勿再保留）
        del crawl["delay_seconds"]
        notes.append("legacy crawl.delay_seconds removed")
    if isinstance(crawl, dict) and isinstance(crawl.get("pagination"), dict):
        source = raw.setdefault("source", {})
        if isinstance(source, dict) and "pagination" not in source:
            pagination = copy.deepcopy(crawl["pagination"])
            if "param" in pagination and "parameter" not in pagination:
                pagination["parameter"] = pagination.pop("param")
            if pagination.get("type") is None:
                pagination["type"] = "page"
            source["pagination"] = pagination
            notes.append("crawl.pagination copied to source.pagination")
            del crawl["pagination"]  # S4.5 P3#129：旧键清理
            notes.append("legacy crawl.pagination removed")

    extract = raw.get("extract", {})
    if isinstance(extract, dict) and extract.get("mode") == "json":
        if "item_selector" in extract and "item_path" not in extract:
            extract["item_path"] = extract["item_selector"]
            notes.append("extract.item_selector copied to extract.item_path for JSON sources")
        if "item_path" in extract and "item_selector" in extract:
            del extract["item_selector"]  # S4.5 P3#129：旧键清理
            notes.append("legacy extract.item_selector removed")

    if isinstance(raw.get("plugin_paths"), list):
        plugins = raw.setdefault("plugins", {})
        if isinstance(plugins, dict) and not plugins.get("paths"):
            plugins["paths"] = copy.deepcopy(raw["plugin_paths"])
            notes.append("plugin_paths copied to plugins.paths")
        if isinstance(plugins.get("paths"), list):
            del raw["plugin_paths"]  # S4.5 P3#129：旧键清理
            notes.append("legacy plugin_paths removed")

    raw["config_version"] = CURRENT_CONFIG_VERSION
    return raw, tuple(notes)


def migrate_file(source: Path, target: Path, *, overwrite: bool = False) -> tuple[Path, tuple[str, ...]]:
    source = source.expanduser().resolve()
    target = target.expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(source)
    if target.exists() and not overwrite:
        raise FileExistsError(target)
    value = yaml.safe_load(source.read_text(encoding="utf-8")) or {}
    if not isinstance(value, dict):
        raise ValueError("Configuration root must be a YAML mapping")
    migrated, notes = migrate_config(value)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(yaml.safe_dump(migrated, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return target, notes
