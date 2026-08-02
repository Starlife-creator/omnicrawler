from __future__ import annotations

import json
import re
import urllib.parse
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import yaml

from ..core.utils import atomic_write


@dataclass(frozen=True, slots=True)
class ApiEndpointProfile:
    endpoint: str
    sample_url: str
    method: str
    status: int
    content_type: str
    item_path: str
    item_count: int
    pagination: dict[str, Any]
    schema: dict[str, Any]
    suggested_fields: dict[str, Any]
    confidence: float
    request_headers: dict[str, str]
    request_payload: Any = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def discover_api_endpoints(responses: list[dict[str, Any]]) -> list[ApiEndpointProfile]:
    """Infer reusable REST templates from captured browser XHR/fetch responses."""

    profiles: list[ApiEndpointProfile] = []
    seen: set[tuple[str, str]] = set()
    for response in responses:
        if not isinstance(response, dict) or "json" not in response:
            continue
        sample_url = str(response.get("url", ""))
        method = str(response.get("method", "GET")).upper()
        endpoint = normalize_endpoint(sample_url)
        key = (method, endpoint)
        if not endpoint or key in seen:
            continue
        seen.add(key)
        payload = response["json"]
        item_path, items = _best_item_array(payload)
        sample = items[0] if items else payload
        schema = infer_schema(sample)
        fields = _suggest_fields(sample)
        pagination = _infer_pagination(payload, sample_url)
        evidence = int(bool(item_path)) + int(bool(fields)) + int(bool(pagination))
        profiles.append(
            ApiEndpointProfile(
                endpoint,
                sample_url,
                method,
                int(response.get("status", 0)),
                str(response.get("content_type", "application/json")),
                item_path,
                len(items),
                pagination,
                schema,
                fields,
                round(min(1.0, 0.4 + evidence * 0.2), 3),
                {
                    str(key): str(value)
                    for key, value in (response.get("request_headers", {}) or {}).items()
                    if str(key).casefold() not in {"authorization", "cookie", "proxy-authorization"}
                },
                response.get("request_payload"),
            )
        )
    profiles.sort(key=lambda item: (-item.confidence, -item.item_count, item.endpoint))
    return profiles


def normalize_endpoint(url: str) -> str:
    parts = urllib.parse.urlsplit(url)
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        return ""
    path = re.sub(r"/(?:(?:\d+)|(?:[0-9a-f]{8}-[0-9a-f-]{27,}))(?=/|$)", "/{id}", parts.path, flags=re.I)
    query = urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
    stable = sorted({key for key, _value in query if key.casefold() not in {"_", "ts", "timestamp", "nonce", "token"}})
    suffix = "?" + "&".join(f"{key}={{{key}}}" for key in stable) if stable else ""
    return urllib.parse.urlunsplit((parts.scheme, parts.netloc, path, suffix, ""))


def infer_schema(value: Any, *, depth: int = 0) -> dict[str, Any]:
    if depth >= 6:
        return {"type": "unknown"}
    if value is None:
        return {"type": "null"}
    if isinstance(value, bool):
        return {"type": "boolean"}
    if isinstance(value, int):
        return {"type": "integer"}
    if isinstance(value, float):
        return {"type": "number"}
    if isinstance(value, str):
        kind = "string"
        if re.fullmatch(r"https?://.+", value):
            kind = "url"
        elif re.fullmatch(r"\d{4}-\d{2}-\d{2}(?:[T ][^ ]+)?", value):
            kind = "datetime"
        return {"type": kind}
    if isinstance(value, list):
        schemas = [infer_schema(item, depth=depth + 1) for item in value[:20]]
        unique = {json.dumps(item, sort_keys=True) for item in schemas}
        return {
            "type": "array",
            "items": schemas[0] if len(unique) == 1 and schemas else {"oneOf": [json.loads(item) for item in sorted(unique)]},
        }
    if isinstance(value, dict):
        return {
            "type": "object",
            "properties": {str(key): infer_schema(item, depth=depth + 1) for key, item in value.items()},
        }
    return {"type": type(value).__name__}


def _walk(value: Any, path: str = ""):
    yield path, value
    if isinstance(value, dict):
        for key, item in value.items():
            child = f"{path}.{key}" if path else str(key)
            yield from _walk(item, child)
    elif isinstance(value, list):
        for index, item in enumerate(value[:5]):
            child = f"{path}.{index}" if path else str(index)
            yield from _walk(item, child)


def _best_item_array(payload: Any) -> tuple[str, list[Any]]:
    choices: list[tuple[int, int, str, list[Any]]] = []
    for path, value in _walk(payload):
        if not isinstance(value, list) or not value:
            continue
        object_count = sum(isinstance(item, dict) for item in value[:50])
        if not object_count:
            continue
        semantic = int(path.rsplit(".", 1)[-1].casefold() in {"items", "results", "data", "records", "rows", "list"})
        choices.append((semantic, len(value), path, value))
    if not choices:
        return "", []
    _semantic, _length, path, value = max(choices, key=lambda item: (item[0], item[1], -len(item[2])))
    return path, value


