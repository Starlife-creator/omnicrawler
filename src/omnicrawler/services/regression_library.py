from __future__ import annotations

import gzip
import hashlib
import json
from pathlib import Path
from typing import Any

from ..core.config import AppConfig
from ..core.models import CrawlRequest, ExtractedRecord, FetchResult
from ..core.utils import atomic_write, redact_headers, utcnow
from ..security.paths import require_workspace_path


def _record_snapshot(records: list[ExtractedRecord]) -> list[dict[str, Any]]:
    return [{"source_url": record.source_url, "record_type": record.record_type,
             "data": record.data} for record in records]


def _canonical(value: Any) -> str:
    # JSON preserves the distinction between 0 and false, including nested fields.
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)


class RegressionLibrary:
    """Bounded offline fixtures for testing template and extractor changes without network access."""

    def __init__(self, config: AppConfig) -> None:
        settings = config.section("regression")
        self.enabled = bool(settings.get("enabled", True))
        self.maximum = max(0, int(settings.get("max_fixtures", 50)))
        self.directory = config.workspace / "regression_fixtures"

    def capture(self, result: FetchResult, *, records: int | list[ExtractedRecord], processor: str,
                replay_supported: bool = True) -> Path | None:
        if not self.enabled or self.maximum == 0 or result.request.kind == "asset":
            return None
        self.directory.mkdir(parents=True, exist_ok=True)
        manifests = sorted(self.directory.glob("*.json"), key=lambda path: path.stat().st_mtime)
        key = result.request.fingerprint[:20] + "-" + result.content_hash[:20]
        manifest_path = self.directory / f"{key}.json"
        # A later run must not silently bless a changed extractor on the same input.
        if manifest_path.exists():
            return manifest_path
        body_path = self.directory / f"{key}.body.gz"
        baseline: dict[str, Any] = {"processor": processor, "records": records if isinstance(records, int) else len(records),
                                    "scope": "processor_output", "provenance": "captured_not_human_verified",
                                    "replay_supported": replay_supported}
        if not isinstance(records, int):
            baseline["expected"] = _record_snapshot(records)
        manifest = {
            "schema_version": 2,
            "captured_at": utcnow(),
            "request": {
                "url": result.request.url,
                "method": result.request.method,
                "kind": result.request.kind,
                "render": result.request.render,
            },
            "final_url": result.final_url,
            "status": result.status,
            "headers": redact_headers(result.headers),
            "elapsed_seconds": result.elapsed_seconds,
            "body": body_path.name,
            "body_sha256": result.content_hash,
            "baseline": baseline,
        }
        encoded = _canonical(manifest).encode("utf-8")
        atomic_write(body_path, gzip.compress(result.body, compresslevel=6))
        atomic_write(manifest_path, encoded)
        manifests = [path for path in manifests if path != manifest_path]
        for old in manifests[: max(0, len(manifests) - self.maximum + 1)]:
            try:
                metadata = json.loads(old.read_text(encoding="utf-8"))
                body = require_workspace_path(self.directory / str(metadata.get("body", "")), root=self.directory, what="regression body")
                body.unlink(missing_ok=True)
                old.unlink(missing_ok=True)
            except (OSError, json.JSONDecodeError):
                continue
        return manifest_path


    def load(self) -> list[tuple[dict[str, Any], FetchResult]]:
        fixtures: list[tuple[dict[str, Any], FetchResult]] = []
        for path in sorted(self.directory.glob("*.json")):
            manifest = json.loads(path.read_text(encoding="utf-8"))
            request_data = manifest["request"]
            body_path = require_workspace_path(self.directory / manifest["body"], root=self.directory, what="regression body")
            body = gzip.decompress(body_path.read_bytes())
            if hashlib.sha256(body).hexdigest() != manifest.get("body_sha256"):
                raise ValueError(f"Regression fixture hash mismatch: {path.name}")
            request = CrawlRequest(
                str(request_data["url"]),
                method=str(request_data.get("method", "GET")),
                kind=str(request_data.get("kind", "page")),
                render=bool(request_data.get("render", False)),
            )
            fixtures.append(
                (
                    manifest,
                    FetchResult(
                        request,
                        str(manifest["final_url"]),
                        int(manifest["status"]),
                        {str(key): str(value) for key, value in manifest.get("headers", {}).items()},
                        body,
                        float(manifest.get("elapsed_seconds", 0)),
                    ),
                )
            )
        return fixtures


def verify_regression_fixtures(config: AppConfig) -> dict[str, Any]:
    from ..extraction import extractors
    from ..pipeline import build_registry

    library = RegressionLibrary(config)
    registry = build_registry(config)
    results: list[dict[str, Any]] = []
    try:
        fixtures = library.load()
    except (OSError, ValueError, KeyError, TypeError, EOFError) as exc:
        return {"ok": False, "fixtures": 0, "passed": 0, "status": "invalid_fixture",
                "error_type": type(exc).__name__, "results": []}
    for manifest, result in fixtures:
        baseline = manifest.get("baseline", {})
        extraction = config.section("extract")
        if ("expected" not in baseline or not baseline.get("replay_supported", False)
                or extraction.get("parser") or extraction.get("extractor")
                or config.section("source").get("seed_template_overrides")):
            results.append({"url": result.final_url, "ok": False, "status": "unverified",
                            "reason": "legacy_count_only_or_unsupported_processor_scope"})
            continue
        mode = str(extraction.get("mode", "auto")).lower()
        processor_name = mode if mode in registry.processors else extractors.choose_processor(result)
        factory = registry.processors.get(processor_name)
        if factory is None:
            results.append({"url": result.final_url, "ok": False, "error": f"missing processor {processor_name}"})
            continue
        try:
            records = factory(config).process(result).records
            matches = _canonical(_record_snapshot(records)) == _canonical(baseline["expected"])
        except Exception as exc:  # A failing processor must not hide the other fixture results.
            results.append({"url": result.final_url, "ok": False, "status": "failed",
                            "error_type": type(exc).__name__})
            continue
        expected = int(baseline.get("records", 0))
        results.append(
            {
                "url": result.final_url,
                "ok": len(records) == expected and matches,
                "status": "consistent" if len(records) == expected and matches else "changed",
                "scope": "processor_output",
                "baseline_provenance": "captured_not_human_verified",
                "expected_records": expected,
                "actual_records": len(records),
                "processor": processor_name,
            }
        )
    return {
        "ok": bool(results) and all(item["ok"] for item in results),
        "status": "checked" if results else "unverified",
        "fixtures": len(results),
        "passed": sum(item["ok"] for item in results),
        "results": results,
    }
