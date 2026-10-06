"""Run explicit offline template checks through built-in processors."""
from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .._version import __version__
from ..core.config import AppConfig
from ..core.models import CrawlRequest, FetchResult
from ..templates.evidence import template_binding
from ..templates.verification import VerificationStore, digest, parameters_for


def _read_bounded(path: Path) -> bytes:
    with path.open("rb") as stream:
        value = stream.read(2 * 1024**2 + 1)
    if len(value) > 2 * 1024**2:
        raise ValueError("复验输入文件超过2MiB")
    return value


def verify_fixture(catalog: Any, template_id: str, url: str, fixture: Path, expected: Path,
                   *, values: dict[str, Any] | None = None, store: VerificationStore | None = None) -> dict[str, Any]:
    record = catalog.get(template_id)
    if record is None:
        raise KeyError(template_id)
    parsed_url = urlsplit(url)
    if parsed_url.scheme not in {"http", "https"} or not parsed_url.hostname or parsed_url.username or parsed_url.password:
        raise ValueError("固定样本复验需要不含明文凭据的HTTP(S)来源URL；不会访问网络")
    payload = _read_bounded(fixture)
    truth = json.loads(_read_bounded(expected).decode("utf8"))
    if not isinstance(truth, list) or not truth or len(truth) > 10000 or any(not isinstance(item, dict) for item in truth):
        raise ValueError("固定样本真值必须包含1至10000条原始提取记录")
    parameters = parameters_for(record, url, values)
    raw = catalog.render(record, parameters)
    extraction = raw.get("extract", {})
    if extraction.get("processor") or extraction.get("parser") or extraction.get("extractor") or raw.get("transformers"):
        raise ValueError("固定样本复验只支持内置HTML/JSON处理器，扩展任务需独立验收")
    mode = extraction.get("mode", "html")
    if mode not in {"html", "json"}:
        raise ValueError("固定样本复验只支持HTML/JSON")
    from ..extraction.extractors import HTMLProcessor, JSONProcessor
    config = AppConfig(fixture, fixture.parent, raw, fixture.parent)
    processor = JSONProcessor(config) if mode == "json" else HTMLProcessor(config)
    binding = template_binding(record, parameters)
    report = {**binding, "checked_at": datetime.now(UTC).isoformat(), "verification": "unverified", "verification_kind": "fixture",
              "url_sha256": hashlib.sha256(url.encode()).hexdigest(), "input_sha256": hashlib.sha256(payload).hexdigest(),
              "expected_sha256": digest(truth), "core_version": __version__, "boundary": "builtin_processor_output",
              "network_accessed": False, "status": "error", "diagnostic": "", "valid_for_days": 90}
    try:
        result = processor.process(FetchResult(CrawlRequest(url), url, 200,
                                   {"content-type": "application/json" if mode == "json" else "text/html; charset=utf-8"}, payload, 0))
        actual = [item.data for item in result.records]
        report.update(actual_sha256=digest(actual), expected_records=len(truth), actual_records=len(actual))
        report["status"] = "passed" if digest(actual) == digest(truth) else "failed"
        report["verification"] = "fixture_verified" if report["status"] == "passed" else "unverified"
        report["diagnostic"] = "" if report["status"] == "passed" else "processor_output_mismatch"
    except Exception as exc:
        report["diagnostic"] = type(exc).__name__
    (store or VerificationStore()).record(report)
    return {"ok": report["status"] == "passed", **report,
            "next_action": "review_fields_or_try_generic_template" if report["status"] != "passed" else "retain_as_historical_fixture_evidence"}
