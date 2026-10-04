from __future__ import annotations

import hashlib
import io
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
import yaml
from reportlab.pdfgen.canvas import Canvas

from omnicrawler.core.config import load_config
from omnicrawler.document_ir import parse_document
from omnicrawler.pipeline import Pipeline
from omnicrawler.review.run_compare import compare_runs
from omnicrawler.services.archive_analysis import execute
from omnicrawler.templates.template_catalog import bundled_template_catalog


@pytest.fixture
def scene_server():
    product = {"sku": "sku-1", "title": "Sample", "price": "12.50", "availability": "in_stock", "noise": "a"}
    stream = io.BytesIO()
    canvas = Canvas(stream)
    canvas.drawString(50, 760, "Verified sample document. DOI: 10.1234/sample.1")
    canvas.save()
    pdf = stream.getvalue()
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            path = self.path.split("?", 1)[0]
            if path == "/robots.txt":
                body, kind = b"User-agent: *\nAllow: /\n", "text/plain"
            elif path == "/products":
                body, kind = json.dumps({"items": [product]}).encode(), "application/json"
            elif path in {"/announcements", "/papers"}:
                prefix = "notice" if path == "/announcements" else "paper"
                body = f"<a href='/{prefix}/1'>One</a><a href='/{prefix}/2'>Two</a>".encode()
                kind = "text/html"
            elif path.startswith(("/notice/", "/paper/")):
                index = path.rsplit("/", 1)[-1]
                identity = "notice-" + index if path.startswith("/notice/") else "10.1234/sample." + index
                body = (f"<article class='record'><h1 data-id='{identity}'>Document {index}</h1>"
                        f"<p class='body'>Evidence document {index}.</p><a class='pdf' href='/document-{index}.pdf'>PDF</a></article>").encode()
                kind = "text/html"
            elif path.startswith("/document-") and path.endswith(".pdf"):
                body, kind = pdf, "application/pdf"
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        def log_message(self, *_args):
            pass
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", product, pdf
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def materialize(tmp_path, name, url):
    raw = bundled_template_catalog().render("scenarios/" + name, {"seed_url": url})
    raw["project"]["workspace"] = str(tmp_path / "work")
    # Local fixture-only exception; distributed templates retain safe defaults.
    raw["http"].update(allow_private_network=True, delay_seconds=0)
    raw["egress"] = {"allowed_domains": ["127.0.0.1"], "maximum_requests": 50}
    path = tmp_path / "task.yaml"
    path.write_text(yaml.safe_dump(raw, allow_unicode=True), encoding="utf-8")
    return load_config(path)


@pytest.mark.parametrize(("name", "route", "field", "expected"), [
    ("announcement-evidence", "/announcements", "notice_id", ["notice-1", "notice-2"]),
    ("paper-evidence", "/papers", "doi", ["10.1234/sample.1", "10.1234/sample.2"]),
])
def test_list_details_pdf_and_archive_report_match_truth(tmp_path, scene_server, name, route, field, expected):
    base, _product, pdf = scene_server
    config = materialize(tmp_path, name, base + route)
    with Pipeline(config) as pipeline:
        summary = pipeline.run()
        records = [json.loads(row["data_json"]) for row in pipeline.state.rows("SELECT data_json FROM records WHERE run_id=?", (summary["run_id"],))]
    assert summary["status"] == "succeeded"
    assert sorted(record[field] for record in records) == expected
    assert summary["pdf_delivery"]["documents"] == 2
    entries = [json.loads(line) for line in Path(summary["pdf_delivery"]["path"]).read_text(encoding="utf-8").splitlines()]
    sources = []
    for index, entry in enumerate(entries):
        target = Path(entry["file_path"])
        assert target.read_bytes() == pdf
        assert entry["sha256"] == hashlib.sha256(pdf).hexdigest()
        assert entry["parent_url"].startswith(base + ("/notice/" if field == "notice_id" else "/paper/"))
        assert parse_document(target).paragraph_locators[0]["page"] == 1
        sources.append({"id": str(index), "path": str(target.relative_to(tmp_path)), "sha256": entry["sha256"], "source_url": entry["source_url"]})
    manifest = tmp_path / "analysis-manifest.json"
    manifest.write_text(json.dumps({"format": 1, "sources": sources}), encoding="utf-8")
    report = execute(manifest, tmp_path / "analysis")
    assert report["status"] == "completed_local"
    payload = json.loads(Path(report["report"]).read_text(encoding="utf-8"))
    assert len(payload["documents"]) == 2 and payload["evidence"]
    assert all(item["locator"]["page"] == 1 for item in payload["evidence"])
    (tmp_path / "scene-result.json").write_text(json.dumps({"scene": name, "truth": expected, "records": records, "delivery": entries, "report": report}, ensure_ascii=False, indent=2), encoding="utf-8")


def test_product_scene_distinguishes_price_availability_and_noise(tmp_path, scene_server):
    base, product, _pdf = scene_server
    config = materialize(tmp_path, "product-changes", base + "/products")
    with Pipeline(config) as pipeline:
        before = pipeline.run()["run_id"]
        product.update(price="15.00", availability="sold_out", noise="b")
        changed = pipeline.run()["run_id"]
        change = compare_runs(pipeline.state, before, changed)
        assert (change["modified"], change["added"], change["removed"]) == (1, 0, 0)
        assert change["notification_summary"]["changed_fields"] == ["availability", "price"]
        product["noise"] = "c"
        after = pipeline.run()["run_id"]
        noise = compare_runs(pipeline.state, changed, after)
        assert noise["notification_summary"]["total"] == 0
    (tmp_path / "scene-result.json").write_text(json.dumps({"scene": "product-changes", "change": change, "noise": noise}, ensure_ascii=False, indent=2), encoding="utf-8")
