import importlib.util
import tempfile
import unittest
from pathlib import Path

from omnicrawler.pdfx.normalization import normalize_amount, normalize_date


class _FakeDB:
    """text_export 只依赖 fetchall；返回可控 documents/pages 数据。"""

    def __init__(self, documents, pages_by_doc):
        self._documents = documents
        self._pages = pages_by_doc

    def fetchall(self, sql, params=()):
        if sql.lstrip().upper().startswith("SELECT D.DOC_ID"):
            return self._documents
        return self._pages.get(params[0] if params else None, [])


class PDFModuleTest(unittest.TestCase):
    def test_normalization(self):
        self.assertEqual(normalize_amount("人民币1.5亿元", "元"), ("150000000", "元"))
        self.assertEqual(normalize_date("2024年7月16日"), ("2024-07-16", None))

    def test_text_manifest_escapes_formula_injection(self):
        """B07-004：text_manifest.csv 的 filename/source_url 必须以 excel_safe 转义。"""
        from omnicrawler.pdfx.config import load_config
        from omnicrawler.pdfx.text_export import export_text_stage

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "output").mkdir(parents=True)
            config_path = root / "fields.yaml"
            config_path.write_text(
                "project_name: test\ninput_dir: data/pdfs\nwork_dir: work\n"
                "output_dir: output\ndatabase: work/pipeline.sqlite3\n"
                "parser: {workers: 1}\nocr: {backend: none}\nllm: {provider: disabled}\n"
                "fields:\n  - {name: title, label: 标题, source: content, required: true}\n",
                encoding="utf-8",
            )
            config = load_config(config_path)
            docs = [
                {"doc_id": "d1", "filename": "=cmd|' /C calc'!A0", "primary_path": "data/x.pdf",
                 "source_url": "https://example.org/a", "status": "parsed"},
            ]
            pages = {"d1": [{"page_no": 1, "final_text": "hi", "parse_method": "native",
                             "needs_ocr": 0, "ocr_status": "none", "ocr_confidence": None,
                             "printable_chars": 2, "garbled_ratio": 0.0}]}
            export_text_stage(config, _FakeDB(docs, pages))
            manifest = (config.output_dir / "text_manifest.csv").read_text(encoding="utf-8-sig")
            assert "'=cmd|' /C calc'!A0" in manifest

    @unittest.skipUnless(importlib.util.find_spec("pdfplumber") and importlib.util.find_spec("reportlab") and importlib.util.find_spec("openpyxl"), "PDF依赖未安装")
    def test_text_pdf_end_to_end(self):
        from omnicrawler.pdfx.config import load_config
        from omnicrawler.pdfx.database import Database
        from omnicrawler.pdfx.exporter import export_stage
        from omnicrawler.pdfx.extraction import extraction_stage
        from omnicrawler.pdfx.ingest import ingest
        from omnicrawler.pdfx.parser import parse_stage

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
            # Phase 0：fitz → reportlab（CJK 用内置 CID 字体）
            from reportlab.lib.pagesizes import A4
            from reportlab.pdfbase import pdfmetrics
            from reportlab.pdfbase.cidfonts import UnicodeCIDFont
            from reportlab.pdfgen import canvas

            pdf = root / "data" / "pdfs" / "notice.pdf"
            pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
            c = canvas.Canvas(str(pdf), pagesize=A4)
            c.setFont("STSong-Light", 12)
            c.drawString(72, A4[1] - 72, "担保金额：1.5亿元")
            c.showPage()
            c.save()
            config = load_config(config_path)
            with Database(config.database) as database:
                self.assertEqual(ingest(config, database)["new"], 1)
                self.assertEqual(parse_stage(config, database)["parsed"], 1)
                self.assertEqual(extraction_stage(config, database)["records"], 1)
                self.assertEqual(database.fetchone("SELECT normalized_value FROM field_values WHERE field_name='amount'")["normalized_value"], "150000000")
                self.assertEqual(export_stage(config, database)["records"], 1)


if __name__ == "__main__":
    unittest.main()