def _infer_pagination(payload: Any, url: str) -> dict[str, Any]:
    result: dict[str, Any] = {}
    query = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))
    for key in ("page", "offset", "limit", "per_page", "cursor", "after", "skip"):
        if key in query:
            result[key] = {"location": "query", "sample": query[key]}
    wanted = {"next", "next_url", "nexturl", "next_cursor", "nextcursor", "cursor", "has_more", "has_next_page", "total"}
    for path, value in _walk(payload):
        leaf = path.rsplit(".", 1)[-1].casefold()
        if leaf in wanted and not isinstance(value, (dict, list)):
            result.setdefault(leaf, {"location": "response", "path": path, "sample": value})
    return result


def _suggest_fields(sample: Any) -> dict[str, Any]:
    if not isinstance(sample, dict):
        return {}
    fields: dict[str, Any] = {}
    for key, value in list(sample.items())[:80]:
        if isinstance(value, (dict, list)):
            continue
        rule: dict[str, Any] = {"path": str(key)}
        schema_type = infer_schema(value).get("type")
        if schema_type in {"integer", "number", "datetime"}:
            rule["data_type"] = schema_type
        if str(key).casefold() in {"id", "uuid", "doi", "url", "title", "name"}:
            rule["required"] = True
        fields[str(key)] = rule
    return fields


def write_discovery_bundle(
    responses: list[dict[str, Any]],
    output_dir: Path,
    *,
    name: str = "discovered_api",
) -> dict[str, Any]:
    profiles = discover_api_endpoints(responses)
    output_dir.mkdir(parents=True, exist_ok=True)
    report_path = output_dir / f"{name}.json"
    validations = [_validate_from_captures(profile, responses) for profile in profiles]
    report = {
        "endpoints": [
            {**profile.to_dict(), "validation": validation}
            for profile, validation in zip(profiles, validations, strict=False)
        ]
    }
    atomic_write(report_path, json.dumps(report, ensure_ascii=False, indent=2, default=str).encode("utf-8"))
    templates: list[str] = []
    for index, profile in enumerate(profiles, 1):
        validation = validations[index - 1]
        source: dict[str, Any] = {
            "kind": "rest", "seeds": [profile.sample_url], "method": profile.method,
        }
        if profile.request_headers:
            source["headers"] = profile.request_headers
        if profile.request_payload is not None:
            source["payload"] = profile.request_payload
        pagination = _pagination_config(profile)
        if pagination:
            source["pagination"] = pagination
        config = {
            "project": {"name": f"{name}_{index}", "workspace": f"work/{name}_{index}"},
            "source": source,
            "crawl": {"max_pages": 100, "concurrency": 2},
            "http": {"respect_robots": True, "delay_seconds": 1.0, "headers": {"Accept": "application/json"}},
            "extract": {"mode": "json", "item_path": profile.item_path or "$", "fields": profile.suggested_fields},
            "outputs": {"jsonl": True, "csv": True, "xlsx": True},
            "api_discovery": {"validation": validation},
        }
        path = output_dir / f"{name}_{index}.yaml"
        atomic_write(path, yaml.safe_dump(config, allow_unicode=True, sort_keys=False).encode("utf-8"))
        templates.append(str(path))
    return {
        "report": str(report_path),
        "templates": templates,
        "endpoints": len(profiles),
        "capture_validated": sum(item["status"] == "validated" for item in validations),
    }


def _pagination_config(profile: ApiEndpointProfile) -> dict[str, Any]:
    values = profile.pagination
    for name in ("page", "offset", "skip"):
        item = values.get(name)
        if isinstance(item, dict) and item.get("location") == "query":
            try:
                start = int(item.get("sample", 0 if name != "page" else 1))
            except (TypeError, ValueError):
                start = 0 if name != "page" else 1
            step = 1
            if name in {"offset", "skip"}:
                limit = values.get("limit", {})
                try:
                    step = max(1, int(limit.get("sample", 1))) if isinstance(limit, dict) else 1
                except (TypeError, ValueError):
                    step = 1
            return {
                "type": "page", "parameter": name, "location": "query",
                "start": start, "end": start + step * 99, "step": step,
            }
    for name in ("next", "next_url", "nexturl"):
        item = values.get(name)
        if isinstance(item, dict) and item.get("location") == "response":
            return {"type": "next", "next_path": item.get("path", name)}
    for name in ("next_cursor", "nextcursor", "cursor"):
        item = values.get(name)
        if isinstance(item, dict) and item.get("location") == "response":
            query_name = "cursor" if "cursor" in values else name
            return {
                "type": "cursor", "next_path": item.get("path", name),
                "parameter": query_name,
            }
    return {}


def _validate_from_captures(
    profile: ApiEndpointProfile,
    responses: list[dict[str, Any]],
) -> dict[str, Any]:
    matching = [
        item
        for item in responses
        if isinstance(item, dict)
        and "json" in item
        and str(item.get("method", "GET")).upper() == profile.method
        and normalize_endpoint(str(item.get("url", ""))) == profile.endpoint
    ]
    item_counts: list[int] = []
    field_sets: list[set[str]] = []
    for item in matching:
        _path, values = _best_item_array(item["json"])
        item_counts.append(len(values))
        sample = values[0] if values else item["json"]
        field_sets.append(set(sample) if isinstance(sample, dict) else set())
    common_fields = set.intersection(*field_sets) if field_sets else set()
    status = "validated" if len(matching) >= 2 and (not profile.suggested_fields or common_fields) else "sample_only"
    return {
        "status": status,
        "captured_samples": len(matching),
        "item_counts": item_counts,
        "stable_fields": sorted(common_fields),
        "needs_live_sample_run": status != "validated",
    }
