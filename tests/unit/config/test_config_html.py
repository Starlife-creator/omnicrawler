import tempfile
import unittest
from pathlib import Path

from omnicrawler.core.config import load_config, resolve_pdf_template, validate_config
from omnicrawler.core.models import CrawlRequest, FetchResult
from omnicrawler.extraction.extractors import HTMLProcessor, JSONProcessor, TableProcessor
from omnicrawler.pdfx.config import load_config as load_pdf_config
from omnicrawler.pdfx.project import create_project_config


class ConfigAndExtractorTest(unittest.TestCase):
    def test_config_env_and_validation(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "project.yaml"
            path.write_text(
                """
project: {name: test, workspace: work}
source: {kind: crawl, seeds: [https://example.com/]}
crawl: {max_pages: 2, concurrency: 1}
http: {user_agent: 'Test/1.0 (+contact: test@example.org)'}
""",
                encoding="utf-8",
            )
            config = load_config(path)
            errors, warnings = validate_config(config)
            self.assertEqual(errors, [])
            self.assertEqual(warnings, [])
            self.assertEqual(config.workspace, (Path(temp) / "work").resolve())

    def test_external_config_uses_packaged_default_pdf_template(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "project.yaml"
            path.write_text(
                "project: {name: pdf, workspace: work}\n"
                "source: {kind: crawl, seeds: [https://example.com/]}\n"
                "download: {enabled: true, extensions: [.pdf]}\n"
                "processors: {pdf: {enabled: true}}\n",
                encoding="utf-8",
            )
            config = load_config(path)
            template = resolve_pdf_template(config)
            self.assertTrue(template.is_file())
            self.assertIn("omnicrawler/templates/pdf", template.as_posix())

    def test_pdf_builtin_template_materializes_entity_asset_for_project(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            project = create_project_config(
                "builtin:pdf/generic_template.yaml",
                root / "project.yaml",
                project_name="pdf",
                input_dir=root / "input",
                work_dir=root / "work",
                output_dir=root / "output",
            )
            config = load_pdf_config(project)
            entity_path = Path(config.normalization["entity_master_csv"])
            self.assertTrue(entity_path.is_file())
            self.assertNotIn("configs/pdf", str(entity_path).replace("\\", "/"))

    def test_legacy_pdf_default_remains_an_alias(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "project.yaml"
            path.write_text(
                "project: {name: pdf, workspace: work}\n"
                "source: {kind: crawl, seeds: [https://example.com/]}\n",
                encoding="utf-8",
            )
            template = resolve_pdf_template(load_config(path), "configs/pdf/generic_template.yaml")
            self.assertTrue(template.is_file())
            self.assertIn("omnicrawler/templates/pdf", template.as_posix())

    def test_html_item_extraction(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "project.yaml"
            path.write_text(
                """
project: {name: test, workspace: work}
source: {kind: static_html, seeds: [https://example.com/]}
extract:
  mode: html
  item_selector: .row
  fields:
    title: {selector: h2}
    href: {selector: 'a[href]', attr: href}
""",
                encoding="utf-8",
            )
            config = load_config(path)
            request = CrawlRequest("https://example.com/")
            result = FetchResult(
                request, request.url, 200, {"content-type": "text/html; charset=utf-8"},
                "<div class='row'><h2>甲</h2><a href='/a'>查看</a></div><div class='row'><h2>乙</h2></div>".encode(),
                0.1,
            )
            records = HTMLProcessor(config).process(result).records
            self.assertEqual([item.data["title"] for item in records], ["甲", "乙"])
            self.assertEqual(records[0].data["href"], "/a")

    def test_json_path_extraction(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "project.yaml"
            path.write_text(
                """
project: {name: test, workspace: work}
source: {kind: rest, seeds: [https://example.com/api]}
extract:
  mode: json
  item_path: data.items[*]
  fields: {name: {path: profile.name}}
""",
                encoding="utf-8",
            )
            config = load_config(path)
            request = CrawlRequest("https://example.com/api")
            result = FetchResult(request, request.url, 200, {"content-type": "application/json"}, b'{"data":{"items":[{"profile":{"name":"A"}},{"profile":{"name":"B"}}]}}', 0.1)
            records = JSONProcessor(config).process(result).records
            self.assertEqual([item.data["name"] for item in records], ["A", "B"])

    def test_table_extraction(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "project.yaml"
            path.write_text(
                "project: {name: test, workspace: work}\nsource: {kind: static_html, seeds: [https://example.com/]}\nextract: {mode: table}\n",
                encoding="utf-8",
            )
            config = load_config(path)
            request = CrawlRequest("https://example.com/")
            result = FetchResult(
                request, request.url, 200, {"content-type": "text/html"},
                "<table><tr><th>名称</th><th>金额</th></tr><tr><td>甲</td><td>100</td></tr></table>".encode(), 0.1,
            )
            records = TableProcessor(config).process(result).records
            self.assertEqual(records[0].data, {"名称": "甲", "金额": "100"})


    def test_xpath_and_browser_network_response_extraction(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "project.yaml"
            path.write_text(
                """
project: {name: test, workspace: work}
source: {kind: browser, seeds: [https://example.com/]}
extract:
  mode: html
  fields:
    heading: {xpath: '//h1/text()'}
    api_name:
      source: browser_response
      url_pattern: '/api/items'
      path: data.name
""",
                encoding="utf-8",
            )
            config = load_config(path)
            request = CrawlRequest("https://example.com/")
            result = FetchResult(
                request,
                request.url,
                200,
                {"content-type": "text/html"},
                b"<html><h1>XPath title</h1></html>",
                0.1,
                {
                    "api_responses": [
                        {
                            "url": "https://example.com/api/items",
                            "status": 200,
                            "json": {"data": {"name": "Network value"}},
                        }
                    ]
                },
            )
            record = HTMLProcessor(config).process(result).records[0]
            self.assertEqual(record.data["heading"], "XPath title")
            self.assertEqual(record.data["api_name"], "Network value")
            self.assertEqual(
                record.evidence["api_name"]["response_url"],
                "https://example.com/api/items",
            )


if __name__ == "__main__":
    unittest.main()
