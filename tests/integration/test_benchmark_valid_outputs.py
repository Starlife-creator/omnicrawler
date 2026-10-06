"""Local HTTP, actual quality checks, durable metrics and three real export formats."""
import csv
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import yaml
from openpyxl import load_workbook

from omnicrawler.services.benchmarking import BenchmarkProfile, BenchmarkRunner


def test_benchmark_counts_valid_outputs_and_preserves_zero_false_across_exports(tmp_path):
    truth = [{"id": 1, "price": 0, "in_stock": False}, {"id": 2, "price": 4, "in_stock": True},
             {"id": 3, "price": True, "in_stock": False}, {"id": 4, "in_stock": False},
             {"id": 2, "price": 4, "in_stock": True}]
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            body = b"User-agent: *\nAllow: /\n" if self.path == "/robots.txt" else json.dumps({"items": truth}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/plain" if self.path == "/robots.txt" else "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        def log_message(self, *_):
            pass
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    raw = {
        "project": {"name": "valid-output", "workspace": str(tmp_path / "work")},
        "source": {"kind": "rest", "seeds": [f"http://127.0.0.1:{server.server_port}/items"]},
        "http": {"allow_private_network": True, "respect_robots": True, "retries": 0},
        "crawl": {"allow_domains": ["127.0.0.1"]},
        "extract": {"mode": "json", "item_path": "$.items[*]", "unique_by": ["id"], "fields": {
            "id": {"path": "id", "type": "integer", "required": True, "strict_json": True},
            "price": {"path": "price", "type": "number", "required": True, "strict_json": True},
            "in_stock": {"path": "in_stock", "type": "boolean", "required": True, "strict_json": True},
        }},
        "outputs": {"jsonl": True, "csv": True, "xlsx": True},
    }
    path = tmp_path / "task.yaml"
    path.write_text(yaml.safe_dump(raw), encoding="utf8")
    try:
        runner = BenchmarkRunner(profiles={"fixture": BenchmarkProfile("fixture", 1, 0, 10, 1)})
        result = runner.run("fixture", config_path=path)
        assert result.status == "succeeded" and result.input_sha256
        counts = dict(result.output_metrics)
        assert counts == {"records": 5, "assessed": 5, "valid": 2, "review": 3, "unassessed": 0,
                          "invalid": 2, "duplicates": 1, "anomalies": 0}
        assert result.valid_records_per_second == 2 / result.duration_seconds
        assert result.time_to_first_valid_record_seconds is not None
        assert result.time_to_first_valid_record_seconds >= result.time_to_first_record_seconds
        output = tmp_path / "work" / "output"
        rows = [json.loads(line) for line in (output / "records.jsonl").read_text(encoding="utf8").splitlines()]
        first = rows[0]["data"]
        assert first["price"] == 0 and first["in_stock"] is False
        with (output / "records.csv").open(encoding="utf-8-sig", newline="") as stream:
            csv_rows = list(csv.DictReader(stream))
        assert csv_rows[0]["price"] == "0" and csv_rows[0]["in_stock"].casefold() == "false"
        with (output / "record_quality.csv").open(encoding="utf-8-sig", newline="") as stream:
            quality_csv = {row["record_id"]: row for row in csv.DictReader(stream)}
        assert len(quality_csv) == len(rows)
        for row in rows:
            assessed = row["evidence"]["_quality"]
            exported = quality_csv[row["record_id"]]
            assert exported["status"] == ("review_required" if assessed["review_required"] else "valid")
            assert json.loads(exported["validation_errors"]) == assessed["validation_errors"]
            assert json.loads(exported["missing_required"]) == assessed["missing_required"]
        book = load_workbook(output / "extraction_results.xlsx", read_only=True, data_only=True)
        try:
            table = list(book.active.values)
            xlsx_rows = [dict(zip(table[0], row, strict=True)) for row in table[1:]]
            assert xlsx_rows[0]["price"] == 0 and xlsx_rows[0]["in_stock"] is False
            status_table = list(book["质量与复核"].values)
            quality_xlsx = {row[0]: dict(zip(status_table[0], row, strict=True)) for row in status_table[1:]}
            assert set(quality_xlsx) == set(quality_csv)
            for record_id, item in quality_xlsx.items():
                assert item["status"] == quality_csv[record_id]["status"]
                assert item["review_required"] == (quality_csv[record_id]["review_required"] == "True")
                assert item["validation_errors"] == quality_csv[record_id]["validation_errors"]

        finally:
            book.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
