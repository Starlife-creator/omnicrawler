import importlib.util
import tempfile
import unittest
from pathlib import Path

from omnicrawl.pdfx.normalization import normalize_amount, normalize_date


class PDFModuleTest(unittest.TestCase):
    def test_normalization(self):
        self.assertEqual(normalize_amount("人民币1.5亿元", "元"), ("150000000", "元"))
        self.assertEqual(normalize_date("2024年7月16日"), ("2024-07-16", None))

    @unittest.skipUnless(importlib.util.find_spec("fitz") and importlib.util.find_spec("openpyxl"), "PDF依赖未安装")
    def test_text_pdf_end_to_end(self):
        import fitz

        from omnicrawl.pdfx.config import load_config
        from omnicrawl.pdfx.database import Database
        from omnicrawl.pdfx.exporter import export_stage
        from omnicrawl.pdfx.extraction import extraction_stage
        from omnicrawl.pdfx.ingest import ingest
        from omnicrawl.pdfx.parser import parse_stage

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "data" / "pdfs").mkdir(parents=True)
            config_path = root / "fields.yaml"
            config_path.write_text(
                r"""
project_name: test
input_dir: data/pdfs
work_dir: work
output_dir: output
database: work/pipeline.sqlite3
parser: {workers: 1, min_native_chars: 5, max_garbled_ratio: 0.1}
ocr: {backend: none}
retrieval: {top_pages: 2, neighbor_pages: 0, min_score: 1, fallback_pages: [1]}
llm: {provider: disabled}
extraction: {workers: 1, max_chars_per_page: 10000}
normalization: {}
validation: {auto_accept_confidence: 0.9, required_together: []}
fields:
  - {name: amount, label: 担保金额, type: amount, source: content, required: true, target_unit: 元, aliases: [担保金额], patterns: ['担保金额\s*[：:]\s*(?P<value>[\d.]+亿元)']}
""",
                encoding="utf-8",
            )
            pdf = root / "data" / "pdfs" / "notice.pdf"
            document = fitz.open()
            page = document.new_page()
            page.insert_text((72, 72), "担保金额：1.5亿元", fontname="china-s", fontsize=12)
            document.save(pdf)
            document.close()
            config = load_config(config_path)
            with Database(config.database) as database:
                self.assertEqual(ingest(config, database)["new"], 1)
                self.assertEqual(parse_stage(config, database)["parsed"], 1)
                self.assertEqual(extraction_stage(config, database)["records"], 1)
                self.assertEqual(database.fetchone("SELECT normalized_value FROM field_values WHERE field_name='amount'")["normalized_value"], "150000000")
                self.assertEqual(export_stage(config, database)["records"], 1)


if __name__ == "__main__":
    unittest.main()

