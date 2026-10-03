from __future__ import annotations

import hashlib
import json
import threading
from collections import Counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
import yaml

from omnicrawler.core.config import load_config
from omnicrawler.core.models import CrawlRequest, FetchResult
from omnicrawler.pipeline import Pipeline
from omnicrawler.pipeline_ops.provenance import write_pdf_source_manifest
from omnicrawler.runtime.recovery import RecoveryCenter
from omnicrawler.state import StateStore


def test_selected_failures_protect_unrelated_done_blocked_and_login(tmp_path, capsys):
    from omnicrawler.cli import main

    config_path = tmp_path / "task.yaml"
    config_path.write_text("project: {name: selected, workspace: work}\nsource: {kind: static_html, seeds: [https://example.test/]}\n", encoding="utf-8")
    config = load_config(config_path)
    requests = [CrawlRequest("https://example.test/" + name, kind="asset") for name in ("selected", "other", "done", "blocked", "login")]
    requests[-1].meta["_auth_failure"] = {"code": "session_expired", "scope": "a" * 64}
    with StateStore(config.workspace / "state.sqlite3") as state:
        for request, status in zip(requests, ("failed", "failed", "done", "blocked", "failed"), strict=True):
            state.enqueue(request)
            state.mark_done(request.fingerprint, status=status, error="test")
        state.conn.execute("UPDATE frontier SET attempts=3")
        state.conn.commit()
    main(["recovery", "failures", "-c", str(config_path), "--limit", "1"])
    preview = json.loads(capsys.readouterr().out)
    assert preview["truncated"] and preview["failures"][0]["fingerprint"] == requests[0].fingerprint
    main(["recovery", "retry-failed", "-c", str(config_path), "--fingerprint", requests[0].fingerprint,
          "--fingerprint", requests[0].fingerprint, "--fingerprint", requests[2].fingerprint,
          "--fingerprint", requests[3].fingerprint, "--fingerprint", requests[4].fingerprint])
    assert json.loads(capsys.readouterr().out)["retried"] == 1
    with StateStore(config.workspace / "state.sqlite3") as state:
        rows = state.rows("SELECT status, attempts FROM frontier ORDER BY id")
        assert [row["status"] for row in rows] == ["pending", "failed", "done", "blocked", "failed"]
        assert [row["attempts"] for row in rows] == [0, 3, 3, 3, 3]
        with pytest.raises(ValueError):
            state.retry_failed_selected([])
        with pytest.raises(ValueError):
            state.retry_failed_selected([requests[1].fingerprint, "invalid"])


def test_host_pdf_download_selection_resume_manifest_and_repeat(tmp_path):
    calls = Counter()
    available = {"/good.pdf"}
    payload = b"%PDF-1.4\n% offline deterministic delivery fixture\n%%EOF"

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            calls[self.path] += 1
            if self.path == "/robots.txt":
                body, kind = b"User-agent: *\nAllow: /\n", "text/plain"
            elif self.path == "/index":
                body = b"<title>Selected open papers</title><a href='/good.pdf'>Good</a><a href='/retry.pdf'>Retry</a><a href='/other.pdf'>Other</a>"
                kind = "text/html"
            elif self.path in available:
                body, kind = payload, "application/pdf"
            else:
                self.send_error(503)
                return
            self.send_response(200)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = f"http://127.0.0.1:{server.server_port}"
        config_path = tmp_path / "task.yaml"
        config_path.write_text(yaml.safe_dump({"project": {"name": "papers", "workspace": str(tmp_path / "work")},
            "source": {"kind": "static_html", "seeds": [base + "/index"]},
            "crawl": {"max_pages": 10, "max_depth": 1, "concurrency": 1, "same_host": True},
            "http": {"allow_private_network": True, "respect_robots": True, "delay_seconds": 0, "retries": 0},
            "egress": {"allowed_domains": ["127.0.0.1"], "maximum_requests": 30},
            "download": {"enabled": True, "extensions": [".pdf"], "verified_pdf_manifest": True},
            "extract": {"mode": "html", "fields": {"title": {"selector": "title"}}},
            "outputs": {"jsonl": True, "csv": True, "xlsx": False}}), encoding="utf-8")
        config = load_config(config_path)
        with Pipeline(config) as pipeline:
            first = pipeline.run()
            assert first["pdf_delivery"]["documents"] == 1
            failures = pipeline.state.rows("SELECT fingerprint, url FROM frontier WHERE status='failed' ORDER BY id")
            assert len(failures) == 2
            manifest = write_pdf_source_manifest(config.workspace, pipeline.state, run_id=first["run_id"])
        first_entry = json.loads(Path(manifest["path"]).read_text(encoding="utf-8").splitlines()[0])
        assert first_entry["sha256"] == hashlib.sha256(payload).hexdigest()
        assert first_entry["parent_url"] == base + "/index"
        original = Path(first_entry["file_path"]).read_bytes()
        selected = next(item["fingerprint"] for item in failures if item["url"].endswith("/retry.pdf"))
        available.add("/retry.pdf")
        assert RecoveryCenter(config).retry_failed(fingerprints=[selected])["retried"] == 1
        before = dict(calls)
        with Pipeline(config) as pipeline:
            second = pipeline.run(resume=True)
            assert second["pdf_delivery"]["documents"] == 2
            manifest = write_pdf_source_manifest(config.workspace, pipeline.state, run_id=second["run_id"])
        assert calls["/good.pdf"] == before["/good.pdf"] and calls["/other.pdf"] == before["/other.pdf"]
        assert calls["/retry.pdf"] == before["/retry.pdf"] + 1
        assert Path(first_entry["file_path"]).read_bytes() == original
        items = [json.loads(line) for line in Path(manifest["path"]).read_text(encoding="utf-8").splitlines()]
        assert len(items) == 2 and all(item["verification"] == "verified_file" for item in items)
        with Pipeline(config) as pipeline:
            pipeline.run(resume=True)
        assert calls["/retry.pdf"] == before["/retry.pdf"] + 1
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_corrupted_pdf_cannot_receive_verified_manifest(tmp_path):
    workspace = tmp_path / "work"
    path = workspace / "artifacts/pdf/paper.pdf"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"original PDF")
    request = CrawlRequest("https://example.test/paper.pdf", kind="asset")
    with StateStore(workspace / "state.sqlite3") as state:
        run_id = state.start_run("test", "test.yaml")
        state.enqueue(request)
        state.save_artifact(run_id, FetchResult(request, request.url, 200, {"content-type": "application/pdf"}, path.read_bytes(), 0), path)
        write_pdf_source_manifest(workspace, state)
        manifest = path.parent / "source_manifest.jsonl"
        before = manifest.read_bytes()
        path.write_bytes(b"tampered PDF")
        with pytest.raises(ValueError, match="哈希"):
            write_pdf_source_manifest(workspace, state)
        assert manifest.read_bytes() == before
