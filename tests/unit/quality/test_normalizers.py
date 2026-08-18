"""AutoDataCleaner 值清洗（quality/normalizers.py）单元测试。

覆盖：列类型推断、无损性硬约束（前导零/歧义日期不猜）、幂等、L1/L2 开关、
证据链、显式类型覆盖、混合列降级策略。
"""

from __future__ import annotations

import json

from omnicrawler.core.models import ExtractedRecord
from omnicrawler.quality.normalizers import (
    NormalizePolicy,
    infer_column_type,
    normalize_cell,
    normalize_records,
)


def _records(*datasets: dict[str, object]) -> list[ExtractedRecord]:
    return [
        ExtractedRecord(source_url=f"https://example.com/{index}", record_type="item", data=dict(data))
        for index, data in enumerate(datasets)
    ]


# ── 类型推断 ────────────────────────────────────────────────


def test_infer_integer_column() -> None:
    profile = infer_column_type(["12", "34", "56"])
    assert profile.kind == "integer"
    assert profile.uniform is True
    assert profile.confidence == 1.0


def test_infer_money_column() -> None:
    profile = infer_column_type(["¥1,299.00", "¥20.50", "¥3,000"])
    assert profile.kind == "money"
    assert profile.uniform is True


def test_infer_mixed_column_not_uniform() -> None:
    profile = infer_column_type(["¥1,299.00", "not a number", "free"])
    assert profile.uniform is False
    assert profile.kind in {"money", "text"}


def test_infer_empty_column_is_text() -> None:
    profile = infer_column_type([])
    assert profile.kind == "text" and profile.uniform is True


# ── L1：无损性硬约束 ────────────────────────────────────────


def test_integer_leading_zero_skipped() -> None:
    """前导零（编码语义）不猜——保持原样。"""
    cell = normalize_cell("00123", "integer", NormalizePolicy(), require_uniform=True)
    assert cell.changed is False and cell.value == "00123"
    assert cell.skipped is True


def test_integer_trim_and_fullwidth() -> None:
    assert normalize_cell("  123  ", "integer", NormalizePolicy(), require_uniform=True).value == "123"
    # 全角数字归位
    assert normalize_cell("１２３", "integer", NormalizePolicy(), require_uniform=True).value == "123"


def test_integer_already_canonical_unchanged() -> None:
    cell = normalize_cell("123", "integer", NormalizePolicy(), require_uniform=True)
    assert cell.changed is False and cell.value == "123"


def test_float_trailing_zero_trimmed() -> None:
    assert normalize_cell("1.50", "float", NormalizePolicy(), require_uniform=True).value == "1.5"
    assert normalize_cell("1299.00", "float", NormalizePolicy(), require_uniform=True).value == "1299"


def test_l1_coercion_requires_uniform() -> None:
    """混合列：L1 类型强转停用，整列保持原样（含可解析的单元格）。"""
    cell = normalize_cell("123", "integer", NormalizePolicy(), require_uniform=False)
    assert cell.changed is False and cell.value == "123"


# ── L2：格式统一规则（单元格级安全，不要求列 uniform）────────────


def test_money_symbol_and_wan() -> None:
    assert normalize_cell("¥1,299.00", "money", NormalizePolicy(), require_uniform=False).value == "1299"
    assert normalize_cell("12.5万元", "money", NormalizePolicy(), require_uniform=False).value == "125000"
    assert normalize_cell("$20.5", "money", NormalizePolicy(), require_uniform=False).value == "20.5"


def test_money_bad_grouping_rejected() -> None:
    cell = normalize_cell("12,99", "money", NormalizePolicy(), require_uniform=False)
    assert cell.changed is False and cell.skipped is True


def test_percent_fullwidth() -> None:
    assert normalize_cell("12.5％", "percent", NormalizePolicy(), require_uniform=False).value == "12.5%"
    assert normalize_cell("12.5 %", "percent", NormalizePolicy(), require_uniform=False).value == "12.5%"


