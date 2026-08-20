from __future__ import annotations

import importlib.util
import json
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import yaml

from omnicrawler.apps.field_extractor import build_parser as extractor_parser
from omnicrawler.apps.pdf_processor import build_parser as processor_parser
from omnicrawler.core.config import load_config
from omnicrawler.pdfx.config import load_config as load_pdf_config
from omnicrawler.pdfx.database import Database
from omnicrawler.pdfx.project import create_project_config, validate_project_template
from omnicrawler.pdfx.safe_regex import search, validate_pattern
from omnicrawler.pipeline import Pipeline


class _PipelineHandler(BaseHTTPRequestHandler):
    pdf_bytes = b""

    def do_GET(self):  # noqa: N802
        if self.path == "/index":
            body = b"<html><title>Notice</title><h1>Guarantee notice</h1><a href='/notice.pdf'>PDF</a></html>"
            content_type = "text/html; charset=utf-8"
        elif self.path == "/notice.pdf":
            body = self.pdf_bytes
            content_type = "application/pdf"
        else:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format, *_args):
        return


@unittest.skipUnless(importlib.util.find_spec("pdfplumber") and importlib.util.find_spec("reportlab") and importlib.util.find_spec("openpyxl"), "PDF依赖未安装")
class UnifiedPipelineTest(unittest.TestCase):
    def test_crawl_to_pdf_provenance_extraction_and_resume(self) -> None:
        import io

        from reportlab.lib.pagesizes import A4
        from reportlab.pdfgen import canvas

        # Phase 0：fitz → reportlab（内存生成 PDF fixture）
        _buffer = io.BytesIO()
        _c = canvas.Canvas(_buffer, pagesize=A4)
        _c.setFont("Helvetica", 12)
        _c.drawString(72, A4[1] - 72, "Security code: 000001")
        _c.drawString(72, A4[1] - 90, "Guarantee amount: 150000000 yuan")
        _c.showPage()
        _c.save()
        _PipelineHandler.pdf_bytes = _buffer.getvalue()

        server = ThreadingHTTPServer(("127.0.0.1", 0), _PipelineHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                workspace = root / "workspace"
                pdf_template = root / "fields.yaml"
                pdf_template.write_text(
                    yaml.safe_dump(
                        {
                            "project_name": "template",
                            "parser": {"workers": 1, "min_native_chars": 10, "max_garbled_ratio": 0.1},
                            "ocr": {"backend": "none", "dpi": 220},
                            "retrieval": {"top_pages": 2, "neighbor_pages": 0, "min_score": 1, "fallback_pages": [1]},
                            "llm": {"provider": "disabled"},
                            "extraction": {"workers": 1, "max_chars_per_page": 10000},
                            "normalization": {},
                            "validation": {"auto_accept_confidence": 0.99},
                            "fields": [
                                {
                                    "name": "stock_code",
                                    "label": "证券代码",
                                    "source": "both",
                                    "required": True,
                                    "aliases": ["Security code"],
                                    "patterns": [r"Security code\s*:\s*(?P<value>\d{6})"],
                                },
                                {
                                    "name": "amount",
                                    "label": "担保金额",
                                    "type": "amount",
                                    "source": "content",
                                    "target_unit": "元",
                                    "required": True,
                                    "aliases": ["Guarantee amount"],
                                    "patterns": [r"Guarantee amount\s*:\s*(?P<value>[\d.]+)"],
                                },
                            ],
                        },
                        allow_unicode=True,
                        sort_keys=False,
                    ),
                    encoding="utf-8",
                )
                crawl_config = root / "project.yaml"
                pdf_url = f"http://127.0.0.1:{server.server_port}/notice.pdf"
                crawl_config.write_text(
                    yaml.safe_dump(
                        {
                            "project": {"name": "unified", "workspace": str(workspace)},
                            "source": {"kind": "incremental", "seeds": [f"http://127.0.0.1:{server.server_port}/index"]},
                            "crawl": {"max_pages": 5, "max_depth": 2, "same_host": True, "concurrency": 2},
                            "http": {
                                "user_agent": "UnifiedTest/2.0 (+contact: test@example.org)",
                                "respect_robots": False,
                                "delay_seconds": 0,
                                "allow_private_network": True,
                            },
                            "download": {"enabled": True, "extensions": [".pdf"]},
                            "extract": {"mode": "html", "fields": {"title": {"selector": "title"}}},
                            "processors": {
                                "pdf": {
                                    "enabled": True,
                                    "config": str(pdf_template),
                                    "skip_ocr": True,
                                }
                            },
                        },
                        allow_unicode=True,
                        sort_keys=False,
                    ),
                    encoding="utf-8",
                )

                config = load_config(crawl_config)
                stages: list[str] = []
                with Pipeline(config) as pipeline:
                    result = pipeline.run(callback=lambda stage, _result: stages.append(stage))
                self.assertEqual(result["status"], "succeeded")
                self.assertEqual(result["processed"], 2)
                self.assertIn("pdf_ingest", stages)
                self.assertEqual(result["pdf"]["result"]["extract"]["records"], 1)

                manifest = workspace / "artifacts" / "pdf" / "source_manifest.jsonl"
                manifest_row = json.loads(manifest.read_text(encoding="utf-8").splitlines()[0])
                self.assertEqual(manifest_row["source_url"], pdf_url)
                self.assertEqual(manifest_row["crawl_run_id"], result["run_id"])
                self.assertTrue(manifest_row["parent_url"].endswith("/index"))

                pdf_project = workspace / "pdf" / "project.yaml"
                pdf_config = load_pdf_config(pdf_project)
                with Database(pdf_config.database) as database:
                    provenance = database.fetchone(
                        "SELECT source_url, source_meta_json FROM document_sources"
                    )
                    value = database.fetchone(
                        "SELECT normalized_value FROM field_values WHERE field_name='amount'"
                    )
                self.assertEqual(provenance["source_url"], pdf_url)
                self.assertIn(result["run_id"], provenance["source_meta_json"])
                self.assertEqual(value["normalized_value"], "150000000")

                for relative in (
                    "output/pipeline_summary.json",
                    "output/pdf/pages.jsonl",
                    "output/pdf/text_manifest.csv",
                    "output/pdf/extraction_results.xlsx",
                    "output/pdf/review_queue.csv",
                ):
                    self.assertTrue((workspace / relative).is_file(), relative)

                with Pipeline(config) as pipeline:
                    resumed = pipeline.run()
                self.assertEqual(
                    resumed["pdf"]["result"]["processing"]["ingest"]["duplicate"], 1
                )
                self.assertEqual(resumed["pdf"]["result"]["extract"]["selected"], 0)
        finally:
            server.shutdown()
            server.server_close()


class ModuleContractTest(unittest.TestCase):
    def test_safe_regex_project_wizard_and_independent_clis(self) -> None:
        with self.assertRaises(ValueError):
            validate_pattern(r"(a+)+$")
        self.assertIsNotNone(search(r"amount:\s*(\d+)", "amount: 42"))
        self.assertIn("export-text", processor_parser().format_help())
        self.assertIn("apply-review", extractor_parser().format_help())

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            template = root / "template.yaml"
            template.write_text(
                yaml.safe_dump(
                    {
                        "ocr": {"backend": "none", "dpi": 220},
                        "llm": {"provider": "disabled"},
                        "fields": [
                            {
                                "name": "name",
                                "label": "名称",
                                "patterns": [r"Name:\s*(?P<value>[^\n]+)"],
                            }
                        ],
                    },
                    allow_unicode=True,
                ),
                encoding="utf-8",
            )
            project = create_project_config(
                template,
                root / "project.yaml",
                project_name="wizard",
                input_dir=root / "input",
                work_dir=root / "work",
                output_dir=root / "output",
            )
            validation = validate_project_template(project)
            self.assertTrue(validation["valid"], validation)


if __name__ == "__main__":
    unittest.main()
