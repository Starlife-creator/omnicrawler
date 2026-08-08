import json
import os
try:
    from datetime import UTC, datetime, timedelta  # Python 3.11+
except ImportError:
    from datetime import datetime, timedelta, timezone
    UTC = timezone.utc
from pathlib import Path
from unittest.mock import patch

from omnicrawl.core.models import CrawlRequest, FetchResult
from omnicrawl.quality.diagnostics import DiagnosticRecorder


def test_recorder_sanitizes_filename_redacts_strings_and_omits_bytes(tmp_path):
    recorder = DiagnosticRecorder(
        tmp_path,
        {"endpoint": "https://user:password@example.test/?api_key=top-secret"},
    )
    request = CrawlRequest(
        "https://example.test/items?access_token=url-secret",
        headers={"Authorization": "Bearer header-secret", "Accept": "application/json"},
        body=b"request-secret-body",
    )
    result = FetchResult(request, request.url, 500, {}, b"response-secret-body", 0.1)

    path = recorder.failure("../unsafe/run", "fetch:page", RuntimeError("token=exception-secret"), request=request, result=result)

    assert path is not None
    assert path.parent == tmp_path / "diagnostics"
    assert ".." not in path.name
    assert ":" not in path.name
    payload = json.loads(path.read_text(encoding="utf-8"))
    rendered = json.dumps(payload, ensure_ascii=False)
    for secret in ("password", "top-secret", "url-secret", "header-secret", "exception-secret", "request-secret-body", "response-secret-body"):
        assert secret not in rendered
    assert payload["request"]["headers"]["Accept"] == "application/json"
    assert payload["result"]["body"] == {"type": "bytes", "length": 20, "omitted": True}
    assert not list(path.parent.glob("*.tmp"))


def test_recorder_truncates_large_messages(tmp_path):
    path = DiagnosticRecorder(tmp_path).failure("run", "stage", RuntimeError("x" * 20_000))

    assert path is not None
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert "<truncated " in payload["error"]["message"]
    assert len(payload["error"]["message"]) < 17_000


def test_recorder_never_masks_original_failure_when_storage_is_unavailable(tmp_path):
    recorder = DiagnosticRecorder(tmp_path)
    with patch.object(Path, "mkdir", side_effect=PermissionError("read only")):
        assert recorder.failure("run", "fetch", RuntimeError("original")) is None


def test_cleanup_enforces_age_count_and_preserves_unrelated_files(tmp_path):
    recorder = DiagnosticRecorder(tmp_path, {"diagnostics": {"retention_days": 2, "max_files": 2, "max_bytes": 10_000}})
    recorder.directory.mkdir()
    old = recorder.directory / "old.json"
    old.write_text("{}", encoding="utf-8")
    old_time = datetime.now(UTC) - timedelta(days=10)
    os.utime(old, (old_time.timestamp(), old_time.timestamp()))
    unrelated = recorder.directory / "keep.txt"
    unrelated.write_text("untouched", encoding="utf-8")

    for index in range(3):
        assert recorder.failure("run", f"stage-{index}", RuntimeError("boom")) is not None

    assert not old.exists()
    assert len(list(recorder.directory.glob("*.json"))) == 2
    assert unrelated.read_text(encoding="utf-8") == "untouched"


def test_cleanup_enforces_total_size_but_keeps_new_diagnostic(tmp_path):
    recorder = DiagnosticRecorder(tmp_path, {"diagnostics": {"max_bytes": 1, "max_files": 100}})
    path = recorder.failure("run", "stage", RuntimeError("boom"))

    assert path is not None
    assert path.exists()