def test_date_iso_and_ambiguous() -> None:
    assert normalize_cell("2026/8/13", "date", NormalizePolicy(), require_uniform=False).value == "2026-08-13"
    assert normalize_cell("13.8.2026", "date", NormalizePolicy(), require_uniform=False).value == "2026-08-13"
    assert normalize_cell("2026年8月13日", "date", NormalizePolicy(), require_uniform=False).value == "2026-08-13"
    # 月/日歧义（M/D 与 D/M 无法区分）→ 不猜
    ambiguous = normalize_cell("8/9/2026", "date", NormalizePolicy(), require_uniform=False)
    assert ambiguous.changed is False and ambiguous.skipped is True


def test_url_tracking_stripped() -> None:
    value = "https://a.com/x?id=1&utm_source=news&fbclid=abc"
    cell = normalize_cell(value, "url", NormalizePolicy(), require_uniform=False)
    assert cell.changed is True
    assert "utm_source" not in cell.value and "fbclid" not in cell.value
    assert "id=1" in cell.value


# ── 记录级入口 + 证据链 ──────────────────────────────────────


def test_normalize_records_changes_values_and_evidence() -> None:
    records = _records(
        {"title": "  标题  ", "price": "¥1,299.00"},
        {"title": "other", "price": "¥20.50"},
    )
    fields = {"title": {"selector": "h1"}, "price": {"selector": ".price"}}
    report = normalize_records(records, fields=fields)
    assert report.total_changed >= 2
    assert records[0].data["title"] == "标题"
    assert records[0].data["price"] == "1299"
    norm_evidence = records[0].evidence["_normalization"]
    assert norm_evidence["price"]["original"] == "¥1,299.00"
    assert "金额" in norm_evidence["price"]["rule"]


def test_normalize_records_idempotent() -> None:
    records = _records({"price": "¥1,299.00"})
    fields = {"price": {"selector": ".price"}}
    normalize_records(records, fields=fields)
    first = records[0].data["price"]
    normalize_records(records, fields=fields)
    assert records[0].data["price"] == first
    assert len(records[0].evidence["_normalization"]) == 1


def test_policy_off_no_change() -> None:
    records = _records({"price": "¥1,299.00"})
    report = normalize_records(
        records, fields={"price": {"selector": ".price"}},
        policy=NormalizePolicy(l1_enabled=False, l2_enabled=False),
    )
    assert report.total_changed == 0
    assert records[0].data["price"] == "¥1,299.00"


def test_explicit_type_override() -> None:
    """列被推断为 text 时，显式 types 覆盖强制按 money 清洗。"""
    records = _records({"price": "1,299.00元"}, {"price": "20元"})
    policy = NormalizePolicy(types={"price": "money"})
    report = normalize_records(records, fields=None, policy=policy)
    assert report.total_changed == 2
    assert records[0].data["price"] == "1299"
    assert records[1].data["price"] == "20"


def test_mixed_column_l2_still_applies() -> None:
    """混合列：L1 停用，但 L2 按单元格安全归一。"""
    records = _records({"price": "¥1,299.00"}, {"price": "free"})
    fields = {"price": {"selector": ".price"}}
    report = normalize_records(records, fields=fields)
    assert records[0].data["price"] == "1299"  # L2 命中
    assert records[1].data["price"] == "free"  # 非金额原样
    stats = next(f for f in report.fields if f.name == "price")
    assert stats.uniform is False and stats.kind == "money"


def test_none_and_empty_untouched() -> None:
    records = _records({"price": None}, {"price": ""})
    normalize_records(records, fields={"price": {"selector": ".price"}})
    assert records[0].data["price"] is None
    assert records[1].data["price"] == ""


# ── 端到端：质量阶段钩子（pipeline _handle_result → normalize）───────────


