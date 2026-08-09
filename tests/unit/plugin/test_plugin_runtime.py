import json
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import yaml

from omnicrawl.core.config import load_config
from omnicrawl.pipeline import Pipeline
from omnicrawl.plugins.plugins import Registry
from omnicrawl.runtime.resources import ResourceGuard, ResourceLimitError


class _StructuredHandler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        if self.headers.get("X-Plugin-Auth") != "ready":
            self.send_error(401)
            return
        body = b"""<!doctype html><html><head>
<title>Fallback title</title>
<meta property="og:description" content="OpenGraph description">
<script type="application/ld+json">{"@type":"Article","headline":"Structured headline"}</script>
</head><body><h1>Visible</h1></body></html>"""
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        return


class PluginRuntimeIntegrationTest(unittest.TestCase):
    def test_pipeline_wires_auth_transformer_exporter_hooks_and_structured_fields(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), _StructuredHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                workspace = root / "work"
                plugin = root / "plugin.py"
                plugin.write_text(
                    """
PLUGIN_METADATA = {
    'name': 'runtime-test', 'version': '1.0.0',
    'plugin_types': ['auth_provider', 'transformer', 'exporter', 'hook'],
    'permissions': ['filesystem_write'],
}
class HeaderAuth:
    def __init__(self, config, options): self.value = options.get('value', 'ready')
    def prepare(self, request):
        request.headers = {**request.headers, 'X-Plugin-Auth': self.value}
        return request
class Enrich:
    def __init__(self, config): pass
    def transform(self, record):
        record.data['transformed'] = True
        return record
def audit_export(config, state, run_id, options):
    path = config.workspace / 'output' / options.get('filename', 'plugin-export.txt')
    path.write_text(run_id, encoding='utf-8')
    return {'path': str(path)}
def record_event(event, **context):
    path = context['pipeline'].workspace / 'hook-events.txt'
    with path.open('a', encoding='utf-8') as handle: handle.write(event + '\\n')
def register(registry):
    registry.register_auth_provider('header_auth', HeaderAuth)
    registry.register_transformer('enrich', Enrich)
    registry.register_exporter('audit', audit_export)
    for event in ('before_run','before_fetch','after_fetch','after_extract','before_export','after_export','after_run'):
        registry.register_hook(event, lambda event=event, **context: record_event(event, **context))
""",
                    encoding="utf-8",
                )
                config_path = root / "project.yaml"
                config_path.write_text(
                    yaml.safe_dump(
                        {
                            "project": {"name": "plugins", "workspace": str(workspace)},
                            "source": {
                                "kind": "static_html",
                                "seeds": [f"http://127.0.0.1:{server.server_port}/"],
                            },
                            "crawl": {"max_pages": 1, "concurrency": 1},
                            "http": {
                                "respect_robots": False,
                                "allow_private_network": True,
                                "delay_seconds": 0,
                                "user_agent": "PluginTest/1.0 (+contact: test@example.org)",
                            },
                            "auth": {"provider": "header_auth", "options": {"value": "ready"}},
                            "transformers": [{"name": "enrich"}],
                            "extract": {
                                "mode": "html",
                                "fields": {
                                    "headline": {"source": "jsonld", "path": "headline"},
                                    "description": {"source": "opengraph", "property": "description"},
                                },
                            },
                            "outputs": {
                                "jsonl": False,
                                "csv": False,
                                "xlsx": False,
                                "plugin_exporters": ["audit"],
                                "exporter_options": {"audit": {"filename": "audit.txt"}},
                            },
                            "plugins": {
                                "paths": [str(plugin)],
                                "approved_permissions": ["filesystem_write"],
                                "signature_policy": "developer",
                            },
                        },
                        sort_keys=False,
                    ),
                    encoding="utf-8",
                )

                with Pipeline(load_config(config_path)) as pipeline:
                    summary = pipeline.run()
                    rows = pipeline.state.rows("SELECT data_json FROM records")

                data = json.loads(rows[0]["data_json"])
                self.assertEqual(data["headline"], "Structured headline")
                self.assertEqual(data["description"], "OpenGraph description")
                self.assertTrue(data["transformed"])
                self.assertTrue(Path(summary["export"]["plugin_exporters"]["audit"]["path"]).is_file())
                events = (workspace / "hook-events.txt").read_text(encoding="utf-8").splitlines()
                self.assertEqual(
                    events,
                    [
                        "before_run", "before_fetch", "after_fetch", "after_extract",
                        "before_export", "after_export", "after_run",
                    ],
                )
        finally:
            server.shutdown()
            server.server_close()

    def test_hook_failure_can_be_isolated(self):
        registry = Registry()
        registry.register_hook("event", lambda **context: 1 / 0)
        self.assertEqual(registry.emit("event", fail_open=True), [])
        self.assertEqual(registry.plugin_errors[0]["path"], "hook:event")

    def test_resource_guard_runtime_and_disk_reserve(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            config_path = root / "project.yaml"
            config_path.write_text(
                "project: {name: resources, workspace: work}\n"
                "source: {kind: static_html, seeds: [https://example.com/]}\n"
                "resources: {maximum_runtime_seconds: 1, minimum_free_disk_bytes: 0}\n",
                encoding="utf-8",
            )
            guard = ResourceGuard(load_config(config_path))
            guard.started -= 2
            with self.assertRaises(ResourceLimitError):
                guard.check(force=True)


if __name__ == "__main__":
    unittest.main()
