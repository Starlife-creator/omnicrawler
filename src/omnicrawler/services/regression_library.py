from __future__ import annotations

import gzip
import json
from pathlib import Path
from typing import Any

from ..core.config import AppConfig
from ..core.models import CrawlRequest, FetchResult
from ..core.utils import atomic_write, redact_headers, utcnow


class RegressionLibrary:
    """Bounded offline fixtures for testing template and extractor changes without network access."""

    def __init__(self, config: AppConfig) -> None:
        settings = config.section("regression")
        self.enabled = bool(settings.get("enabled", True))
        self.maximum = max(0, int(settings.get("max_fixtures", 50)))
        self.directory = config.workspace / "regression_fixtures"

    def capture(self, result: FetchResult, *, records: int, processor: str) -> Path | None:
        if not self.enabled or self.maximum == 0 or result.request.kind == "asset":
            return None
        self.directory.mkdir(parents=True, exist_ok=True)
        manifests = sorted(self.directory.glob("*.json"), key=lambda path: path.stat().st_mtime)
        key = result.request.fingerprint[:20]
        manifest_path = self.directory / f"{key}.json"
        body_path = self.directory / f"{key}.body.gz"
        body_path.write_bytes(gzip.compress(result.body, compresslevel=6))
        manifest = {
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
            "baseline": {"processor": processor, "records": records},
        }
        atomic_write(manifest_path, json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8"))
        manifests = [path for path in manifests if path != manifest_path]
        for old in manifests[: max(0, len(manifests) - self.maximum + 1)]:
            try:
                metadata = json.loads(old.read_text(encoding="utf-8"))
                (self.directory / str(metadata.get("body", ""))).unlink(missing_ok=True)
                old.unlink(missing_ok=True)
            except (OSError, json.JSONDecodeError):
                continue
        return manifest_path


    def load(self) -> list[tuple[dict[str, Any], FetchResult]]:
        fixtures: list[tuple[dict[str, Any], FetchResult]] = []
        for path in sorted(self.directory.glob("*.json")):
            manifest = json.loads(path.read_text(encoding="utf-8"))
            request_data = manifest["request"]
            body = gzip.decompress((self.directory / manifest["body"]).read_bytes())
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
    for manifest, result in library.load():
        processor_name = extractors.choose_processor(result)
        factory = registry.processors.get(processor_name)
        if factory is None:
            results.append({"url": result.final_url, "ok": False, "error": f"missing processor {processor_name}"})
            continue
        records = factory(config).process(result).records
        expected = int(manifest.get("baseline", {}).get("records", 0))
        results.append(
            {
                "url": result.final_url,
                "ok": len(records) == expected,
                "expected_records": expected,
                "actual_records": len(records),
                "processor": processor_name,
            }
        )
    return {
        "ok": all(item["ok"] for item in results),
        "fixtures": len(results),
        "passed": sum(item["ok"] for item in results),
        "results": results,
    }
