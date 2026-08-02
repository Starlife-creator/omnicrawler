"""Deterministic local fixtures shared by OmniCrawler E2E scenarios."""

from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import yaml


class _LocalHandler(BaseHTTPRequestHandler):
    pdf_bytes = b""

    def do_GET(self) -> None:  # noqa: N802
        pages = {
            "/index": (
                b"<!doctype html><html><head><title>E2E notice</title></head>"
                b"<body><h1>Guarantee notice</h1><a href='/notice.pdf'>PDF</a></body></html>",
                "text/html; charset=utf-8",
            ),
            "/notice.pdf": (self.pdf_bytes, "application/pdf"),
            "/api/items": (b'{"data":{"name":"Captured E2E API value"}}', "application/json"),
            "/dynamic": (
                b"""<!doctype html><html><body><div id="root">Loading</div>
<script>
fetch('/api/items').then(response => response.json()).then(data => {
  const root = document.querySelector('#root');
  root.textContent = data.data.name;
  root.setAttribute('data-ready', 'yes');
});
</script></body></html>""",
                "text/html; charset=utf-8",
            ),
        }
        item = pages.get(self.path)
        if item is None:
            self.send_error(404)
            return
        body, content_type = item
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format: str, *_args: object) -> None:
        return


def create_pdf_bytes() -> bytes:
    """Create the tiny PDF fixture in memory; no download or cloud service is used."""
    import fitz

    with fitz.open() as document:
        page = document.new_page()
        page.insert_text(
            (72, 72),
            "Security code: 000001\nGuarantee amount: 150000000 yuan",
            fontsize=12,
        )
        return document.tobytes()


@contextmanager
def local_server(pdf_bytes: bytes = b"") -> Iterator[str]:
    """Serve all E2E pages on localhost and always release the selected port."""
    _LocalHandler.pdf_bytes = pdf_bytes
    server = ThreadingHTTPServer(("127.0.0.1", 0), _LocalHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def write_pdf_fields(path: Path) -> Path:
    """Write a minimal deterministic PDF field schema used by the pipeline scenario."""
    path.write_text(
        yaml.safe_dump(
            {
                "project_name": "e2e-fields",
                "parser": {"workers": 1, "min_native_chars": 10, "max_garbled_ratio": 0.1},
                "ocr": {"backend": "none", "dpi": 220},
                "retrieval": {"top_pages": 2, "neighbor_pages": 0, "min_score": 1, "fallback_pages": [1]},
                "llm": {"provider": "disabled"},
                "extraction": {"workers": 1, "max_chars_per_page": 10000},
                "normalization": {},
                "validation": {"auto_accept_confidence": 0.99},
                "fields": [
                    {
                        "name": "stock_code", "label": "证券代码", "source": "both", "required": True,
                        "aliases": ["Security code"],
                        "patterns": [r"Security code\s*:\s*(?P<value>\d{6})"],
                    },
                    {
                        "name": "amount", "label": "担保金额", "type": "amount", "source": "content",
                        "target_unit": "元", "required": True, "aliases": ["Guarantee amount"],
                        "patterns": [r"Guarantee amount\s*:\s*(?P<value>[\d.]+)"],
                    },
                ],
            },
            allow_unicode=True,
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    return path


def write_pipeline_config(root: Path, base_url: str) -> Path:
    """Create the local HTML-to-PDF pipeline configuration."""
    fields = write_pdf_fields(root / "fields.yaml")
    config_path = root / "project.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "project": {"name": "e2e", "workspace": str(root / "workspace")},
                "source": {"kind": "incremental", "seeds": [f"{base_url}/index"]},
                "crawl": {"max_pages": 5, "max_depth": 2, "same_host": True, "concurrency": 2},
                "http": {
                    "user_agent": "OmniCrawler-E2E/1.0 (+test@example.invalid)",
                    "respect_robots": False, "delay_seconds": 0, "allow_private_network": True,
                },
                "download": {"enabled": True, "extensions": [".pdf"]},
                "extract": {"mode": "html", "fields": {"title": {"selector": "title"}}},
                "processors": {"pdf": {"enabled": True, "config": str(fields), "skip_ocr": True}},
            },
            allow_unicode=True,
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    return config_path


def write_browser_config(root: Path, base_url: str) -> Path:
    """Create the local-only browser configuration used by the optional extension."""
    config_path = root / "browser.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "project": {"name": "browser-e2e", "workspace": str(root / "browser-workspace")},
                "source": {"kind": "browser", "seeds": [f"{base_url}/dynamic"]},
                "http": {"allow_private_network": True, "respect_robots": False, "delay_seconds": 0, "timeout_seconds": 15},
                "browser": {
                    "engine": "playwright", "headless": True, "pool_size": 1, "wait_until": "networkidle",
                    "actions": [{"action": "wait_for", "selector": "#root[data-ready='yes']"}],
                },
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    return config_path
