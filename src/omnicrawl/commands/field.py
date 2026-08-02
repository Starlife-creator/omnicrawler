"""字段建议和操作录制命令。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml


def execute_field_suggest(html_path: str, output: str = "", limit: int = 20) -> Any:
    from ..extraction.field_designer import analyze_html

    candidates = [
        item.to_dict()
        for item in analyze_html(
            Path(html_path).read_text(encoding="utf-8", errors="replace"),
            limit=limit,
        )
    ]
    if output:
        target = Path(output).expanduser().resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            yaml.safe_dump({"fields": candidates}, allow_unicode=True), encoding="utf-8"
        )
        return {"created": str(target), "fields": len(candidates)}
    return candidates


def execute_record_actions(url: str, output: str, timeout: float = 120.0) -> Any:
    from ..fetching.action_recorder import record_with_playwright
    return record_with_playwright(url, Path(output).expanduser().resolve(), timeout_seconds=int(timeout))


def execute_api_discover(input_path: str, output: str) -> Any:
    from ..extraction.api_discovery import write_discovery_bundle

    payload = json.loads(Path(input_path).read_text(encoding="utf-8"))
    responses = payload.get("api_responses", payload) if isinstance(payload, dict) else payload
    if not isinstance(responses, list):
        raise ValueError("API capture input must be a JSON list")
    return write_discovery_bundle(responses, Path(output).expanduser().resolve())