def test_pipeline_normalizes_extracted_values(tmp_path) -> None:  # noqa: ANN001
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    import yaml

    from omnicrawler.core.config import load_config
    from omnicrawler.pipeline import Pipeline

    html = "<html><body><span class='price'>¥1,299.00</span></body></html>".encode()

    class _Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(html)))
            self.end_headers()
            self.wfile.write(html)

        def log_message(self, format, *args):  # noqa: N802
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{server.server_port}/"
        config_path = tmp_path / "project.yaml"
        config_path.write_text(yaml.safe_dump({
            "project": {"name": "norm", "workspace": str(tmp_path / "work")},
            "source": {"kind": "static_html", "seeds": [url]},
            "http": {"respect_robots": False, "allow_private_network": True, "delay_seconds": 0},
            "extract": {"mode": "html", "fields": {"price": {"selector": ".price"}}},
        }, sort_keys=False), encoding="utf-8")
        config = load_config(config_path)
        with Pipeline(config) as pipeline:
            summary = pipeline.run()
            rows = pipeline.state.rows(
                "SELECT data_json FROM records WHERE run_id=?", (summary["run_id"],)
            )
        assert rows, "应产生记录"
        assert json.loads(rows[0]["data_json"])["price"] == "1299"
    finally:
        server.shutdown()
        server.server_close()


def test_pipeline_normalize_disabled_keeps_raw(tmp_path) -> None:  # noqa: ANN001
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    import yaml

    from omnicrawler.core.config import load_config
    from omnicrawler.pipeline import Pipeline

    html = "<html><body><span class='price'>¥1,299.00</span></body></html>".encode()

    class _Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(html)))
            self.end_headers()
            self.wfile.write(html)

        def log_message(self, format, *args):  # noqa: N802
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{server.server_port}/"
        config_path = tmp_path / "project.yaml"
        config_path.write_text(yaml.safe_dump({
            "project": {"name": "norm_off", "workspace": str(tmp_path / "work")},
            "source": {"kind": "static_html", "seeds": [url]},
            "http": {"respect_robots": False, "allow_private_network": True, "delay_seconds": 0},
            "extract": {"mode": "html", "fields": {"price": {"selector": ".price"}}},
            "quality": {"normalize": {"enabled": False}},
        }, sort_keys=False), encoding="utf-8")
        config = load_config(config_path)
        with Pipeline(config) as pipeline:
            summary = pipeline.run()
            rows = pipeline.state.rows(
                "SELECT data_json FROM records WHERE run_id=?", (summary["run_id"],)
            )
        assert json.loads(rows[0]["data_json"])["price"] == "¥1,299.00"
    finally:
        server.shutdown()
        server.server_close()


# ── 公开值级包装（H4：AST 求值器 / transform 表达式）──────────────


def test_public_parse_money() -> None:
    from omnicrawler.quality.normalizers import parse_money

    assert parse_money("¥1,299 元") == "1299"
    assert parse_money("12.5万") == "125000"
    assert parse_money("不是金额") == "不是金额"
    assert parse_money(123) == 123  # 非字符串原样返回


def test_public_parse_time() -> None:
    from omnicrawler.quality.normalizers import parse_time

    assert parse_time("2026年8月13日") == "2026-08-13"
    assert parse_time("n/a") == "n/a"
    assert parse_time("13/08/2026") == "2026-08-13"


def test_public_parse_number() -> None:
    from omnicrawler.quality.normalizers import parse_number

    assert parse_number("42") == "42"
    assert parse_number("1.50") == "1.5"
    assert parse_number("00123") == "00123"  # 前导零不猜
    assert parse_number("abc") == "abc"


def test_public_trim_and_clean_html() -> None:
    from omnicrawler.quality.normalizers import clean_html, trim

    assert trim("  x \n") == "x"
    assert trim(123) == 123
    assert clean_html("<p>a &amp; b</p>") == "a & b"
    assert clean_html("") == ""


def test_public_regex_extract() -> None:
    from omnicrawler.quality.normalizers import regex_extract

    assert regex_extract("abc-123", r"-(\d+)") == "123"
    assert regex_extract("abc", r"-(\d+)") == "abc"  # 未命中返回原值
    assert regex_extract("abc", "(a+)+") == "abc"  # 嵌套量词被安全闸拒绝 → 原值


def test_public_coalesce_and_concat() -> None:
    from omnicrawler.quality.normalizers import coalesce, concat

    assert coalesce(None, "", "  ", "x") == "x"
    assert coalesce(None, "") is None
    assert concat("a", None, "b", sep="-") == "a-b"
    assert concat(1, 2, sep="") == "12"
